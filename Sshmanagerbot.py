#این نسخه  فعلا باگ ساخت اکانت برطرف شده ولی هنوز باگ داره
#cat > /root/sshmanagerbot7.py << 'EOF'
#!/usr/bin/env python3
# sshmanagerbot_fixed.py
import os
import subprocess
import random
import string
import psutil
import socket
import json
import pwd
import logging
from datetime import datetime, timedelta
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove

from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

# ---------- Configuration (use environment variables) ----------
BOT_TOKEN = "8152962391:AAG4kYisE21KI8dAbzFy9oq-rn9h9RCQyBM"
ADMIN_ID = 8062924341
PORT_PUBLIC = 443
DOMAIN = "ssh.ultraspeed.shop"
NOLOGIN_PATH = "/usr/sbin/nologin"
FIX_IPTABLES_SCRIPT = "/root/fix-iptables.sh"

# ensure directories
Path("/etc/sshmanager/limits").mkdir(parents=True, exist_ok=True)
Path("/etc/sshmanager/logs").mkdir(parents=True, exist_ok=True)

# conversation states
ASK_USERNAME, ASK_TYPE, ASK_VOLUME, ASK_EXPIRE = range(4)
ASK_RENEW_USERNAME, ASK_RENEW_ACTION, ASK_RENEW_VALUE = range(4, 7)
ASK_DELETE_USERNAME = 7
ASK_UNLOCK_USERNAME = 8
ASK_ANOTHER_RENEW = 9
main_menu_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        ["📊 وضعیت سیستم", "🛡 بررسی سلامت سرور"],
        ["🔎 بررسی پورت و دامنه", "⚠ فایل‌های مشکوک"],
        ["📋 لیست کاربران", "📉 مصرف کاربران"],
        ["بازگشت به منو"]
    ],
    resize_keyboard=True
)

# ---------- utilities ----------
log = logging.getLogger("sshmanager")
logging.basicConfig(level=logging.INFO)

def random_str(length=10):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

def get_reply_func(update: Update):
    """Return a function to reply depending on whether update is message or callback_query."""
    if hasattr(update, "message") and update.message:
        return update.message.reply_text
    elif hasattr(update, "callback_query") and update.callback_query:
        return update.callback_query.message.reply_text
    else:
        return lambda *args, **kwargs: None

def lock_user_account(username: str) -> bool:
    try:
        subprocess.run(["sudo", "passwd", "-l", username], check=True)
        subprocess.run(["sudo", "usermod", "-s", NOLOGIN_PATH, username], check=True)
        
        # New: kill all active sessions for the user
        subprocess.run(["sudo", "pkill", "-u", username], check=False)

        # remove iptables rule if exists
        try:
            uid = subprocess.getoutput(f"id -u {username}").strip()
            subprocess.run(["sudo", "iptables", "-D", "SSH_USERS", "-m", "owner", "--uid-owner", uid, "-j", "ACCEPT"], check=False)
        except Exception:
            pass
        return True
    except Exception as e:
        log.exception("lock_user_account failed")
        return False


def fix_iptables():
    try:
        subprocess.run(["sudo", "bash", FIX_IPTABLES_SCRIPT], check=True)
    except Exception as e:
        log.warning("fix_iptables failed: %s", e)

def format_config(username, password, expire_str):
    return f"""مشخصات اتصال:
✅️ Host: {DOMAIN}
✅️ Port: {PORT_PUBLIC}
✅️ Username: {username}
✅️ Password: {password}
✅️ SNI: {DOMAIN}
✅️ TLS: 1.2

⏳ این اکانت تا {expire_str} معتبر است."""

def get_system_stats():
    cpu = psutil.cpu_percent(interval=1)
    ram = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    return f"""📊 وضعیت سیستم:

🧠 CPU: {cpu}%
💾 RAM: {ram.percent}% از {round(ram.total / 1024**3, 2)} GB
📀 دیسک: {disk.percent}% از {round(disk.total / 1024**3, 2)} GB"""

def check_ports_and_ping(domain=DOMAIN):
    result = subprocess.getoutput(f"ping -c 1 {domain}")
    ports = [2222, 80, 443]
    port_status = ""
    for port in ports:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        status = sock.connect_ex((domain, port))
        port_status += f"✅ پورت {port} باز است\n" if status == 0 else f"❌ پورت {port} بسته است\n"
        sock.close()
    return f"""🔎 بررسی دامنه و پورت‌ها:
📡 دامنه: {domain}

🛰 پینگ:
{result}

🔌 پورت‌ها:
{port_status}"""

def find_suspicious_files():
    output = subprocess.getoutput("find / -type f \\( -name '*.sh' -o -name '*.py' \\) -mmin -30 2>/dev/null | head -10")
    return "⚠ فایل‌های مشکوک اخیر:\n\n" + (output if output else "فایلی یافت نشد.")

def list_real_users():
    lines = subprocess.getoutput("getent passwd | awk -F: '$3>=1000 {print}'").splitlines()
    return [line.split(":")[0] for line in lines]

def get_user_traffic(username):
    """Read a per-user log if exists. Expect stored value in KB (integer)."""
    try:
        log_path = f"/etc/sshmanager/logs/{username}.log"
        if os.path.exists(log_path):
            with open(log_path, "r") as f:
                v = f.read().strip()
                return int(v) if v else 0
    except Exception:
        log.exception("get_user_traffic error")
    return 0

# ---------- Handlers (conversation & actions) ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = get_reply_func(update)
    if update.effective_user.id != ADMIN_ID:
        return await reply("⛔ دسترسی ندارید.")
    fix_iptables()
    Path("/etc/sshmanager/limits").mkdir(parents=True, exist_ok=True)

    keyboard = [
        [InlineKeyboardButton("✅ ساخت اکانت SSH", callback_data="create_user")],
        [InlineKeyboardButton("❌ حذف اکانت", callback_data="delete_user")],
        [
            InlineKeyboardButton("🔒 قفل‌کردن اکانت", callback_data="lock_user"),
            InlineKeyboardButton("🔓 بازکردن اکانت", callback_data="unlock_user")
        ],
        [InlineKeyboardButton("📊 کاربران حجمی", callback_data="show_limited")],
        [InlineKeyboardButton("🚫 کاربران مسدود", callback_data="show_blocked")],
        [InlineKeyboardButton("⏳ تمدید اکانت", callback_data="extend_user")],
        [InlineKeyboardButton("📋 گزارش اکانت‌ها", callback_data="report_users")]
    ]

    await reply("📲 پنل مدیریت SSH:", reply_markup=InlineKeyboardMarkup(keyboard))
    
    # NEW: از یک نقطه به جای فاصله خالی برای جلوگیری از خطای "Text must be non-empty" استفاده شد.
    await reply(".", reply_markup=ReplyKeyboardRemove())




async def ask_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    if update.effective_user.id != ADMIN_ID:
        return
    await update.callback_query.message.reply_text("❗️ لطفاً یوزرنیم وارد شود:")
    return ASK_USERNAME

async def ask_account_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # message handler previously set username
    context.user_data['username'] = update.message.text.strip()
    keyboard = [
        [InlineKeyboardButton("📦 حجمی", callback_data="acc_type_limited")],
        [InlineKeyboardButton("♾ نامحدود", callback_data="acc_type_unlimited")]
    ]
    await update.message.reply_text("📘 چه نوع اکانتی می‌خواهید بسازید؟", reply_markup=InlineKeyboardMarkup(keyboard))
    return ASK_TYPE

async def handle_account_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    acc_type = query.data.replace("acc_type_", "")
    context.user_data["acc_type"] = acc_type
    if acc_type == "limited":
        await query.message.reply_text("📏 چه محدودیتی برای حجم تنظیم کنیم؟\nمثلاً: `30MB` یا `1.5GB`", parse_mode="Markdown")
        return ASK_VOLUME
    else:
        return await ask_expire(update, context)

async def handle_volume_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper()
    volume_gb = 0
    volume_mb = 0
    try:
        if text.endswith("GB"):
            volume_gb = float(text[:-2].strip())
            volume_mb = int(volume_gb * 1024)
        elif text.endswith("MB"):
            volume_mb = int(float(text[:-2].strip()))
        else:
            volume_mb = int(float(text))
    except Exception:
        await update.message.reply_text("❌ حجم واردشده نامعتبر است. لطفاً مانند `30MB` یا `1.5GB` وارد کنید.")
        return ASK_VOLUME

    if volume_mb <= 0:
        await update.message.reply_text("❌ حجم واردشده باید بزرگتر از صفر باشد.")
        return ASK_VOLUME

    # NEW: Store the volume in KB correctly
    context.user_data["volume"] = volume_mb * 1024
    
    return await ask_expire(update, context)



async def ask_expire(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # for both callback and message flows this returns expire selection
    # if called from callback_query, update.callback_query exists; if from message, update.message used earlier
    if hasattr(update, "callback_query") and update.callback_query:
        caller = update.callback_query.message
    else:
        caller = update.message
    # خط زیر حذف شد چون نام کاربری قبلاً در ask_account_type ذخیره شده است.
    # context.user_data['username'] = context.user_data.get('username', caller.text.strip())
    keyboard = [
        [InlineKeyboardButton("⌛️ یک ماهه", callback_data="expire_30d")],
        [InlineKeyboardButton("⏳️ دو ماهه", callback_data="expire_60d")],
        [InlineKeyboardButton("⏳️ سه ماهه", callback_data="expire_90d")]
    ]
    await caller.reply_text("⏱️ لطفاً مدت انتخاب شود:", reply_markup=InlineKeyboardMarkup(keyboard))
    return ASK_EXPIRE


# ------------- extend flow -------------
async def start_extend_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    await update.callback_query.message.reply_text("📋 لطفاً *نام کاربری* که می‌خواهید تمدید کنید را وارد کنید:", parse_mode="Markdown")
    return ASK_RENEW_USERNAME

async def handle_extend_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END
    username = update.message.text.strip()
    context.user_data["renew_username"] = username
    # check user exists
    check = subprocess.getoutput(f"id -u {username}")
    if not check.isdigit():
        await update.message.reply_text("❌ این یوزرنیم وجود ندارد.")
        return ConversationHandler.END
    # check lock status
    passwd_s = subprocess.getoutput(f"passwd -S {username} 2>/dev/null").split()
    locked = (len(passwd_s) > 1 and passwd_s[1] == "L")
    lock_status = "🚫 مسدود" if locked else "✅ فعال"
    # read limit
    limits_file = f"/etc/sshmanager/limits/{username}.json"
    if os.path.exists(limits_file):
        try:
            with open(limits_file) as f:
                data = json.load(f)
            used_kb = int(data.get("used", 0))
            limit_kb = int(data.get("limit", 0))
            percent = int((used_kb / max(1, limit_kb)) * 100) if limit_kb > 0 else 0
            type_status = "✅ محدود (حجمی)" if data.get("type") == "limited" else "✅ نامحدود"
            expire_ts = int(data.get("expire_timestamp", 0)) if data.get("expire_timestamp") else None
            expire_date = datetime.fromtimestamp(expire_ts).strftime("%Y-%m-%d") if expire_ts else "نامشخص"
            usage_info = f"{used_kb // 1024}MB / {limit_kb // 1024}MB ({percent}%)"
        except Exception:
            usage_info = "نامشخص"
            expire_date = "نامشخص"
            type_status = "نامشخص"
    else:
        usage_info = "نامشخص"
        expire_date = "نامشخص"
        type_status = "⛔️ فاقد محدودیت حجمی"

    await update.message.reply_text(
        f"👤 اطلاعات اکانت: `{username}`\n"
        f"📊 مصرف: {usage_info}\n"
        f"⏳ تاریخ انقضا: {expire_date}\n"
        f"🔐 وضعیت قفل: {lock_status}\n"
        f"{type_status}",
        parse_mode="Markdown"
    )

    keyboard = [
        [InlineKeyboardButton("🕒 تمدید زمان", callback_data="renew_time"),
         InlineKeyboardButton("📶 تمدید حجم", callback_data="renew_volume")]
    ]
    await update.message.reply_text("لطفاً نوع تمدید را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(keyboard))
    return ASK_RENEW_ACTION

async def handle_extend_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    action = query.data  # renew_time or renew_volume
    context.user_data["renew_action"] = action
    username = context.user_data.get("renew_username", "")
    if action == "renew_time":
        keyboard = [
            [InlineKeyboardButton("1️⃣ یک‌ماهه", callback_data="add_days_30")],
            [InlineKeyboardButton("2️⃣ دو‌ماهه", callback_data="add_days_60")],
            [InlineKeyboardButton("3️⃣ سه‌ماهه", callback_data="add_days_90")]
        ]
        await query.message.reply_text("📆 مدت مورد نظر را انتخاب کنید:", reply_markup=InlineKeyboardMarkup(keyboard))
        return ASK_RENEW_VALUE
    else:
        # current volume info shown in next step
        limits_file = f"/etc/sshmanager/limits/{username}.json"
        current_volume = "نامشخص"
        if os.path.exists(limits_file):
            try:
                with open(limits_file) as f:
                    data = json.load(f)
                used = int(data.get("used", 0))
                limit = int(data.get("limit", 0))
                current_volume = f"{used//1024}MB / {limit//1024}MB"
            except Exception:
                pass
        keyboard = [
            [InlineKeyboardButton("10 گیگ", callback_data="add_gb_10")],
            [InlineKeyboardButton("20 گیگ", callback_data="add_gb_20")],
            [InlineKeyboardButton("35 گیگ", callback_data="add_gb_35")],
            [InlineKeyboardButton("50 گیگ", callback_data="add_gb_50")]
        ]
        await query.message.reply_text(f"📶 حجم فعلی `{username}`: `{current_volume}`\n\nمقدار اضافه:", reply_markup=InlineKeyboardMarkup(keyboard))
        return ASK_RENEW_VALUE





async def handle_extend_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    username = context.user_data.get("renew_username", "")
    action = context.user_data.get("renew_action", "")
    data = query.data
    
    added_days = 0
    added_gb = 0

    if not username or not action:
        await query.message.reply_text("❌ اطلاعات تمدید ناقص است.")
        return ConversationHandler.END

    uid = subprocess.getoutput(f"id -u {username}").strip()

    # --- تمدید زمان ---
    if action == "renew_time" and data.startswith("add_days_"):
        days = int(data.replace("add_days_", ""))
        added_days = days

        # تاریخ فعلی انقضا را بخوان
        output = subprocess.getoutput(f"chage -l {username} 2>/dev/null")
        current_exp = ""
        for line in output.splitlines():
            if "Account expires" in line:
                current_exp = line.split(":", 1)[1].strip()
                break

        if current_exp.lower() != "never" and current_exp:
            try:
                current_date = datetime.strptime(current_exp, "%b %d, %Y")
                new_date = current_date + timedelta(days=days)
            except Exception:
                new_date = datetime.now() + timedelta(days=days)
        else:
            new_date = datetime.now() + timedelta(days=days)

        subprocess.run(["sudo", "chage", "-E", new_date.strftime("%Y-%m-%d"), username], check=False)

        # 📌 بعد از آپدیت limits_file → آنلاک خودکار اگر قفل موقت بوده
        limits_file = f"/etc/sshmanager/limits/{username}.json"
        try:
            with open(limits_file, "r") as f:
                j = json.load(f)
        except Exception:
            j = {}

        if j.get("is_blocked", False) and j.get("block_reason") != "manual":
            subprocess.run(["sudo", "usermod", "-s", "/bin/bash", username], check=False)
            subprocess.run(["sudo", "passwd", "-u", username], check=False)
            subprocess.run(["sudo", "chage", "-E", "-1", username], check=False)

            rc = subprocess.run(
                ["sudo", "iptables", "-C", "SSH_USERS", "-m", "owner", "--uid-owner", uid, "-j", "ACCEPT"],
                stderr=subprocess.DEVNULL
            ).returncode
            if rc != 0:
                subprocess.run(
                    ["sudo", "iptables", "-A", "SSH_USERS", "-m", "owner", "--uid-owner", uid, "-j", "ACCEPT"],
                    check=False
                )

            j["is_blocked"] = False
            j["block_reason"] = None
            j["alert_sent"] = False
            with open(limits_file, "w") as fw:
                json.dump(j, fw, indent=4)

        # پیام موفقیت
        await query.message.reply_text(f"⏳ {days} روز به تاریخ انقضای `{username}` اضافه شد.", parse_mode="Markdown")
        context.user_data["added_days"] = added_days

        # پیشنهاد تمدید حجم
        keyboard = [
            [InlineKeyboardButton("➕ تمدید حجم", callback_data="renew_volume"),
             InlineKeyboardButton("❌ خیر", callback_data="end_extend")]
        ]
        await query.message.reply_text("آیا می‌خواهید حجم هم تمدید شود؟", reply_markup=InlineKeyboardMarkup(keyboard))
        return ASK_ANOTHER_RENEW

    # --- تمدید حجم ---
    elif action == "renew_volume" and data.startswith("add_gb_"):
        gb = int(data.replace("add_gb_", ""))
        added_gb = gb
        limits_file = f"/etc/sshmanager/limits/{username}.json"
        if os.path.exists(limits_file):
            try:
                with open(limits_file, "r") as f:
                    j = json.load(f)
            except Exception:
                j = {}

            add_kb = gb * 1024 * 1024
            j["limit"] = int(j.get("limit", 0)) + add_kb

            # 📌 آنلاک خودکار اگر قفل موقت بوده
            if j.get("is_blocked", False) and j.get("block_reason") != "manual":
                subprocess.run(["sudo", "usermod", "-s", "/bin/bash", username], check=False)
                subprocess.run(["sudo", "passwd", "-u", username], check=False)
                subprocess.run(["sudo", "chage", "-E", "-1", username], check=False)

                rc = subprocess.run(
                    ["sudo", "iptables", "-C", "SSH_USERS", "-m", "owner", "--uid-owner", uid, "-j", "ACCEPT"],
                    stderr=subprocess.DEVNULL
                ).returncode
                if rc != 0:
                    subprocess.run(
                        ["sudo", "iptables", "-A", "SSH_USERS", "-m", "owner", "--uid-owner", uid, "-j", "ACCEPT"],
                        check=False
                    )

                j["is_blocked"] = False
                j["block_reason"] = None
                j["alert_sent"] = False

            with open(limits_file, "w") as f:
                json.dump(j, f, indent=4)

            await query.message.reply_text(f"📶 حجم اکانت `{username}` به مقدار {gb}GB افزایش یافت.", parse_mode="Markdown")
            context.user_data["added_gb"] = added_gb
        else:
            await query.message.reply_text("❌ فایل محدودیت پیدا نشد.")

        # پیشنهاد تمدید زمان
        keyboard = [
            [InlineKeyboardButton("➕ تمدید زمان", callback_data="renew_time"),
             InlineKeyboardButton("❌ خیر", callback_data="end_extend")]
        ]
        await query.message.reply_text("آیا می‌خواهید زمان هم تمدید شود؟", reply_markup=InlineKeyboardMarkup(keyboard))
        return ASK_ANOTHER_RENEW

    # --- پایان ---
    if added_gb and added_days:
        await query.message.reply_text(
            f"✅ تمدید انجام شد:\n👤 `{username}`\n🕒 +{added_days} روز\n📶 +{added_gb}GB",
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    return ConversationHandler.END
#کد جدید ادامه کانورسیشن

async def handle_renew_another_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "renew_time":
        context.user_data["renew_action"] = "renew_time"
        keyboard = [
            [InlineKeyboardButton("۳۰ روز", callback_data="add_days_30"), InlineKeyboardButton("۶۰ روز", callback_data="add_days_60"), InlineKeyboardButton("۹۰ روز", callback_data="add_days_90")],
            [InlineKeyboardButton("بازگشت", callback_data="cancel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(f"⏰ زمان اکانت `{context.user_data['renew_username']}` را انتخاب کنید:", reply_markup=reply_markup, parse_mode="Markdown")
        return ASK_RENEW_VALUE

    elif query.data == "renew_volume":
        context.user_data["renew_action"] = "renew_volume"
        keyboard = [
            [InlineKeyboardButton("5GB", callback_data="add_gb_5"), InlineKeyboardButton("10GB", callback_data="add_gb_10"), InlineKeyboardButton("20GB", callback_data="add_gb_20")],
            [InlineKeyboardButton("50GB", callback_data="add_gb_50"), InlineKeyboardButton("100GB", callback_data="add_gb_100")],
            [InlineKeyboardButton("بازگشت", callback_data="cancel")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(f"📊 حجم اکانت `{context.user_data['renew_username']}` را انتخاب کنید:", reply_markup=reply_markup, parse_mode="Markdown")
        return ASK_RENEW_VALUE
    
    # NEW: Handle "No" button correctly which ends the conversation
    elif query.data == "end_extend":
        return await end_extend_handler(update, context)

    # Handle cancel button correctly
    elif query.data == "cancel":
        await query.message.reply_text("✅ عملیات تمدید لغو شد.")
        return ConversationHandler.END
        
    return ConversationHandler.END



async def end_extend_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    username = context.user_data.get("renew_username", "نامشخص")
    added_days = context.user_data.get("added_days", 0)
    added_gb = context.user_data.get("added_gb", 0)

    summary = f"✅ عملیات تمدید برای `{username}` به پایان رسید.\n"
    if added_days:
        summary += f"🕒 تمدید زمان: +{added_days} روز\n"
    if added_gb:
        summary += f"📶 تمدید حجم: +{added_gb} GB\n"
    if not added_days and not added_gb:
        summary += "ℹ️ هیچ تغییری اعمال نشد."

    await update.callback_query.message.reply_text(summary, parse_mode="Markdown")

    # NEW: Clear user data to prevent future conflicts
    context.user_data.clear()
    
    return ConversationHandler.END


# ---------- create / delete / lock / unlock / listing handlers ----------

async def make_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END
    username = context.user_data.get("username","").strip()
    if not username:
        await query.message.reply_text("❌ یوزرنیم یافت نشد.")
        return ConversationHandler.END
    # avoid system users
    uid_check = subprocess.getoutput(f"id -u {username}")
    if uid_check.isdigit() and int(uid_check) < 1000:
        await query.message.reply_text("⛔️ نام کاربری سیستمی است.")
        return ConversationHandler.END

    password = random_str(12)
    acc_type = context.user_data.get("acc_type","unlimited")
    
    # NEW: Get the volume directly in KB from user_data
    # No need to multiply by 1024 again, as it was already done in handle_volume_input
    limit_kb = int(context.user_data.get("volume", 0))  
    
    period = query.data.replace("expire_","")
    if period.endswith("h"):
        delta = timedelta(hours=int(period.replace("h","")))
        expire_date = datetime.now() + delta
        period_str = "۲ ساعته تستی"
    else:
        days = int(period.replace("d",""))
        expire_date = datetime.now() + timedelta(days=days)
        period_str = f"{days} روزه"
    expire_str = expire_date.strftime("%Y-%m-%d %H:%M")
    try:
        # ensure not exists
        check_user = subprocess.getoutput(f"id -u {username} 2>/dev/null")
        if check_user.isdigit():
            await query.message.reply_text("❌ این یوزرنیم قبلاً وجود دارد.")
            return ConversationHandler.END
        # create user without home and with nologin shell
        subprocess.run(["sudo","useradd","-M","-s",NOLOGIN_PATH,username], check=True)
        # set password
        subprocess.run(["sudo","chpasswd"], input=f"{username}:{password}".encode(), check=True)
        # set expiration
        subprocess.run(["sudo","chage","-E",expire_date.strftime("%Y-%m-%d"), username], check=True)
        # add iptables rule
        uid = subprocess.getoutput(f"id -u {username}").strip()
        subprocess.run(["sudo","iptables","-C","SSH_USERS","-m","owner","--uid-owner",uid,"-j","ACCEPT"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["sudo","iptables","-A","SSH_USERS","-m","owner","--uid-owner",uid,"-j","ACCEPT"], check=False)
        # create limits file if limited
        if acc_type == "limited":
            limits_dir = Path("/etc/sshmanager/limits")
            limits_dir.mkdir(parents=True, exist_ok=True)
            limit_file = limits_dir / f"{username}.json"
            data = {
                "limit": limit_kb,
                "used": 0,
                "type": "limited",
                "expire": expire_str,
                "expire_timestamp": int(expire_date.timestamp()),
                "start_timestamp": int(datetime.now().timestamp()),
                "is_blocked": False,
                "block_reason": None
            }
            with open(limit_file,"w") as f:
                json.dump(data,f)
        await query.message.reply_text(f"✅ اکانت ساخته شد ({period_str}):\n\n{format_config(username,password,expire_str)}")
    except subprocess.CalledProcessError as e:
        await query.message.reply_text(f"❌ خطا در ساخت اکانت:\n{e}")
    except Exception as e:
        await query.message.reply_text(f"❌ خطای پیش‌بینی‌نشده:\n{e}")
    return ConversationHandler.END

#تابع حذف جدید جایگزین شد
#async def delete_user_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    #await update.callback_query.answer()
    #if update.effective_user.id != ADMIN_ID:
        #return
    #await update.callback_query.message.reply_text("لطفاً نام کاربری را برای حذف وارد کنید:")
    #context.user_data["awaiting_delete"] = True


# تابع جدید برای شروع مکالمه حذف اکانت
async def start_delete_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.callback_query.answer()
        return ConversationHandler.END
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("❗️ لطفا نام کاربری را برای حذف وارد کنید:")
    return ASK_DELETE_USERNAME

# تابع جدید برای مدیریت حذف اکانت
async def handle_delete_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip()
    try:
        # Check if user exists and is not a system user
        uid = int(subprocess.getoutput(f"id -u {username}").strip())
        if uid < 1000:
            await update.message.reply_text("⛔️ این کاربر سیستمی است و حذف نمی‌شود.")
            return ConversationHandler.END
    except Exception:
        await update.message.reply_text("❌ کاربر یافت نشد.")
        return ConversationHandler.END
    
    try:
        # NEW: Kill all active sessions before deleting
        subprocess.run(["sudo", "pkill", "-u", username], check=False)
        
        # Delete user
        subprocess.run(["sudo","userdel","-f",username], check=True)
        # Delete user's limit file
        limit_file_path = f"/etc/sshmanager/limits/{username}.json"
        if os.path.exists(limit_file_path):
            os.remove(limit_file_path)

        await update.message.reply_text(f"✅ اکانت `{username}` حذف شد.", parse_mode="Markdown", reply_markup=main_menu_keyboard)

    except Exception as e:
        await update.message.reply_text(f"❌ حذف با خطا مواجه شد:\n`{e}`", parse_mode="Markdown")

    return ConversationHandler.END




#async def ask_user_to_lock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    #await update.callback_query.answer()
    #if update.effective_user.id != ADMIN_ID:
        #return
    #context.user_data["awaiting_lock"] = True
    #await update.callback_query.message.reply_text("🛑 نام کاربری را برای *قفل کردن* وارد کنید:", parse_mode="Markdown")


#تابع جدید قفل کردن کاربر
async def start_lock_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("🔒 لطفاً *نام کاربری* که می‌خواهید قفل کنید را وارد کنید:", parse_mode="Markdown")
    return ASK_DELETE_USERNAME



#ادامه تابع جدید قفل کاربر
async def handle_lock_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # این تابع پیامِ یوزرنیم را پس از start_lock_user دریافت و پردازش می‌کند
    username = update.message.text.strip()
    try:
        # بررسی وجود کاربر و جلوگیری از قفل کاربران سیستمی
        uid_str = subprocess.getoutput(f"id -u {username}").strip()
        if not uid_str.isdigit():
            await update.message.reply_text("❌ کاربر یافت نشد.")
            return ConversationHandler.END
        uid = int(uid_str)
        if uid < 1000:
            await update.message.reply_text("⛔️ این کاربر سیستمی است و قفل نخواهد شد.")
            return ConversationHandler.END
    except Exception:
        await update.message.reply_text("❌ خطا در بررسی کاربر.")
        return ConversationHandler.END

    try:
        # فراخوانی lock_user.py با دلیل manual
        # توجه: اگر اسکریپت نیاز به sudo دارد، مطمئن شو اسکریپت به عنوان root اجرا می‌شود یا sudoers تنظیم شده
        proc = subprocess.run(["python3", "/root/sshmanager/lock_user.py", username, "manual"], check=False)
        if proc.returncode != 0:
            await update.message.reply_text(f"❌ خطا در اجرای اسکریپت قفل (returncode={proc.returncode}).")
            return ConversationHandler.END

        # همگام‌سازی JSON (در صورتی که اسکریپت قبلاً آپدیت نکرده باشد)
        limit_file_path = f"/etc/sshmanager/limits/{username}.json"
        if os.path.exists(limit_file_path):
            try:
                with open(limit_file_path, "r") as f:
                    user_data = json.load(f)
            except Exception:
                user_data = {}

            user_data["is_blocked"] = True
            user_data["block_reason"] = "manual"
            # (اختیاری) ثبت زمان بلاک
            from datetime import datetime
            user_data["blocked_at"] = int(datetime.now().timestamp())

            try:
                with open(limit_file_path, "w") as f:
                    json.dump(user_data, f, indent=4)
            except Exception:
                pass

        await update.message.reply_text(f"🔒 اکانت `{username}` با موفقیت قفل شد.", parse_mode="Markdown", reply_markup=main_menu_keyboard)
    except Exception as e:
        await update.message.reply_text(f"❌ خطا هنگام قفل‌کردن اکانت:\n`{e}`", parse_mode="Markdown")

    return ConversationHandler.END
#تابع جدید انلاک کردن کاربر


async def start_unlock_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.callback_query.answer()
        return ConversationHandler.END
    await update.callback_query.answer()
    await update.callback_query.message.reply_text("🔓 لطفا نام کاربری را برای باز کردن اکانت وارد کنید:")
    return ASK_UNLOCK_USERNAME

async def handle_unlock_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip()
    try:
        # Check if user exists and is not a system user
        uid = int(subprocess.getoutput(f"id -u {username}").strip())
        if uid < 1000:
            await update.message.reply_text("⛔️ این کاربر سیستمی است و نمی‌توان آن را باز کرد.")
            return ConversationHandler.END
    except Exception:
        await update.message.reply_text("❌ کاربر یافت نشد.")
        return ConversationHandler.END

    try:
        # Check if the user is a limited/blocked user
        limit_file_path = f"/etc/sshmanager/limits/{username}.json"
        is_blocked = False
        if os.path.exists(limit_file_path):
            with open(limit_file_path, "r") as f:
                user_data = json.load(f)
            is_blocked = user_data.get("is_blocked", False)

        if not is_blocked:
            await update.message.reply_text("⚠️ اکانت قفل نیست.")
            return ConversationHandler.END

        # 🔓 Unlock the user (فقط تونل، بدون لاگین مستقیم)
        subprocess.run(["sudo", "usermod", "-s", "/usr/sbin/nologin", username], check=False)  # شل بدون دسترسی
        subprocess.run(["sudo", "usermod", "-d", "/nonexistent", username], check=False)       # مسیر هوم غیرواقعی
        subprocess.run(["sudo", "passwd", "-u", username], check=False)                        # باز کردن پسورد
        subprocess.run(["sudo", "chage", "-E", "-1", username], check=False)                   # حذف تاریخ انقضا

        # ✅ بازگرداندن دسترسی iptables
        uid = subprocess.getoutput(f"id -u {username}").strip()
        if uid.isdigit():
            subprocess.run(["sudo", "iptables", "-D", "SSH_USERS", "-m", "owner", "--uid-owner", uid, "-j", "ACCEPT"], check=False)
            subprocess.run(["sudo", "iptables", "-A", "SSH_USERS", "-m", "owner", "--uid-owner", uid, "-j", "ACCEPT"], check=False)

        # Update the JSON file
        user_data["is_blocked"] = False
        user_data["block_reason"] = None
        with open(limit_file_path, "w") as f:
            json.dump(user_data, f, indent=4)
        
        await update.message.reply_text(
            f"✅ اکانت `{username}` با موفقیت باز شد.",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard
        )

    except Exception as e:
        await update.message.reply_text(
            f"❌ باز کردن اکانت با خطا مواجه شد:\n`{e}`",
            parse_mode="Markdown"
        )

    return ConversationHandler.END

async def show_limited_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    if update.effective_user.id != ADMIN_ID:
        return
    limits_dir = Path("/etc/sshmanager/limits")
    if not limits_dir.exists():
        await update.callback_query.message.reply_text("❌ پوشه محدودیت پیدا نشد.")
        return
    msg = "📊 لیست کاربران حجمی:\n\n"
    found = False
    for file in limits_dir.glob("*.json"):
        try:
            with open(file) as f:
                data = json.load(f)
            username = file.stem
            if data.get("type") != "limited":
                continue
            used = int(data.get("used",0))
            limit = int(data.get("limit",1))
            percent = (used / limit) * 100 if limit > 0 else 0
            
            # Formatting used and limit values to be more readable (e.g., KB to MB)
            used_mb = used // 1024
            limit_mb = limit // 1024

            expire_text = ""
            if data.get("expire_timestamp"):
                days_left = (int(data["expire_timestamp"]) - int(datetime.now().timestamp())) // 86400
                expire_text = f" | ⏳ {days_left} روز مانده" if days_left >= 0 else " | ⌛ منقضی‌شده"
            
            # Using appropriate emoji for the status
            if percent >= 100: 
                emoji = "🔴"
            elif percent >= 90:
                emoji = "🟠"
            else: 
                emoji = "🟢"

            msg += f"{emoji} `{username}` → {used_mb}MB / {limit_mb}MB ({percent:.0f}٪){expire_text}\n"
            found = True
        except Exception:
            log.exception("reading limit file failed")
    if not found:
        msg = "⚠️ هیچ کاربر حجمی پیدا نشد."
    await update.callback_query.message.reply_text(msg, parse_mode="Markdown")

#مشاهده کاربران مسدود 
async def show_blocked_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.callback_query.answer()
        return
    await update.callback_query.answer("در حال دریافت لیست کاربران مسدود شده...")

    blocked_users = []
    limits_dir = "/etc/sshmanager/limits"

    if not os.path.exists(limits_dir):
        await update.callback_query.message.reply_text("❌ پوشه محدودیت پیدا نشد.")
        return

    for file in os.listdir(limits_dir):
        if file.endswith(".json"):
            file_path = os.path.join(limits_dir, file)
            try:
                with open(file_path, "r") as f:
                    user_data = json.load(f)
                
                # Check the is_blocked flag
                if user_data.get("is_blocked", False):
                    username = file.replace(".json", "")
                    reason = user_data.get("block_reason", "unknown")
                    blocked_users.append(f"{username} ({reason})")
            except (json.JSONDecodeError, FileNotFoundError):
                continue

    if not blocked_users:
        message = "❗️ هیچ کاربر مسدودی یافت نشد."
    else:
        message = "✅ لیست کاربران مسدودشده:\n\n"
        for user in blocked_users:
            message += f"🔒 {user}\n"

    # NEW: Removed reply_markup to prevent old keyboard from reappearing
    await update.callback_query.message.reply_text(message, parse_mode="Markdown")


# unified text handler for awaiting actions or quick menu commands
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    text = update.message.text.strip()

    # menu commands
    if text == "📊 وضعیت سیستم":
        await update.message.reply_text(get_system_stats(), reply_markup=main_menu_keyboard)
    elif text == "🛡 بررسی سلامت سرور":
        await update.message.reply_text("✅ ربات در حال اجراست.", reply_markup=main_menu_keyboard)
    elif text == "🔎 بررسی پورت و دامنه":
        await update.message.reply_text(check_ports_and_ping(), reply_markup=main_menu_keyboard)
    elif text == "⚠ فایل‌های مشکوک":
        await update.message.reply_text(find_suspicious_files(), reply_markup=main_menu_keyboard)
    elif text == "📋 لیست کاربران":
        users = list_real_users()
        await update.message.reply_text("\n".join(users) or "هیچ کاربری یافت نشد.", reply_markup=main_menu_keyboard)
    elif text == "📉 مصرف کاربران":
        # show basic usage for all users
        report = []
        for u in list_real_users():
            used_kb = get_user_traffic(u)
            report.append(f"{u}: {used_kb//1024}MB")
        await update.message.reply_text("\n".join(report) or "هیچ کاربری یافت نشد.", reply_markup=main_menu_keyboard)
    elif text == "بازگشت به منو":
        await update.message.reply_text("↩ بازگشت به منوی اصلی", reply_markup=main_menu_keyboard)

# ---------- reporting helper ----------
async def report_all_users_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("در حال آماده‌سازی گزارش...")
    if update.effective_user.id != ADMIN_ID:
        return

    report_text = "📊 گزارش کلی کاربران\n\n"
    limits_dir = "/etc/sshmanager/limits"

    if not os.path.exists(limits_dir):
        await query.message.reply_text("❌ پوشه محدودیت پیدا نشد.", reply_markup=main_menu_keyboard)
        return

    # Fetch users from the JSON files, not system commands
    for file in os.listdir(limits_dir):
        if file.endswith(".json"):
            file_path = os.path.join(limits_dir, file)
            try:
                with open(file_path, "r") as f:
                    user_data = json.load(f)

                username = file.replace(".json", "")
                
                # Fetching user status (blocked or active)
                if user_data.get("is_blocked", False):
                    reason_map = {
                        "manual": "دستی",
                        "quota": "حجمی",
                        "expire": "انقضا"
                    }
                    reason = reason_map.get(user_data.get("block_reason"), "نامشخص")
                    status = f"🔒 مسدود ({reason})"
                else:
                    status = "✅ فعال"

                # NEW: Correctly fetch and format usage from KB to MB/GB
                used_kb = int(user_data.get("used", 0))
                limit_kb = int(user_data.get("limit", 0))

                if limit_kb > 0:
                    used_mb = used_kb / 1024
                    limit_mb = limit_kb / 1024

                    if limit_mb > 1024:
                        used_gb = used_mb / 1024
                        limit_gb = limit_mb / 1024
                        usage_text = f"📶 {used_gb:.2f}GB / {limit_gb:.2f}GB"
                    else:
                        usage_text = f"📶 {used_mb:.2f}MB / {limit_mb:.2f}MB"
                    
                    usage_percent = (used_kb / limit_kb) * 100
                    usage_text += f" ({usage_percent:.0f}%)"
                else:
                    usage_text = "📶 نامحدود"

                # Fetching and formatting expiration date
                expire_timestamp = user_data.get("expire_timestamp")
                if expire_timestamp:
                    expire_date = datetime.fromtimestamp(expire_timestamp).strftime("%Y-%m-%d")
                    days_left = (datetime.fromtimestamp(expire_timestamp) - datetime.now()).days
                    if days_left >= 0:
                        expire_text = f"⏳ {expire_date} ({days_left} روز مانده)"
                    else:
                        expire_text = f"⌛ منقضی‌شده ({expire_date})"
                else:
                    expire_text = "⏳ نامحدود"

                report_text += (
                    f"👤 `{username}`\n"
                    f"وضعیت: {status}\n"
                    f"مصرف: {usage_text}\n"
                    f"انقضا: {expire_text}\n"
                    f"--------------------\n"
                )

            except (json.JSONDecodeError, FileNotFoundError):
                continue
    
    if len(report_text.splitlines()) > 2: # Checks if any user was found
        await query.message.reply_text(report_text, parse_mode="Markdown", reply_markup=main_menu_keyboard)
    else:
        await query.message.reply_text("❌ هیچ کاربری برای گزارش یافت نشد.", reply_markup=main_menu_keyboard)


# Before def run_bot():
async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels a conversation."""
    if update.effective_message:
        await update.effective_message.reply_text("❌ عملیات لغو شد.", reply_markup=main_menu_keyboard)
    return ConversationHandler.END
# ---------- run ----------
def run_bot():
    if not BOT_TOKEN:
        log.error("BOT_TOKEN not set. Export SSH_MANAGER_BOT_TOKEN")
        raise SystemExit("Set SSH_MANAGER_BOT_TOKEN")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # تعریف ConversationHandlerها (این بخش دست نخورده باقی می‌ماند)
    conv_create = ConversationHandler(
        entry_points=[CallbackQueryHandler(ask_username, pattern="^create_user$")],
        states={
            ASK_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_account_type)],
            ASK_TYPE: [CallbackQueryHandler(handle_account_type, pattern="^acc_type_")],
            ASK_VOLUME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_volume_input)],
            ASK_EXPIRE: [CallbackQueryHandler(make_account, pattern="^expire_\\d+[hd]$")]
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    )

    conv_extend = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_extend_user, pattern="^extend_user$")],
        states={
            ASK_RENEW_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_extend_username)],
            ASK_RENEW_ACTION: [CallbackQueryHandler(handle_extend_action, pattern="^renew_")],
            ASK_RENEW_VALUE: [CallbackQueryHandler(handle_extend_value, pattern="^(add_days_|add_gb_)")],
            ASK_ANOTHER_RENEW: [CallbackQueryHandler(handle_renew_another_action, pattern="^(renew_volume|renew_time|end_extend|cancel)$")]
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation), CallbackQueryHandler(end_extend_handler, pattern="^end_extend$")],
    )


    conv_delete = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_delete_user, pattern="^delete_user$")],
        states={
            ASK_DELETE_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_delete_input)]
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    )

    conv_unlock = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_unlock_user, pattern="^unlock_user$")],
        states={
            ASK_UNLOCK_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_unlock_input)]
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    )

    #جدید
    conv_lock = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_lock_user, pattern="^lock_user$")],
        states={
            ASK_DELETE_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_lock_input)]
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    )
    
    # اضافه کردن Handlers به ترتیب صحیح:
    # 1. تمام ConversationHandlerها را ابتدا اضافه کنید.
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start ))
    app.add_handler(conv_create)
    app.add_handler(conv_extend)
    app.add_handler(conv_delete)
    app.add_handler(conv_unlock)
    app.add_handler(conv_lock)

    # 2. CallbackQueryHandlerهای غیر مکالمه‌ای را اضافه کنید.
    app.add_handler(CallbackQueryHandler(show_limited_users, pattern="^show_limited$"))
    app.add_handler(CallbackQueryHandler(show_blocked_users, pattern="^show_blocked$"))
    app.add_handler(CallbackQueryHandler(report_all_users_callback, pattern="^report_users$"))
    
    # 3. MessageHandlerهای متنی را در انتها اضافه کنید تا با مکالمه تداخل نداشته باشند.
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_lock_input))  # for lock flow
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))       # general
    
    app.run_polling()

if __name__ == "__main__":
    run_bot()
#EOF
                    
