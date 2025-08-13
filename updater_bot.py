#cat > /root/updater_bot.py << 'EOF'
# /root/updater_bot.py
# -*- coding: utf-8 -*-

import os, re, shlex, subprocess, json
from datetime import datetime
from pathlib import Path
from glob import glob

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

# ───────── پیکربندی ─────────
BOT_TOKEN = "7666791827:AAGeLPPlzRYb-tVke_nq6wIYtxz-fBtY9fg"
ADMIN_ID = 8062924341
REPO_PATH = "/root/sshmanager_repo"

STATE_DIR   = "/etc/updater-bot"
ITEMS_JSON  = f"{STATE_DIR}/items.json"
TELE_LIMIT  = 3500
UPDATER_GUESS = "sshmanagerbot_updater.service"  # اسم سرویسی که گفتی

# ───────── آیتم‌های پیش‌فرض ─────────
DEFAULT_ITEMS = {
    "Sshmanagerbot.py": {
        "source": f"{REPO_PATH}/Sshmanagerbot.py",
        "dest":   "/root/sshmanagerbot.py",
        "service": "sshmanagerbot.service",
    },
    "check_user_usage.py": {
        "source": f"{REPO_PATH}/check_user_usage.py",
        "dest":   "/usr/local/bin/check_user_usage.py",
        "service": None,  # اگر سرویس داری اینجا نامش رو بذار
    },
    "check_users_expire.py": {
        "source": f"{REPO_PATH}/check_users_expire.py",
        "dest":   "/usr/local/bin/check_users_expire.py",
        "service": "check-expire.service",  # ری‌استارت همزمان
    },
    "log_user_traffic.py": {
        "source": f"{REPO_PATH}/log_user_traffic.py",
        "dest":   "/usr/local/bin/log_user_traffic.py",
        "service": "log-user-traffic.service",  # ری‌استارت همزمان
    },
    # فایل‌های سیستمی آماده
    "log-user-traffic.service": {
        "source": f"{REPO_PATH}/log-user-traffic.service",
        "dest":   "/etc/systemd/system/log-user-traffic.service",
        "service": "log-user-traffic.service",
    },
    "check-expire.service": {
        "source": f"{REPO_PATH}/check-expire.service",
        "dest":   "/etc/systemd/system/check-expire.service",
        "service": "check-expire.service",
    },
    # خود ربات (case-insensitive)
    "Updater Bot (self)": {
        "source": None,  # بعداً اتومات پر می‌شود
        "dest":   "/root/updater_bot.py",
        "service": "auto",
    },
}

# ───────── ابزار ─────────
def is_admin(uid: int) -> bool: return uid == ADMIN_ID
def ensure_state(): Path(STATE_DIR).mkdir(parents=True, exist_ok=True)

def load_items():
    ensure_state()
    user_items = {}
    if os.path.isfile(ITEMS_JSON):
        try:
            with open(ITEMS_JSON, "r", encoding="utf-8") as f:
                user_items = json.load(f) or {}
        except Exception:
            user_items = {}
    items = {**DEFAULT_ITEMS, **user_items}
    # auto-detect updater file name
    if items["Updater Bot (self)"]["source"] is None:
        for fname in ("Updater_bot.py","updater_bot.py"):
            p = f"{REPO_PATH}/{fname}"
            if os.path.exists(p):
                items["Updater Bot (self)"]["source"] = p
                break
    return items

def save_items(items: dict):
    ensure_state()
    user_items = {k:v for k,v in items.items() if k not in DEFAULT_ITEMS}
    with open(ITEMS_JSON, "w", encoding="utf-8") as f:
        json.dump(user_items, f, ensure_ascii=False, indent=2)

def codeblock(text: str) -> str:
    return f"```\n{text}\n```"

async def send_or_file_for_query(q, text: str, fname: str):
    if len(text) > TELE_LIMIT:
        path = f"/tmp/{fname}.txt"
        with open(path,"w",encoding="utf-8") as f: f.write(text)
        await q.edit_message_text("📄 خروجی طولانی است؛ فایل جداگانه ارسال شد.")
        await q.message.reply_document(InputFile(path), caption="📄 خروجی کامل عملیات")
    else:
        await q.edit_message_text(codeblock(text), parse_mode=ParseMode.MARKDOWN_V2)

async def send_or_file_for_update(upd: Update, text: str, fname: str):
    if len(text) > TELE_LIMIT:
        path = f"/tmp/{fname}.txt"
        with open(path,"w",encoding="utf-8") as f: f.write(text)
        await upd.message.reply_document(InputFile(path), caption="📄 خروجی کامل")
    else:
        await upd.message.reply_text(codeblock(text), parse_mode=ParseMode.MARKDOWN_V2)

def sh(cmd: str, timeout: int = 180):
    p = subprocess.run(cmd, shell=True, text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    return p.returncode, p.stdout

def cp_force(src: str, dst: str):
    Path(os.path.dirname(dst)).mkdir(parents=True, exist_ok=True)
    return sh(f"/bin/cp -f {shlex.quote(src)} {shlex.quote(dst)}")

def chmod_exec(path: str):
    try:
        os.chmod(path, os.stat(path).st_mode | 0o111)
        return True, ""
    except Exception as e:
        return False, str(e)

def normalize_service(name: str | None):
    if not name: return None
    name = name.strip()
    if not name: return None
    if not name.endswith(".service") and not name.endswith(".timer"):
        name += ".service"
    return name

def detect_updater_service():
    # ترجیحاً همونی که گفتی
    if os.path.exists(f"/etc/systemd/system/{UPDATER_GUESS}"):
        return UPDATER_GUESS
    for p in glob("/etc/systemd/system/*.service"):
        b = os.path.basename(p).lower()
        if "updater" in b and "bot" in b:
            return os.path.basename(p)
    return UPDATER_GUESS

def systemd_reload_enable_restart(service: str):
    log = []
    c,o = sh("systemctl daemon-reload"); log.append(f"$ systemctl daemon-reload\n{o}")
    svc = normalize_service(service)
    if svc:
        for cmd in (f"systemctl enable {svc}",
                    f"systemctl restart {svc}",
                    f"systemctl status {svc}"):
            c,o = sh(cmd); log.append(f"$ {cmd}\n{o}")
        # اگر تایمر متناظر وجود دارد فعال/استارت شود
        if svc.endswith(".service"):
            timer = svc.replace(".service",".timer")
            if os.path.exists(f"/etc/systemd/system/{timer}"):
                for cmd in (f"systemctl enable {timer}", f"systemctl start {timer}", f"systemctl status {timer}"):
                    c,o = sh(cmd); log.append(f"$ {cmd}\n{o}")
    return "\n".join(log)

# ───────── ساخت/نصب سرویس‌ها در صورت نبود ─────────
SERVICE_TEMPLATES = {
    "sshmanagerbot_updater.service": """[Unit]
Description=SSHManager Updater Bot
After=network.target

[Service]
User=root
WorkingDirectory=/root
ExecStart=/usr/bin/python3 /root/updater_bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
""",
    "log-user-traffic.service": """[Unit]
Description=Log User Traffic
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/local/bin/log_user_traffic.py
Restart=always

[Install]
WantedBy=multi-user.target
""",
    "check-expire.service": """[Unit]
Description=Check Users Expire
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/local/bin/check_users_expire.py
Restart=on-failure

[Install]
WantedBy=multi-user.target
""",
    "check-expire.timer": """[Unit]
Description=Run check-expire.service periodically

[Timer]
OnBootSec=2min
OnUnitActiveSec=10min
Unit=check-expire.service

[Install]
WantedBy=timers.target
""",
}

def ensure_service_file(name: str, content: str):
    path = f"/etc/systemd/system/{name}"
    created = False
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        created = True
    return created, path

def setup_services():
    logs = []
    for name, content in SERVICE_TEMPLATES.items():
        created, path = ensure_service_file(name, content)
        logs.append(f"{'✅ ایجاد شد' if created else 'ℹ️ موجود است'}: {path}")
    # daemon-reload و enable/start برای سه سرویس کلیدی
    logs.append(systemd_reload_enable_restart("sshmanagerbot_updater.service"))
    logs.append(systemd_reload_enable_restart("log-user-traffic.service"))
    logs.append(systemd_reload_enable_restart("check-expire.service"))
    return "\n".join(logs)

# ───────── UI ─────────
def keyboard():
    items = load_items()
    rows = [[InlineKeyboardButton(f"⭕️ آپدیت {n}", callback_data=f"u::{n}")] for n in items]
    rows.insert(0, [InlineKeyboardButton("🚀 آپدیت همه + ری‌استارت", callback_data="u_all")])
    rows += [
        [InlineKeyboardButton("🛠 ساخت/نصب سرویس‌ها", callback_data="setup")],
        [InlineKeyboardButton("📜 لیست آیتم‌ها", callback_data="list"),
         InlineKeyboardButton("🔄 Git Pull", callback_data="pull")],
        [InlineKeyboardButton("➕ ساخت دکمه", callback_data="add")]
    ]
    return InlineKeyboardMarkup(rows)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await update.message.reply_text("یک گزینه را انتخاب کن:", reply_markup=keyboard())

# ───────── عملیات آپدیت ─────────
def git_pull_log():
    c,o = sh(f"git -C {shlex.quote(REPO_PATH)} pull --ff-only")
    return f"$ git -C {REPO_PATH} pull --ff-only\n{o}(exit={c})"

def do_single_update(name: str):
    items = load_items()
    if name not in items: return False, f"❌ آیتم «{name}» پیدا نشد."

    info = items[name]
    src, dst, svc = info.get("source"), info.get("dest"), info.get("service")

    logs = [f"⏱ {datetime.now()}",
            f"🔧 آیتم: {name}",
            f"📦 منبع: {src}",
            f"📍 مقصد: {dst}",
            f"🧩 سرویس: {svc}"]

    # 1) git pull
    logs.append(git_pull_log())

    # 2) validate source
    if not src or not os.path.exists(src):
        logs.append(f"❌ منبع وجود ندارد: {src}")
        return False, "\n".join(logs)

    # 3) copy
    c,o = cp_force(src, dst)
    logs.append(f"$ cp -f {src} {dst}\n{o}(exit={c})")
    if c != 0:
        return False, "\n".join(logs)

    # 4) make executable if py/sh
    if dst.endswith((".py",".sh")):
        ok, err = chmod_exec(dst)
        logs.append("✅ chmod +x انجام شد." if ok else f"⚠️ chmod خطا: {err}")

    # 5) systemd
    eff = None
    if svc == "auto":
        eff = detect_updater_service()
        logs.append(f"🔎 سرویس تشخیص‌شده: {eff}")
    else:
        eff = normalize_service(svc)

    if eff:
        logs.append(systemd_reload_enable_restart(eff))

    logs.append(f"✅ عملیات «{name}» تمام شد.")
    return True, "\n".join(logs)

# ───────── کال‌بک دکمه‌ها ─────────
async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.edit_message_text("❌ دسترسی ندارید.")
        return

    data = q.data or ""
    if data.startswith("u::"):
        name = data.split("::",1)[1]
        await q.edit_message_text(f"⏳ در حال آپدیت «{name}» ...")
        ok, log = do_single_update(name)
        await send_or_file_for_query(q, log, f"update-{name}")
        return

    if data == "u_all":
        await q.edit_message_text("⏳ در حال آپدیت همه آیتم‌ها ...")
        all_logs = []
        for name in load_items().keys():
            ok, log = do_single_update(name)
            all_logs.append(log)
        await send_or_file_for_query(q, "\n\n" + ("\n".join(all_logs)), "update-all")
        return

    if data == "pull":
        await q.edit_message_text("⏳ در حال git pull ...")
        log = git_pull_log()
        await send_or_file_for_query(q, log, "git-pull")
        return

    if data == "list":
        items = load_items()
        lines = [
            f"- {k} | src: {v.get('source')} | dst: {v.get('dest')} | svc: {v.get('service')}"
            for k,v in items.items()
        ]
        await send_or_file_for_query(q, "📜 لیست آیتم‌ها:\n" + "\n".join(lines), "items")
        return

    if data == "setup":
        await q.edit_message_text("⏳ در حال ساخت/نصب سرویس‌ها ...")
        out = setup_services()
        await send_or_file_for_query(q, out, "setup-services")
        return

    if data == "add":
        context.user_data["add_state"] = 1
        context.user_data["new_item"] = {}
        await q.edit_message_text("نام دکمه را بفرست:")
        return

# ───────── ویزارد ساخت دکمه (کوتاه و بی‌حرف) ─────────
async def add_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    st = context.user_data.get("add_state")
    if not st: return
    msg = (update.message.text or "").strip()

    if st == 1:
        context.user_data["new_item"]["name"] = msg
        context.user_data["add_state"] = 2
        await update.message.reply_text("مسیر منبع (در ریپو یا مطلق):")
        return

    if st == 2:
        src = msg if msg.startswith("/") else f"{REPO_PATH.rstrip('/')}/{msg}"
        context.user_data["new_item"]["source"] = src
        context.user_data["add_state"] = 3
        await update.message.reply_text("مسیر مقصد مطلق:")
        return

    if st == 3:
        if not msg.startswith("/"):
            await update.message.reply_text("❌ باید مطلق باشد. دوباره بفرست.")
            return
        context.user_data["new_item"]["dest"] = msg
        context.user_data["add_state"] = 4
        await update.message.reply_text("نام سرویس (اختیاری):")
        return

    if st == 4:
        svc = msg.strip() or None
        if svc and not svc.endswith((".service",".timer")):
            svc += ".service"
        ni = context.user_data["new_item"]
        ni["service"] = svc
        items = load_items()
        items[ni["name"]] = {"source": ni["source"], "dest": ni["dest"], "service": ni["service"]}
        save_items(items)
        context.user_data.clear()
        await update.message.reply_text("✅ دکمه ساخته شد. /start")
        return

# ───────── دستورات ─────────
async def cmd_pull(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    log = git_pull_log()
    await send_or_file_for_update(update, log, "git-pull")

async def cmd_items(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    items = load_items()
    lines = [f"- {k} | src: {v.get('source')} | dst: {v.get('dest')} | svc: {v.get('service')}" for k,v in items.items()]
    await send_or_file_for_update(update, "📜 لیست آیتم‌ها:\n" + "\n".join(lines), "items")

async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    context.user_data["add_state"] = 1
    context.user_data["new_item"] = {}
    await update.message.reply_text("نام دکمه را بفرست:")

async def cmd_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    out = setup_services()
    await send_or_file_for_update(update, out, "setup-services")

# ───────── main ─────────
def main():
    ensure_state()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("pull", cmd_pull))
    app.add_handler(CommandHandler("items", cmd_items))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("setup", cmd_setup))

    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, add_router))

    app.run_polling(allowed_updates=["message","callback_query"])

if __name__ == "__main__":
    main()
