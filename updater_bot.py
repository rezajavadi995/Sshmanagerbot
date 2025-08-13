#cat > /root/updater_bot.py << 'EOF'
# /root/updater_bot.py
# -*- coding: utf-8 -*-

import asyncio
import json
import os
import re
import shlex
import subprocess
import time
from datetime import datetime
from glob import glob
from pathlib import Path
from typing import Dict, Optional, Tuple

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ──────────────────────────────[ پیکربندی ]────────────────────────────────

# ⚠️ توکن ربات را اینجا بگذار
BOT_TOKEN = "7666791827:AAGeLPPlzRYb-tVke_nq6wIYtxz-fBtY9fg"

# فقط ادمین اجازهٔ کار با ربات را دارد:
ADMIN_ID = 8062924341  

# مسیر کلون ریپو که فایل‌ها از آن Pull و کپی می‌شوند:
REPO_PATH = "/root/sshmanager_repo"

# محل ذخیرهٔ پیکربندی دکمه‌های سفارشی (برای پایداری)
STATE_DIR = "/etc/updater-bot"
ITEMS_JSON = f"{STATE_DIR}/items.json"

# حد امن طول پیام تلگرام؛ اگر بیشتر شد، فایل txt آپلود می‌کنیم
TELEGRAM_SAFE_LIMIT = 3500

# نام سرویسی که «حدس» می‌زنیم برای همین ربات باشد (اگر دقیق نیست، Auto-Detect می‌شود)
GUESSED_UPDATER_SERVICE = "updater-bot.service"

# ──────────────────────────────[ آیتم‌های پیش‌فرض ]────────────────────────

# ساختار هر آیتم:
# name: {
#    "source": "<path in repo or absolute>",
#    "dest":   "<absolute dest on server>",
#    "service": "<systemd service name or None or 'auto'>"
# }
DEFAULT_ITEMS: Dict[str, Dict] = {
    "Sshmanagerbot.py": {
        "source": f"{REPO_PATH}/Sshmanagerbot.py",
        "dest": "/root/sshmanagerbot.py",
        "service": "sshmanagerbot.service",
    },
    "check_user_usage.py": {
        "source": f"{REPO_PATH}/check_user_usage.py",
        "dest": "/usr/local/bin/check_user_usage.py",
        "service": None,
    },
    "check_users_expire.py": {
        "source": f"{REPO_PATH}/check_users_expire.py",
        "dest": "/usr/local/bin/check_users_expire.py",
        "service": None,
    },
    "lock_user.py": {
        "source": f"{REPO_PATH}/lock_user.py",
        "dest": "/root/sshmanager/lock_user.py",
        "service": None,
    },
    "log_user_traffic.py": {
        "source": f"{REPO_PATH}/log_user_traffic.py",
        "dest": "/usr/local/bin/log_user_traffic.py",
        "service": None,
    },

    # فایل‌های سیستمی که گفتی:
    "log-user-traffic.service": {
        "source": f"{REPO_PATH}/log-user-traffic.service",
        "dest": "/etc/systemd/system/log-user-traffic.service",
        "service": "log-user-traffic.service",
    },
    "check-expire.service": {
        "source": f"{REPO_PATH}/check-expire.service",
        "dest": "/etc/systemd/system/check-expire.service",
        "service": "check-expire.service",
    },

    # خودِ ربات و سرویسش
    "Updater_bot.py (self)": {
        "source": f"{REPO_PATH}/Updater_bot.py",   # اسم فایل داخل گیت‌هاب طبق گفتهٔ شما
        "dest": "/root/updater_bot.py",
        "service": "auto",  # تلاش برای پیدا کردن سرویس درست
    },
}

# ──────────────────────────────[ ابزارها ]─────────────────────────────────

def ensure_state_dir():
    Path(STATE_DIR).mkdir(parents=True, exist_ok=True)

def load_items() -> Dict[str, Dict]:
    ensure_state_dir()
    data = {}
    if os.path.isfile(ITEMS_JSON):
        try:
            with open(ITEMS_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
    # merge defaults (defaults override only if not already set)
    merged = dict(DEFAULT_ITEMS)
    merged.update(data)  # اگر کاربر قبلا سفارشی کرده، همان بماند
    return merged

def save_items(items: Dict[str, Dict]):
    ensure_state_dir()
    # فقط آیتم‌های اضافه‌شدهٔ کاربر را ذخیره کنیم (پیش‌فرض‌ها را لازم نیست)
    user_items = {k: v for k, v in items.items() if k not in DEFAULT_ITEMS}
    with open(ITEMS_JSON, "w", encoding="utf-8") as f:
        json.dump(user_items, f, ensure_ascii=False, indent=2)

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

def fmt_code(s: str) -> str:
    # برای خوانایی بهتر
    if not s:
        return "—"
    # از بلاک کد تلگرام استفاده می‌کنیم
    return f"```\n{s}\n```"

def normalize_service_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    name = name.strip()
    if not name:
        return None
    if not name.endswith(".service") and not name.endswith(".timer"):
        name += ".service"
    return name

def detect_updater_service() -> Optional[str]:
    """
    اگر کاربر سرویس ربات آپدیتر را نداند، از داخل /etc/systemd/system سعی می‌کنیم حدس بزنیم.
    """
    candidates = []
    for path in glob("/etc/systemd/system/*.service"):
        base = os.path.basename(path).lower()
        if "updater" in base and "bot" in base:
            candidates.append(os.path.basename(path))
    if candidates:
        # اگر چندتا پیدا شد، اولی را می‌گیریم
        return candidates[0]
    # اگر چیزی پیدا نشد، حدس از پیش تعیین‌شده
    return GUESSED_UPDATER_SERVICE

def set_executable(path: str):
    try:
        st = os.stat(path)
        os.chmod(path, st.st_mode | 0o111)
    except Exception:
        pass

def run_cmd(cmd: str, timeout: int = 120) -> Tuple[int, str]:
    """
    دستور شِل را اجرا کرده و (exit_code, combined_output) را برمی‌گرداند.
    """
    try:
        completed = subprocess.run(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            text=True,
        )
        return completed.returncode, completed.stdout
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") + "\n\n[Timeout]"
        return 124, out
    except Exception as e:
        return 1, f"Exception: {e}"

async def reply_log(update: Update, text: str, filename_prefix: str = "log"):
    """
    اگر متن طولانی باشد، به‌صورت فایل می‌فرستد؛ وگرنه به‌صورت پیام.
    """
    if len(text) > TELEGRAM_SAFE_LIMIT:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = f"/tmp/{filename_prefix}-{ts}.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        await update.effective_message.reply_document(
            document=InputFile(path),
            caption="📄 خروجی طولانی بود؛ به‌صورت فایل ارسال شد."
        )
    else:
        await update.effective_message.reply_text(fmt_code(text), parse_mode=ParseMode.MARKDOWN_V2)

async def edit_log(query, text: str, filename_prefix: str = "log"):
    """
    مشابه reply_log اما برای ویرایش پیام دکمه.
    """
    if len(text) > TELEGRAM_SAFE_LIMIT:
        await query.edit_message_text("📄 خروجی طولانی بود؛ به‌صورت فایل جداگانه ارسال می‌شود...")
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = f"/tmp/{filename_prefix}-{ts}.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        await query.message.reply_document(
            document=InputFile(path),
            caption="📄 خروجی کامل عملیات"
        )
    else:
        await query.edit_message_text(fmt_code(text), parse_mode=ParseMode.MARKDOWN_V2)

def build_keyboard(items: Dict[str, Dict]) -> InlineKeyboardMarkup:
    buttons = []
    for name in items.keys():
        buttons.append([InlineKeyboardButton(f"⭕️ آپدیت {name}", callback_data=f"update::{name}")])
    # کنترل‌ها
    controls = [
        [InlineKeyboardButton("➕ ساخت دکمهٔ جدید", callback_data="control::add")],
        [InlineKeyboardButton("📜 لیست آیتم‌ها", callback_data="control::list")],
        [InlineKeyboardButton("🔄 Git Pull دستی", callback_data="control::pull")],
    ]
    return InlineKeyboardMarkup(buttons + controls)

# ──────────────────────────────[ هندلرهای اصلی ]───────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    items = load_items()
    await update.message.reply_text(
        "کدام فایل/سرویس را می‌خواهید آپدیت کنید؟",
        reply_markup=build_keyboard(items),
    )

async def git_pull(update_or_query, is_query=False):
    cmd = f"git -C {shlex.quote(REPO_PATH)} pull --ff-only"
    code, out = run_cmd(cmd, timeout=180)
    text = f"🏷 دستور: {cmd}\nخروجی:\n{out}\n(exit={code})"
    if is_query:
        await edit_log(update_or_query, text, "git-pull")
    else:
        await reply_log(update_or_query, text, "git-pull")

def safe_copy(src: str, dst: str) -> Tuple[int, str]:
    """
    فایل را به‌صورت امن کپی می‌کند (با ایجاد دایرکتوری‌های مقصد).
    """
    dst_dir = os.path.dirname(dst)
    Path(dst_dir).mkdir(parents=True, exist_ok=True)
    cmd = f"/bin/cp -f {shlex.quote(src)} {shlex.quote(dst)}"
    return run_cmd(cmd)

def systemd_reload_enable_restart(service: str) -> Tuple[int, str]:
    """
    systemctl daemon-reload; enable; restart
    همچنین اگر .timer متناظر دارد، آن را نیز enable/start می‌کند.
    """
    log_all = []
    # daemon-reload
    c, o = run_cmd("systemctl daemon-reload")
    log_all.append(f"$ systemctl daemon-reload\n{o}(exit={c})\n")

    # normalize service/timer
    svc = normalize_service_name(service)
    if svc:
        c, o = run_cmd(f"systemctl enable {shlex.quote(svc)}")
        log_all.append(f"$ systemctl enable {svc}\n{o}(exit={c})\n")
        c, o = run_cmd(f"systemctl restart {shlex.quote(svc)}")
        log_all.append(f"$ systemctl restart {svc}\n{o}(exit={c})\n")

        # اگر .timer دارد
        timer_name = re.sub(r"\.service$", ".timer", svc)
        if timer_name != svc:
            # اگر فایل تایمر وجود دارد، enable/start شود
            timer_path = f"/etc/systemd/system/{timer_name}"
            if os.path.exists(timer_path):
                c, o = run_cmd(f"systemctl enable {shlex.quote(timer_name)}")
                log_all.append(f"$ systemctl enable {timer_name}\n{o}(exit={c})\n")
                c, o = run_cmd(f"systemctl start {shlex.quote(timer_name)}")
                log_all.append(f"$ systemctl start {timer_name}\n{o}(exit={c})\n")
    return 0, "\n".join(log_all)

async def do_update(query, name: str):
    items = load_items()
    info = items.get(name)
    if not info:
        await query.edit_message_text("❌ آیتم موردنظر پیدا نشد.")
        return

    src = info.get("source")
    dst = info.get("dest")
    svc = info.get("service")

    logs = []
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logs.append(f"⏱ زمان: {ts}")
    logs.append(f"🔧 آیتم: {name}")
    logs.append(f"📦 منبع: {src}")
    logs.append(f"📍 مقصد: {dst}")
    logs.append(f"🧩 سرویس: {svc}")

    # 1) git pull
    c, o = run_cmd(f"git -C {shlex.quote(REPO_PATH)} pull --ff-only", timeout=180)
    logs.append(f"$ git -C {REPO_PATH} pull --ff-only\n{o}(exit={c})\n")
    if c != 0:
        await edit_log(query, "\n".join(logs), f"update-{name}")
        return

    # 2) کپی فایل
    if not os.path.exists(src):
        logs.append(f"❌ منبع وجود ندارد: {src}")
        await edit_log(query, "\n".join(logs), f"update-{name}")
        return

    c, o = safe_copy(src, dst)
    logs.append(f"$ cp -f {src} {dst}\n{o}(exit={c})\n")
    if c != 0:
        await edit_log(query, "\n".join(logs), f"update-{name}")
        return

    # 3) اگر فایل اجرایی است (py/sh)، پرمیشن اجرا بده
    if dst.endswith(".py") or dst.endswith(".sh"):
        try:
            set_executable(dst)
            logs.append(f"✅ مجوز اجرا به {dst} داده شد.")
        except Exception as e:
            logs.append(f"⚠️ خطا در chmod: {e}")

    # 4) اگر سرویس دارد → reload/enable/restart (+timer)
    effective_service = None
    if svc == "auto":
        effective_service = detect_updater_service()
        logs.append(f"🔎 سرویس تشخیص‌داده‌شده: {effective_service}")
    elif svc:
        effective_service = normalize_service_name(svc)

    if effective_service:
        c, o = systemd_reload_enable_restart(effective_service)
        logs.append(o)

    # 5) پایان
    logs.append(f"✅ عملیاتِ آپدیت «{name}» به پایان رسید.")
    await edit_log(query, "\n".join(logs), f"update-{name}")

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.edit_message_text("شما ادمین نیستید.")
        return

    data = query.data or ""
    if data.startswith("update::"):
        name = data.split("::", 1)[1]
        await query.edit_message_text(f"⏳ در حال آپدیت «{name}» ...")
        await do_update(query, name)
        return
    if data == "control::pull":
        await git_pull(query, is_query=True)
        return
    if data == "control::list":
        items = load_items()
        lines = []
        for k, v in items.items():
            lines.append(f"- {k}  | source: {v.get('source')}  | dest: {v.get('dest')}  | service: {v.get('service')}")
        text = "📜 لیست آیتم‌ها:\n" + "\n".join(lines)
        await edit_log(query, text, "items")
        return
    if data == "control::add":
        # شروع ویزارد ساخت دکمه
        await start_add_wizard(query, context)
        return

# ──────────────────────────────[ ویزارد ساخت دکمه ]────────────────────────

ADD_NAME, ADD_SOURCE, ADD_DEST, ADD_SERVICE, ADD_CONFIRM = range(5)

async def start_add_wizard(query, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["add_item"] = {}
    await query.edit_message_text(
        "🧩 ساخت دکمهٔ جدید\n\n"
        "مرحله ۱/۴ — لطفاً «نام نمایشی دکمه» را بفرست:\n"
        "مثال: myscript.py یا backup-job"
    )
    # سوییچ به حالت مکالمه
    # این تابع فقط پیام را عوض می‌کند؛ شروع مکالمه با /add انجام می‌شود
    # اما چون از دکمه آمدیم، باید انتظار پیام بعدی را داشته باشیم
    context.user_data["add_state"] = ADD_NAME

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    context.user_data["add_item"] = {}
    context.user_data["add_state"] = ADD_NAME
    await update.message.reply_text(
        "🧩 ساخت دکمهٔ جدید\n\n"
        "مرحله ۱/۴ — لطفاً «نام نمایشی دکمه» را بفرست:"
    )

async def add_message_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return

    state = context.user_data.get("add_state")
    if state is None:
        return  # خارج از ویزارد

    msg = (update.message.text or "").strip()

    # مرحله 1: نام
    if state == ADD_NAME:
        items = load_items()
        if msg in items:
            await update.message.reply_text("⚠️ آیتمی با این نام وجود دارد. یک نام دیگر بفرست.")
            return
        context.user_data["add_item"]["name"] = msg
        context.user_data["add_state"] = ADD_SOURCE
        await update.message.reply_text(
            "مرحله ۲/۴ — «مسیر منبع در ریپو» یا مسیر مطلق را بفرست:\n"
            f"مثال: {REPO_PATH}/myscript.py یا relative مثل myscript.py"
        )
        return

    # مرحله 2: منبع
    if state == ADD_SOURCE:
        src = msg
        if not src.startswith("/"):
            # اگر نسبی بود، از ریشهٔ ریپو در نظر بگیر
            src = f"{REPO_PATH.rstrip('/')}/{src}"
        context.user_data["add_item"]["source"] = src
        context.user_data["add_state"] = ADD_DEST
        await update.message.reply_text(
            "مرحله ۳/۴ — «مسیر مقصد روی سرور» را بفرست (مطلق):\n"
            "مثال: /usr/local/bin/myscript.py"
        )
        return

    # مرحله 3: مقصد
    if state == ADD_DEST:
        if not msg.startswith("/"):
            await update.message.reply_text("❌ مسیر مقصد باید مطلق باشد و با / شروع شود. دوباره بفرست.")
            return
        context.user_data["add_item"]["dest"] = msg
        context.user_data["add_state"] = ADD_SERVICE
        await update.message.reply_text(
            "مرحله ۴/۴ — «نام سرویس systemd» (اختیاری) را بفرست:\n"
            "- می‌تونی خالی بگذاری.\n"
            "- با یا بدون .service بفرست (هر دو قبوله)."
        )
        return

    # مرحله 4: سرویس
    if state == ADD_SERVICE:
        svc = msg.strip()
        if svc == "":
            svc = None
        else:
            # اگر کاربر .service ننویسد هم اوکی
            if not svc.endswith(".service") and not svc.endswith(".timer"):
                svc = svc + ".service"
        context.user_data["add_item"]["service"] = svc
        context.user_data["add_state"] = ADD_CONFIRM

        item = context.user_data["add_item"]
        preview = (
            f"نام: {item['name']}\n"
            f"منبع: {item['source']}\n"
            f"مقصد: {item['dest']}\n"
            f"سرویس: {item['service']}\n\n"
            "برای تایید «yes» و برای لغو «no» را بفرست."
        )
        await update.message.reply_text(preview)
        return

    # تایید نهایی
    if state == ADD_CONFIRM:
        if msg.lower() not in ("y", "yes", "بله", "ok", "تایید"):
            await update.message.reply_text("عملیات لغو شد.")
            context.user_data.pop("add_state", None)
            context.user_data.pop("add_item", None)
            return

        item = context.user_data.get("add_item", {})
        name = item.get("name")
        source = item.get("source")
        dest = item.get("dest")
        service = item.get("service")

        items = load_items()
        items[name] = {"source": source, "dest": dest, "service": service}
        save_items(items)

        context.user_data.pop("add_state", None)
        context.user_data.pop("add_item", None)

        await update.message.reply_text(
            f"✅ دکمهٔ «{name}» ساخته شد.\n/ start را بزن تا منو به‌روز شود."
        )
        return

# ──────────────────────────────[ دستورات ]────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def cmd_pull(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    await git_pull(update)

async def cmd_items(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    items = load_items()
    lines = []
    for k, v in items.items():
        lines.append(f"- {k}  | source: {v.get('source')}  | dest: {v.get('dest')}  | service: {v.get('service')}")
    await reply_log(update, "📜 لیست آیتم‌ها:\n" + "\n".join(lines), "items")

async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return
    args = (update.message.text or "").split(maxsplit=1)
    if len(args) < 2:
        await update.message.reply_text("استفاده: /remove <نام آیتم>")
        return
    name = args[1].strip()
    items = load_items()
    if name not in items:
        await update.message.reply_text("❌ چنین آیتمی پیدا نشد.")
        return
    if name in DEFAULT_ITEMS:
        await update.message.reply_text("⚠️ آیتم‌های پیش‌فرض قابل حذف نیستند. می‌توانی override کنی یا نام دیگری بسازی.")
        return
    items.pop(name, None)
    save_items(items)
    await update.message.reply_text(f"✅ آیتم «{name}» حذف شد.")

# ──────────────────────────────[ main ]────────────────────────────────────

def build_app():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # دستورات
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("pull", cmd_pull))
    app.add_handler(CommandHandler("items", cmd_items))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("remove", cmd_remove))

    # دکمه‌ها
    app.add_handler(CallbackQueryHandler(button))

    # روتینگ پیام‌های ویزارد ساخت دکمه
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, add_message_router))

    return app

def main():
    # مطمئن شو مسیر State وجود دارد
    ensure_state_dir()
    app = build_app()
    app.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    main()
#EOF
