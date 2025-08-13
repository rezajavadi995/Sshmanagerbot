# /root/updater_bot.py
# -*- coding: utf-8 -*-

import os, shlex, subprocess, json
from datetime import datetime
from pathlib import Path
from glob import glob
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ContextTypes
)

# ───────── پیکربندی ─────────
BOT_TOKEN = "7666791827:AAGeLPPlzRYb-tVke_nq6wIYtxz-fBtY9fg"
ADMIN_ID = 8062924341
REPO_PATH = "/root/sshmanager_repo"
STATE_DIR   = "/etc/updater-bot"
ITEMS_JSON  = f"{STATE_DIR}/items.json"
TELE_LIMIT  = 3500
UPDATER_GUESS = "sshmanagerbot_updater.service"

STATIC_SERVICES = {
    "check-expire.service",
    "log-user-traffic.service",
}

# ───────── آیتم‌های پیش‌فرض ─────────
def detect_self_source():
    for fname in ("Updater_bot.py", "updater_bot.py"):
        p = f"{REPO_PATH}/{fname}"
        if os.path.exists(p):
            return p
    return f"{REPO_PATH}/updater_bot.py"

DEFAULT_ITEMS = {
    "Sshmanagerbot.py": {
        "source": f"{REPO_PATH}/Sshmanagerbot.py",
        "dest":   "/root/sshmanagerbot.py",
        "service": "sshmanagerbot.service",
    },
    "check_user_usage.py": {
        "source": f"{REPO_PATH}/check_user_usage.py",
        "dest":   "/usr/local/bin/check_user_usage.py",
        "service": "check-user-usage.service",
    },
    "check_users_expire.py": {
        "source": f"{REPO_PATH}/check_users_expire.py",
        "dest":   "/usr/local/bin/check_users_expire.py",
        "service": "check-expire.service",
    },
    "log_user_traffic.py": {
        "source": f"{REPO_PATH}/log_user_traffic.py",
        "dest":   "/usr/local/bin/log_user_traffic.py",
        "service": "log-user-traffic.service",
    },
    "Updater Bot (self)": {
        "source": detect_self_source(),
        "dest":   "/root/updater_bot.py",
        "service": "auto",
    },
}

# ───────── ابزار ─────────
def is_admin(uid: int) -> bool:
    return uid == ADMIN_ID

def ensure_state():
    Path(STATE_DIR).mkdir(parents=True, exist_ok=True)

def load_items():
    ensure_state()
    user_items = {}
    if os.path.isfile(ITEMS_JSON):
        try:
            with open(ITEMS_JSON, "r", encoding="utf-8") as f:
                user_items = json.load(f) or {}
        except Exception:
            user_items = {}
    return {**DEFAULT_ITEMS, **user_items}

def save_items(items: dict):
    ensure_state()
    user_items = {k: v for k, v in items.items() if k not in DEFAULT_ITEMS}
    with open(ITEMS_JSON, "w", encoding="utf-8") as f:
        json.dump(user_items, f, ensure_ascii=False, indent=2)

def codeblock(text: str) -> str:
    return f"```\n{text}\n```"

async def send_or_file(target, text: str, fname: str):
    if len(text) > TELE_LIMIT:
        path = f"/tmp/{fname}.txt"
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        if hasattr(target, "edit_message_text"):
            await target.edit_message_text("📄 خروجی طولانی است؛ فایل جداگانه ارسال شد.")
            await target.message.reply_document(InputFile(path), caption="📄 خروجی کامل عملیات")
        else:
            await target.reply_document(InputFile(path), caption="📄 خروجی کامل")
    else:
        if hasattr(target, "edit_message_text"):
            await target.edit_message_text(codeblock(text), parse_mode=ParseMode.MARKDOWN_V2)
        else:
            await target.reply_text(codeblock(text), parse_mode=ParseMode.MARKDOWN_V2)

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

def ensure_shebang(path: str):
    try:
        if not path.endswith(".py"):
            return False, "not a python file"
        with open(path, "r+", encoding="utf-8") as f:
            lines = f.readlines()
            if not lines or not lines[0].startswith("#!"):
                lines.insert(0, "#!/usr/bin/env python3\n")
                f.seek(0); f.writelines(lines); f.truncate()
                return True, "shebang added"
            elif "python" not in lines[0]:
                lines[0] = "#!/usr/bin/env python3\n"
                f.seek(0); f.writelines(lines); f.truncate()
                return True, "shebang fixed"
        return False, "shebang ok"
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
    if os.path.exists(f"/etc/systemd/system/{UPDATER_GUESS}"):
        return UPDATER_GUESS
    for p in glob("/etc/systemd/system/*.service"):
        b = os.path.basename(p).lower()
        if "updater" in b and "bot" in b:
            return os.path.basename(p)
    return UPDATER_GUESS

def kill_service_processes(service: str):
    try:
        sh(f"systemctl stop {service}")
        name = service.replace(".service", "")
        sh(f"pkill -f {name}")
        return True, ""
    except Exception as e:
        return False, str(e)

def systemd_reload_enable_restart(service: str):
    log = []
    sh("systemctl daemon-reload")
    svc = normalize_service(service)
    if svc:
        if svc in STATIC_SERVICES:
            timer = svc.replace(".service", ".timer")
            if os.path.exists(f"/etc/systemd/system/{timer}"):
                for cmd in (f"systemctl enable {timer}", f"systemctl start {timer}", f"systemctl status {timer}"):
                    _, o = sh(cmd); log.append(f"$ {cmd}\n{o}")
        else:
            kill_service_processes(svc)
            for cmd in (f"systemctl enable {svc}", f"systemctl restart {svc}", f"systemctl status {svc}"):
                _, o = sh(cmd); log.append(f"$ {cmd}\n{o}")
    return "\n".join(log)

# ───────── سرویس‌ها + تایمرهای یک دقیقه‌ای ─────────
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
    "check-user-usage.service": """[Unit]
Description=Check User Usage
After=network.target
[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/local/bin/check_user_usage.py
Restart=always
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
Description=Run check-expire.service every 1 min
[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
Unit=check-expire.service
[Install]
WantedBy=timers.target
""",
    "log-user-traffic.timer": """[Unit]
Description=Run log-user-traffic.service every 1 min
[Timer]
OnBootSec=1min
OnUnitActiveSec=1min
Unit=log-user-traffic.service
[Install]
WantedBy=timers.target
""",
}

def ensure_service_file(name: str, content: str):
    path = f"/etc/systemd/system/{name}"
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f: f.write(content)
        return True, path
    return False, path

def setup_services():
    logs = [f"⏱ {datetime.now()} - شروع ساخت/نصب سرویس‌ها"]
    for name, content in SERVICE_TEMPLATES.items():
        created, path = ensure_service_file(name, content)
        logs.append(f"{'✅ ایجاد شد' if created else 'ℹ️ موجود است'}: {path}")
    for svc in ("sshmanagerbot_updater.service", "check-user-usage.service", "log-user-traffic.service", "check-expire.service"):
        logs.append(systemd_reload_enable_restart(svc))
    logs.append("✅ ساخت/نصب سرویس‌ها تمام شد.")
    return "\n".join(logs)

# ───────── عملیات آپدیت ─────────
def git_pull_log():
    c, o = sh(f"git -C {shlex.quote(REPO_PATH)} pull --ff-only")
    return f"$ git -C {REPO_PATH} pull --ff-only\n{o}(exit={c})"

def do_single_update(name: str):
    items = load_items()
    if name not in items:
        return False, f"❌ آیتم «{name}» پیدا نشد."
    info = items[name]
    src, dst, svc = info.get("source"), info.get("dest"), info.get("service")
    logs = [f"⏱ {datetime.now()}", f"🔧 آیتم: {name}", f"📦 منبع: {src}", f"📍 مقصد: {dst}", f"🧩 سرویس: {svc}"]
    logs.append(git_pull_log())
    if not src or not os.path.exists(src):
        logs.append(f"❌ منبع وجود ندارد: {src}")
        return False, "\n".join(logs)
    c, o = cp_force(src, dst)
    logs.append(f"$ cp -f {src} {dst}\n{o}(exit={c})")
    if c != 0: return False, "\n".join(logs)
    if dst.endswith((".py", ".sh")):
        changed, msg = ensure_shebang(dst); logs.append(f"📜 Shebang: {msg}")
        ok, err = chmod_exec(dst); logs.append("✅ chmod +x" if ok else f"⚠️ chmod خطا: {err}")
    eff = detect_updater_service() if svc == "auto" else normalize_service(svc)
    if eff: logs.append(systemd_reload_enable_restart(eff))
    logs.append(f"✅ عملیات «{name}» تمام شد.")
    return True, "\n".join(logs)

# ───────── رابط کاربری ─────────
def keyboard():
    items = load_items()
    rows = [[InlineKeyboardButton(f"⭕️ آپدیت {n}", callback_data=f"u::{n}")] for n in items]
    rows.insert(0, [InlineKeyboardButton("🚀 آپدیت همه + ری‌استارت", callback_data="u_all")])
    rows += [
        [InlineKeyboardButton("🛠 ساخت/نصب سرویس‌ها", callback_data="setup")],
        [InlineKeyboardButton("📜 لیست آیتم‌ها", callback_data="list"),
         InlineKeyboardButton("🔄 Git Pull", callback_data="pull")]
    ]
    return InlineKeyboardMarkup(rows)

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return
    await update.message.reply_text("یک گزینه را انتخاب کن:", reply_markup=keyboard())

async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer()
    if not is_admin(q.from_user.id):
        return await q.edit_message_text("❌ دسترسی ندارید.")
    data = q.data or ""
    if data.startswith("u::"):
        name = data.split("::", 1)[1]
        await q.edit_message_text(f"⏳ در حال آپدیت «{name}» ...")
        _, log = do_single_update(name)
        await send_or_file(q, log, f"update-{name}")
    elif data == "u_all":
        await q.edit_message_text("⏳ در حال آپدیت همه آیتم‌ها ...")
        all_logs = []
        for name in load_items().keys():
            _, log = do_single_update(name)
            all_logs.append(log)
        await send_or_file(q, "\n\n".join(all_logs), "update-all")
    elif data == "pull":
        await q.edit_message_text("⏳ در حال git pull ...")
        await send_or_file(q, git_pull_log(), "git-pull")
    elif data == "list":
        items = load_items()
        lines = [f"- {k} | src: {v.get('source')} | dst: {v.get('dest')} | svc: {v.get('service')}" for k, v in items.items()]
        await send_or_file(q, "📜 لیست آیتم‌ها:\n" + "\n".join(lines), "items")
    elif data == "setup":
        await q.edit_message_text("⏳ در حال ساخت/نصب سرویس‌ها ...")
        await send_or_file(q, setup_services(), "setup-services")

# ───────── main ─────────
def main():
    ensure_state()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(on_button))
    app.run_polling(allowed_updates=["message","callback_query"])

if __name__ == "__main__":
    main()
