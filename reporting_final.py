cat > /root/reporting_final.py << 'EOF'
# -*- coding: utf-8 -*-
"""
reporting_final.py
گزارش کاربران و کاربران حجمی با صفحه‌بندی + نوار پیشرفت گرافیکی
سازگار با python-telegram-bot v20+

نکات:
- LIMITS_DIR را با ساختار فعلی‌ات تنظیم کرده‌ام.
- پیش از ساخت گزارش، مصرف زنده با update_live_usage() آپدیت می‌شود؛
  اگر در کدت چنین تابعی نباشد، fallback داخلی از iptables-save -c می‌خواند.
- دکمه‌ها:
  • گزارش همه کاربران:     /report_users
  • گزارش کاربران حجمی:     /report_volume
  • دکمه‌های inline: Prev/Next/Refresh/Switch
"""

import os
import json
import subprocess
from datetime import datetime
from typing import List, Dict, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
)

# ====== تنظیمات عمومی ======
LIMITS_DIR = "/etc/sshmanager/limits"
PROGRESS_BAR_WIDTH = 20
PAGE_SIZE = 10
CHAIN_NAME = "SSH_USERS"  # برای fallback
USE_SUDO = True           # اگر دسترسی روت نداری و در سیستم از sudo استفاده می‌کنی، True بماند

# ====== ابزارهای واحد/ایمن ======
def safe_int(v, default=0) -> int:
    try:
        return int(v)
    except Exception:
        try:
            return int(float(v))
        except Exception:
            return default

def percent_used_kb(used_kb: int, limit_kb: int) -> float:
    if not limit_kb or limit_kb <= 0:
        return 0.0
    try:
        return (float(used_kb) / float(limit_kb)) * 100.0
    except Exception:
        return 0.0

def kb_to_human(kb: int) -> str:
    try:
        kb = int(kb)
    except Exception:
        kb = 0
    if kb >= 1024 * 1024:
        return f"{kb / (1024*1024):.2f} GB"
    if kb >= 1024:
        return f"{kb / 1024:.1f} MB"
    return f"{kb} KB"

def make_progress_bar(pct: float, width: int = PROGRESS_BAR_WIDTH) -> str:
    pct = max(0.0, min(100.0, pct))
    filled = int(round((pct / 100.0) * width))
    empty = width - filled
    return "▮" * filled + "▯" * empty  # بلوک‌های پر/خالی

def run_cmd(cmd: List[str]) -> Tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception as e:
        return 999, "", f"EXC: {e}"

def _sudo_prefix() -> List[str]:
    return ["sudo"] if USE_SUDO else []

# ====== خواندن/نوشتن JSON کاربر ======
def user_limits_path(username: str) -> str:
    return os.path.join(LIMITS_DIR, f"{username}.json")

def load_user_json(username: str) -> Dict:
    path = user_limits_path(username)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f) or {}
    except Exception:
        return {}

def save_user_json(username: str, data: Dict) -> None:
    path = user_limits_path(username)
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
    except Exception:
        pass

# ====== لیست کاربران از روی پوشه limits ======
def list_all_users() -> List[str]:
    if not os.path.isdir(LIMITS_DIR):
        return []
    users = []
    for fn in os.listdir(LIMITS_DIR):
        if fn.endswith(".json"):
            users.append(fn[:-5])
    users.sort()
    return users

# ====== Fallback: بروزرسانی مصرف از iptables-save -c ======
def _uid_to_username(uid: int) -> str:
    rc, out, _ = run_cmd(["getent", "passwd", str(uid)])
    if rc == 0 and out:
        parts = out.split(":")
        if len(parts) >= 1:
            return parts[0]
    return ""

def refresh_usage_from_iptables():
    """
    اگر update_live_usage() در کد اصلی موجود نبود، از این استفاده می‌کنیم.
    از iptables-save -c شمارنده‌ها را می‌خوانیم و used را در JSON می‌نویسیم.
    """
    cmd = _sudo_prefix() + ["iptables-save", "-c"]
    rc, out, err = run_cmd(cmd)
    if rc != 0 or not out:
        return  # دسترسی یا خروجی نداریم؛ رها می‌کنیم

    # نمونه خط: [123:456789] -A SSH_USERS -m owner --uid-owner 1001 -j ACCEPT
    lines = [ln for ln in out.splitlines() if f"-A {CHAIN_NAME} " in ln and "--uid-owner" in ln]
    for ln in lines:
        try:
            # استخراج bytes از [pkts:bytes]
            lb = ln.find('[')
            rb = ln.find(']')
            bytes_count = 0
            if lb != -1 and rb != -1 and rb > lb:
                counters = ln[lb+1:rb]
                parts = counters.split(":")
                if len(parts) == 2:
                    bytes_count = safe_int(parts[1], 0)

            # استخراج UID
            uid = None
            TOK = "--uid-owner"
            if TOK in ln:
                after = ln.split(TOK, 1)[1].strip()
                uid_str = after.split()[0]
                uid = safe_int(uid_str, None)

            if uid is None:
                continue
            username = _uid_to_username(uid)
            if not username:
                continue

            # به KB تبدیل کنیم (round down)
            used_kb = int(bytes_count / 1024)
            j = load_user_json(username)
            prev_used = safe_int(j.get("used", 0), 0)
            # فقط اگر تغییر معنادار است ذخیره کنیم
            if used_kb >= prev_used:
                j["used"] = used_kb
                save_user_json(username, j)

        except Exception:
            continue

def update_live_usage_safe():
    """
    اگر در کد اصلی‌ات update_live_usage() موجود بود از خودش استفاده می‌کنیم،
    در غیر این صورت fallback داخلی را اجرا می‌کنیم.
    """
    fn = globals().get("update_live_usage")
    if callable(fn):
        try:
            fn()
            return
        except Exception:
            pass
    # fallback
    refresh_usage_from_iptables()

# ====== قالب‌بندی هر کاربر ======
def format_user_line(username: str) -> str:
    j = load_user_json(username)
    limit_kb = safe_int(j.get("limit", 0))
    used_kb  = safe_int(j.get("used", 0))
    exp_ts   = j.get("expire_timestamp")
    is_block = bool(j.get("is_blocked", False))
    block_reason = j.get("block_reason")

    if limit_kb > 0:
        pct = percent_used_kb(used_kb, limit_kb)
        bar = make_progress_bar(pct)
        usage = f"{kb_to_human(used_kb)} / {kb_to_human(limit_kb)} ({pct:.1f}%)"
    else:
        pct = 0.0
        bar = "—" * PROGRESS_BAR_WIDTH
        usage = "♾ نامحدود"

    exp_str = "—"
    if isinstance(exp_ts, (int, float)) and int(exp_ts) > 0:
        exp_str = datetime.fromtimestamp(int(exp_ts)).strftime("%Y-%m-%d")

    status = "🔒" if is_block else "✅"
    if is_block and block_reason:
        status += f" ({block_reason})"

    # قالب نهایی خط
    text = (
        f"👤 `{username}` {status}\n"
        f"📊 مصرف: {usage}\n"
        f"▒{bar}▒\n"
        f"⏳ انقضا: {exp_str}\n"
    )
    return text

# ====== ساخت صفحه گزارش ======
def gather_users(view: str) -> List[str]:
    """
    view: 'all' یا 'vol'
    """
    users = list_all_users()
    if view == "vol":
        # فقط کاربرانی که limit > 0 دارند
        vol_users = []
        for u in users:
            j = load_user_json(u)
            if safe_int(j.get("limit", 0)) > 0:
                vol_users.append(u)
        return vol_users
    return users

def build_report_page(view: str, page: int) -> Tuple[str, InlineKeyboardMarkup]:
    users = gather_users(view)
    total = len(users)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)
    chunk = users[start:end]

    if not chunk:
        body = "➖ هیچ کاربری برای نمایش وجود ندارد."
    else:
        body_lines = [format_user_line(u) for u in chunk]
        body = "\n".join(body_lines)

    # عنوان
    title = "📄 گزارش همهٔ کاربران" if view == "all" else "📄 گزارش کاربران حجمی"
    header = f"{title}\nصفحه {page+1} از {total_pages} | تعداد: {total}\n\n"

    # دکمه‌ها
    prev_page = max(0, page - 1)
    next_page = min(total_pages - 1, page + 1)

    kb = [
        [
            InlineKeyboardButton("⬅️ قبلی", callback_data=f"rep:{view}:page={prev_page}"),
            InlineKeyboardButton("➡️ بعدی", callback_data=f"rep:{view}:page={next_page}"),
        ],
        [
            InlineKeyboardButton("🔄 بروزرسانی", callback_data=f"rep:{view}:refresh={page}"),
            InlineKeyboardButton(("👥 همه" if view != "all" else "📶 حجمی"), callback_data=f"rep:{'all' if view!='all' else 'vol'}:page=0"),
        ]
    ]
    return header + body, InlineKeyboardMarkup(kb)

# ====== هندلرها ======
async def report_users_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # بروزرسانی مصرف زنده
    update_live_usage_safe()
    text, kb = build_report_page("all", 0)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")

async def report_volume_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    update_live_usage_safe()
    text, kb = build_report_page("vol", 0)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")

async def report_pagination_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    # الگوهای: rep:all:page=N | rep:vol:page=N | rep:<view>:refresh=N
    try:
        _, view, tail = data.split(":", 2)
    except Exception:
        return
    if not (view in ("all", "vol")):
        return

    if tail.startswith("refresh="):
        # قبل از refresh مصرف را بروزرسانی کن
        update_live_usage_safe()
        try:
            page = int(tail.split("=", 1)[1])
        except Exception:
            page = 0
        text, kb = build_report_page(view, page)
        await q.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        return

    if tail.startswith("page="):
        try:
            page = int(tail.split("=", 1)[1])
        except Exception:
            page = 0
        text, kb = build_report_page(view, page)
        await q.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        return

# ====== ثبت هندلرها در اپلیکیشن ======
def register_reporting_handlers(application):
    """
    در فایل اصلی اپ، بعد از ساخت Application، این را صدا بزن:
        from reporting_final import register_reporting_handlers
        register_reporting_handlers(application)
    """
    application.add_handler(CommandHandler("report_users", report_users_entry))
    application.add_handler(CommandHandler("report_volume", report_volume_entry))
    application.add_handler(CallbackQueryHandler(report_pagination_cb, pattern=r"^rep:(all|vol):(page|refresh)="))
EOF
