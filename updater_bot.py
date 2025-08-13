#cat > /root/updater_bot.py << 'EOF'
# /root/updater_bot.py
# -*- coding: utf-8 -*-

import os, re, shlex, subprocess, json
from datetime import datetime
from pathlib import Path
from glob import glob
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

BOT_TOKEN = "7666791827:AAGeLPPlzRYb-tVke_nq6wIYtxz-fBtY9fg"
ADMIN_ID = 8062924341
REPO_PATH = "/root/sshmanager_repo"
STATE_DIR = "/etc/updater-bot"
ITEMS_JSON = f"{STATE_DIR}/items.json"
TELEGRAM_LIMIT = 3500
GUESSED_UPDATER_SERVICE = "sshmanagerbot_updater.service"

DEFAULT_ITEMS = {
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
    "Updater Bot (self)": {
        "source": None,  # auto-detect case-sensitive name
        "dest": "/root/updater_bot.py",
        "service": "auto",
    },
}

def ensure_state_dir(): Path(STATE_DIR).mkdir(parents=True, exist_ok=True)
def load_items():
    ensure_state_dir()
    if os.path.isfile(ITEMS_JSON):
        with open(ITEMS_JSON, "r", encoding="utf-8") as f:
            user_items = json.load(f)
    else: user_items = {}
    items = {**DEFAULT_ITEMS, **user_items}
    if items["Updater Bot (self)"]["source"] is None:
        for fname in ("Updater_bot.py", "updater_bot.py"):
            if os.path.exists(f"{REPO_PATH}/{fname}"):
                items["Updater Bot (self)"]["source"] = f"{REPO_PATH}/{fname}"
                break
    return items
def save_items(items):
    ensure_state_dir()
    with open(ITEMS_JSON, "w", encoding="utf-8") as f:
        json.dump({k:v for k,v in items.items() if k not in DEFAULT_ITEMS}, f, ensure_ascii=False, indent=2)

def is_admin(uid): return uid == ADMIN_ID
def fmt(s): return f"```\n{s}\n```" if s else "—"
def normalize_service(s): return s if not s else (s if s.endswith((".service",".timer")) else s+".service")
def detect_updater_service():
    for path in glob("/etc/systemd/system/*.service"):
        if "updater" in path.lower(): return os.path.basename(path)
    return GUESSED_UPDATER_SERVICE
def set_exec(path): 
    try: os.chmod(path, os.stat(path).st_mode | 0o111)
    except: pass
def run_cmd(cmd, timeout=120):
    try:
        p = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout)
        return p.returncode, p.stdout
    except subprocess.TimeoutExpired as e:
        return 124, (e.stdout or "") + "\n[Timeout]"
async def send_log(obj, text, name):
    if len(text) > TELEGRAM_LIMIT:
        p = f"/tmp/{name}.txt"
        with open(p, "w", encoding="utf-8") as f: f.write(text)
        await obj.message.reply_document(InputFile(p), caption="📄 خروجی کامل")
    else:
        await obj.edit_message_text(fmt(text), parse_mode=ParseMode.MARKDOWN_V2)

async def start(update: Update, context):
    if not is_admin(update.effective_user.id): return
    items = load_items()
    buttons = [[InlineKeyboardButton(f"⭕️ آپدیت {n}", callback_data=f"u::{n}")] for n in items]
    buttons.insert(0, [InlineKeyboardButton("🚀 آپدیت همه + ری‌استارت", callback_data="u_all")])
    buttons += [
        [InlineKeyboardButton("➕ ساخت دکمه جدید", callback_data="add")],
        [InlineKeyboardButton("📜 لیست آیتم‌ها", callback_data="list")],
        [InlineKeyboardButton("🔄 Git Pull دستی", callback_data="pull")]
    ]
    await update.message.reply_text("کدام فایل/سرویس را آپدیت کنیم؟", reply_markup=InlineKeyboardMarkup(buttons))

async def do_update(query, name, all_mode=False):
    items = load_items()
    if name not in items: await query.edit_message_text("❌ پیدا نشد"); return
    info = items[name]
    src, dst, svc = info["source"], info["dest"], info["service"]
    if not src or not os.path.exists(src):
        await query.edit_message_text(f"❌ منبع وجود ندارد: {src}"); return
    logs = [f"⏱ {datetime.now()}", f"🔧 {name}", f"📦 {src}", f"📍 {dst}", f"🧩 {svc}"]
    c,o = run_cmd(f"git -C {shlex.quote(REPO_PATH)} pull --ff-only"); logs.append(o)
    c,o = run_cmd(f"/bin/cp -f {shlex.quote(src)} {shlex.quote(dst)}"); logs.append(o)
    if dst.endswith((".py",".sh")): set_exec(dst)
    eff_svc = detect_updater_service() if svc=="auto" else normalize_service(svc)
    if eff_svc:
        for cmd in [f"systemctl daemon-reload", f"systemctl enable {eff_svc}", f"systemctl restart {eff_svc}", f"systemctl status {eff_svc}"]:
            c,o = run_cmd(cmd); logs.append(f"$ {cmd}\n{o}")
    if not all_mode: await send_log(query, "\n".join(logs), f"log-{name}")
    return "\n".join(logs)

async def button(update: Update, context):
    q = update.callback_query; await q.answer()
    if not is_admin(q.from_user.id): await q.edit_message_text("ادمین نیستید"); return
    data = q.data
    if data.startswith("u::"): await q.edit_message_text("⏳ در حال آپدیت..."); await do_update(q, data[3:])
    elif data=="u_all":
        logs = []
        for name in load_items(): logs.append(await do_update(q, name, all_mode=True))
        await send_log(q, "\n".join(logs), "update-all")
    elif data=="pull":
        c,o = run_cmd(f"git -C {REPO_PATH} pull --ff-only"); await send_log(q,o,"git-pull")
    elif data=="list":
        items = load_items()
        out = "\n".join(f"- {k} | src: {v['source']} | dst: {v['dest']} | svc: {v['service']}" for k,v in items.items())
        await send_log(q,out,"items")
    elif data=="add":
        context.user_data["add_stage"]=1; context.user_data["new_item"]={}
        await q.edit_message_text("نام دکمه:")

async def add_router(update: Update, context):
    if not is_admin(update.effective_user.id): return
    st = context.user_data.get("add_stage")
    if not st: return
    msg = update.message.text.strip()
    if st==1:
        context.user_data["new_item"]["name"]=msg; context.user_data["add_stage"]=2
        await update.message.reply_text("مسیر منبع (در ریپو یا مطلق):"); return
    if st==2:
        src = msg if msg.startswith("/") else f"{REPO_PATH}/{msg}"
        context.user_data["new_item"]["source"]=src; context.user_data["add_stage"]=3
        await update.message.reply_text("مسیر مقصد مطلق:"); return
    if st==3:
        if not msg.startswith("/"): await update.message.reply_text("❌ باید مطلق باشد"); return
        context.user_data["new_item"]["dest"]=msg; context.user_data["add_stage"]=4
        await update.message.reply_text("نام سرویس (اختیاری):"); return
    if st==4:
        svc = msg.strip() or None
        if svc and not svc.endswith((".service",".timer")): svc+=".service"
        context.user_data["new_item"]["service"]=svc
        items=load_items(); ni=context.user_data["new_item"]
        items[ni["name"]]={"source":ni["source"],"dest":ni["dest"],"service":ni["service"]}
        save_items(items)
        context.user_data.clear()
        await update.message.reply_text("✅ دکمه ساخته شد. /start")

def main():
    ensure_state_dir()
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, add_router))
    app.run_polling()

if __name__=="__main__": main()
