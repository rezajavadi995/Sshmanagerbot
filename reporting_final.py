# /root/reporting_final.py
cat > /root/reporting_final.py << 'EOF'
# -*- coding: utf-8 -*-
import os, json, subprocess
from datetime import datetime
from typing import List, Dict, Tuple

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler

LIMITS_DIR = "/etc/sshmanager/limits"
PROGRESS_BAR_WIDTH = 20
PAGE_SIZE = 10
CHAIN_NAME = "SSH_USERS"
USE_SUDO = True
LOG_USER_TRAFFIC = "/usr/local/bin/log_user_traffic.py"

def safe_int(v, default=0) -> int:
    try: return int(v)
    except Exception:
        try: return int(float(v))
        except Exception: return default

def percent_used_kb(used_kb: int, limit_kb: int) -> float:
    return (float(used_kb) / float(limit_kb) * 100.0) if limit_kb and limit_kb > 0 else 0.0

def kb_to_human(kb: int) -> str:
    kb = safe_int(kb, 0)
    if kb >= 1024 * 1024: return f"{kb / (1024*1024):.2f} GB"
    if kb >= 1024: return f"{kb / 1024:.1f} MB"
    return f"{kb} KB"

def make_progress_bar(pct: float, width: int = PROGRESS_BAR_WIDTH) -> str:
    pct = max(0.0, min(100.0, pct))
    filled = int(round((pct / 100.0) * width))
    empty = width - filled
    return "▮" * filled + "▯" * empty

def run_cmd(cmd: List[str]) -> Tuple[int, str, str]:
    try:
        p = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception as e:
        return 999, "", f"EXC: {e}"

def _sudo_prefix() -> List[str]:
    return ["sudo"] if USE_SUDO else []

def user_limits_path(username: str) -> str:
    return os.path.join(LIMITS_DIR, f"{username}.json")

def load_user_json(username: str) -> Dict:
    path = user_limits_path(username)
    if not os.path.exists(path): return {}
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

def list_all_users() -> List[str]:
    if not os.path.isdir(LIMITS_DIR): return []
    users = [fn[:-5] for fn in os.listdir(LIMITS_DIR) if fn.endswith(".json")]
    users.sort()
    return users

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
    try:
        if isinstance(exp_ts, (int, float)) and int(exp_ts) > 0:
            exp_str = datetime.fromtimestamp(int(exp_ts)).strftime("%Y-%m-%d")
    except Exception:
        pass

    status = "🔒" if is_block else "✅"
    if is_block and block_reason:
        status += f" ({block_reason})"

    text = (
        f"👤 `{username}` {status}\n"
        f"📊 مصرف: {usage}\n"
        f"▒{bar}▒\n"
        f"⏳ انقضا: {exp_str}\n"
    )
    return text

def gather_users(view: str) -> List[str]:
    users = list_all_users()
    if view == "vol":
        return [u for u in users if safe_int(load_user_json(u).get("limit", 0)) > 0]
    return users

def build_report_page(view: str, page: int) -> Tuple[str, InlineKeyboardMarkup]:
    users = gather_users(view)
    total = len(users)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start, end = page * PAGE_SIZE, min(page * PAGE_SIZE + PAGE_SIZE, total)
    chunk = users[start:end]

    if not chunk:
        body = "➖ هیچ کاربری برای نمایش وجود ندارد."
    else:
        body = "\n".join([format_user_line(u) for u in chunk])

    title = "📄 گزارش همهٔ کاربران" if view == "all" else "📄 گزارش کاربران حجمی"
    header = f"{title}\nصفحه {page+1} از {total_pages} | تعداد: {total}\n\n"

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

def _force_update_usage():
    # قبل از هر نمایش گزارش، یکبار منبع اصلی محاسبه حجم را اجرا می‌کنیم
    run_cmd(["/usr/bin/python3", LOG_USER_TRAFFIC])

async def report_users_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _force_update_usage()
    text, kb = build_report_page("all", 0)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=kb, parse_mode="Markdown")

async def report_volume_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _force_update_usage()
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
    try:
        _, view, tail = data.split(":", 3)
    except Exception:
        return
    if view not in ("all", "vol"): return

    if tail.startswith("refresh="):
        _force_update_usage()
        try: page = int(tail.split("=", 1)[1])
        except Exception: page = 0
        text, kb = build_report_page(view, page)
        await q.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        return

    if tail.startswith("page="):
        try: page = int(tail.split("=", 1)[1])
        except Exception: page = 0
        text, kb = build_report_page(view, page)
        await q.edit_message_text(text, reply_markup=kb, parse_mode="Markdown")
        return

def register_reporting_handlers(application):
    application.add_handler(CommandHandler("report_users", report_users_entry))
    application.add_handler(CommandHandler("report_volume", report_volume_entry))
    application.add_handler(CallbackQueryHandler(report_pagination_cb, pattern=r"^rep:(all|vol):(page|refresh)="))
EOF
