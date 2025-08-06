cat > /root/sshmanager.py << 'EOF'
import os
import subprocess
#import datetime
import random
import string
import psutil
import socket
import time
import json
import traceback
from datetime import datetime, timedelta
from sshmanager.lock_user import lock_user
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

BOT_TOKEN = "توکن بات"
ADMIN_ID = ایدی عددی
PORT_PUBLIC = 443
DOMAIN = "ssh.ultraspeed.shop"
NOLOGIN_PATH = "/usr/sbin/nologin"
FIX_IPTABLES_SCRIPT = "/root/fix-iptables.sh"

ASK_USERNAME, ASK_TYPE, ASK_VOLUME, ASK_EXPIRE = range(4)

# Stateهای مکالمه تمدید
ASK_RENEW_USERNAME, ASK_RENEW_ACTION, ASK_RENEW_TYPE, ASK_RENEW_VALUE = range(4, 8)

main_menu_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        ["📊 وضعیت سیستم", "🛡 بررسی سلامت سرور"],
        ["🔎 بررسی پورت و دامنه", "⚠ فایل‌های مشکوک"],
        ["📋 لیست کاربران", "📉 مصرف کاربران"],
        ["بازگشت به منو"]
    ],
    resize_keyboard=True
)

def random_str(length=10):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))


def lock_user_account(username: str) -> bool:
    try:
        subprocess.run(["sudo", "passwd", "-l", username], check=True)
        subprocess.run(["sudo", "usermod", "-s", "/usr/sbin/nologin", username], check=True)
        return True
    except Exception as e:
        print(f"[!] خطا در lock_user_account: {e}")
        return False

def remove_user_iptables_rule(username):
    try:
        uid = int(subprocess.getoutput(f"id -u {username}").strip())
        subprocess.run(
            ["sudo", "iptables", "-D", "SSH_USERS", "-m", "owner", "--uid-owner", str(uid), "-j", "ACCEPT"],
            check=False
        )
    except Exception as e:
        print(f"[!] خطا در حذف rule از iptables برای {username}: {e}")


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

def get_user_data_usage(username):
    try:
        uid = int(subprocess.getoutput(f"id -u {username}").strip())
        output = subprocess.getoutput("iptables -L SSH_USERS -v -n -x")
        lines = output.strip().split('\n')

        for i in range(len(lines)):
            if f"UID match {uid}" in lines[i]:
                if i > 0:
                    parts = lines[i - 1].split()
                    if len(parts) >= 2:
                        packets = int(parts[0])
                        bytes_sent = int(parts[1])
                        mb = int(bytes_sent / (1024 * 1024))
                        return f"{mb} MB - {packets} پکت"
                return "0 MB - 0 پکت"
        return "بدون ترافیک ثبت‌شده"
    except Exception:
        return "❌ خطا در محاسبه ترافیک"

def get_all_users_usage():
    users = list_real_users()
    if not users:
        return "هیچ کاربری یافت نشد."
    report = ["📉 مصرف کاربران:"]
    for u in users:
        usage = get_user_data_usage(u)
        report.append(f"{u}: {usage}")
    return "\n".join(report)
    
    # -------------------- HANDLERS --------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return await update.message.reply_text("❌ دسترسی غیرمجاز")

    fix_iptables()  # اجرای اسکریپت اصلاح iptables در شروع

    keyboard = [
        [InlineKeyboardButton("✅️ ساخت اکانت SSH", callback_data="create_user")],
        [InlineKeyboardButton("❌️ حذف اکانت", callback_data="delete_user")],
        [
            InlineKeyboardButton("🔒 قفل‌کردن اکانت", callback_data="lock_user"),
            InlineKeyboardButton("🔓 بازکردن اکانت", callback_data="unlock_user")
        ],
        [InlineKeyboardButton("📊 کاربران حجمی", callback_data="show_limited")],
        [InlineKeyboardButton("🚫 کاربران مسدود", callback_data="show_blocked")],
        [InlineKeyboardButton("⏳ تمدید اکانت", callback_data="extend_user")]
    ]

    await update.message.reply_text("📲 پنل مدیریت SSH:", reply_markup=InlineKeyboardMarkup(keyboard))

async def ask_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    if update.effective_user.id != ADMIN_ID:
        return
    await update.callback_query.message.reply_text("❗️ لطفاً یوزرنیم وارد شود:")
    return ASK_USERNAME
    
#codejadid

async def ask_account_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['username'] = update.message.text.strip()
    keyboard = [
        [InlineKeyboardButton("📦 حجمی", callback_data="acc_type_limited")],
        [InlineKeyboardButton("♾ نامحدود", callback_data="acc_type_unlimited")]
    ]
    await update.message.reply_text("📘 چه نوع اکانتی می‌خواهید بسازید؟", reply_markup=InlineKeyboardMarkup(keyboard))
    return ASK_TYPE

#codejadid

async def handle_account_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    acc_type = query.data.replace("acc_type_", "")
    context.user_data["acc_type"] = acc_type

    if acc_type == "limited":
        await query.message.reply_text("📏 چه محدودیتی برای حجم تنظیم کنیم؟\nمثلاً: `30MB` یا `1.5GB`", parse_mode="Markdown")
        return ASK_VOLUME
    else:
        return await ask_expire(query, context)
        

#codejadid

async def handle_volume_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper()
    volume = 0

    if text.endswith("GB"):
        try:
            volume = int(float(text[:-2].strip()) * 1024)
        except:
            pass
    elif text.endswith("MB"):
        try:
            volume = int(float(text[:-2].strip()))
        except:
            pass
    else:
        try:
            volume = int(float(text) * 1024)
        except:
            pass

    if volume <= 0:
        await update.message.reply_text("❌ حجم واردشده نامعتبر است. لطفاً مانند `30MB` یا `1.5GB` وارد کنید.")
        return ASK_VOLUME

    context.user_data["volume"] = volume
    return await ask_expire(update, context)
    
    


async def ask_expire(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['username'] = update.message.text.strip()
    keyboard = [
        [InlineKeyboardButton("⌛️ یک ماهه", callback_data="expire_30d")],
        [InlineKeyboardButton("⏳️ دو ماهه", callback_data="expire_60d")],
        [InlineKeyboardButton("⏳️ سه ماهه", callback_data="expire_90d")]
    ]
    await update.message.reply_text("⏱️ لطفاً مدت انتخاب شود:", reply_markup=InlineKeyboardMarkup(keyboard))
    return ASK_EXPIRE
    
#وقتی روی دکمه تمدید کلیک شد یوزرنیمو از ادمین بخواد

async def start_extend_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    await update.callback_query.message.reply_text(
        "📋 لطفاً *نام کاربری* که می‌خواهید تمدید کنید را وارد کنید:",
        parse_mode="Markdown"
    )
    return ASK_RENEW_USERNAME
    

#دریافت یوزرنیم و نمایش دکمه های تمدید زمان و تمدید حجم 

async def handle_extend_username(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return ConversationHandler.END

    username = update.message.text.strip()
    context.user_data["renew_username"] = username

    # بررسی وجود کاربر (اختیاری ولی پیشنهادی)
    check = subprocess.getoutput(f"id -u {username}")
    if not check.isdigit():
        await update.message.reply_text("❌ این یوزرنیم وجود ندارد.")
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton("🕒 تمدید زمان", callback_data="renew_time")],
        [InlineKeyboardButton("📶 تمدید حجم", callback_data="renew_volume")]
    ]
    await update.message.reply_text(
        f"اکانت `{username}` انتخاب شد. لطفاً نوع تمدید را انتخاب کنید:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )
    return ASK_RENEW_ACTION
    
    
#تابع ی که در پاسخ به انتخاب نوع تمدید فراخوانی میشه

async def handle_extend_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END

    action = query.data  # renew_time یا renew_volume
    context.user_data["renew_action"] = action

    username = context.user_data.get("renew_username", "")

    if action == "renew_time":
        keyboard = [
            [InlineKeyboardButton("1️⃣ یک‌ماهه", callback_data="add_days_30")],
            [InlineKeyboardButton("2️⃣ دو‌ماهه", callback_data="add_days_60")],
            [InlineKeyboardButton("3️⃣ سه‌ماهه", callback_data="add_days_90")]
        ]
        await query.message.reply_text(
            f"📆 لطفاً مدت زمانی که می‌خواهید به `{username}` اضافه شود را انتخاب کنید:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ASK_RENEW_VALUE

    elif action == "renew_volume":
        # بررسی حجم فعلی
        limits_file = f"/etc/sshmanager/limits/{username}.json"
        current_volume = "نامشخص"

        if os.path.exists(limits_file):
            try:
                with open(limits_file) as f:
                    data = json.load(f)
                    used = int(data.get("used", 0))
                    limit = int(data.get("limit", 0))
                    current_volume = f"{used}/{limit} MB"
            except:
                pass

        keyboard = [
            [InlineKeyboardButton("10 گیگ", callback_data="add_gb_10")],
            [InlineKeyboardButton("20 گیگ", callback_data="add_gb_20")],
            [InlineKeyboardButton("35 گیگ", callback_data="add_gb_35")],
            [InlineKeyboardButton("50 گیگ", callback_data="add_gb_50")]
        ]
        await query.message.reply_text(
            f"📶 حجم فعلی `{username}`: `{current_volume}`\n\nلطفاً مقدار حجمی که می‌خواهید اضافه شود را انتخاب کنید:",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ASK_RENEW_VALUE

        return ConversationHandler.END
        
        
#هندل نهایی برای تمدید و تغییر در حجم و زمان اکانت

async def handle_extend_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    username = context.user_data.get("renew_username", "")
    action = context.user_data.get("renew_action", "")
    data = query.data
    added_days = 0
    added_gb = 0

    if not username or not action:
        await query.message.reply_text("❌ خطا: اطلاعات تمدید ناقص است.")
        return ConversationHandler.END

    uid = subprocess.getoutput(f"id -u {username}").strip()

    # تمدید زمان
    if action == "renew_time" and data.startswith("add_days_"):
        days = int(data.replace("add_days_", ""))
        added_days = days

        output = subprocess.getoutput(f"chage -l {username}")
        current_exp = ""
        for line in output.splitlines():
            if "Account expires" in line:
                current_exp = line.split(":")[1].strip()
                break

        if current_exp.lower() != "never":
            current_date = datetime.datetime.strptime(current_exp, "%b %d, %Y")
            new_date = current_date + datetime.timedelta(days=days)
        else:
            new_date = datetime.datetime.now() + datetime.timedelta(days=days)

        subprocess.run(["chage", "-E", new_date.strftime("%Y-%m-%d"), username])

        # باز کردن قفل و اضافه‌کردن rule iptables
        subprocess.run(["usermod", "-s", "/bin/bash", username])
        subprocess.run(["passwd", "-u", username])

        # بررسی وجود rule قبلی
        rule_check = subprocess.run(
            ["iptables", "-C", "SSH_USERS", "-m", "owner", "--uid-owner", uid, "-j", "ACCEPT"],
            stderr=subprocess.DEVNULL
        )
        if rule_check.returncode != 0:
            subprocess.run([
                "iptables", "-A", "SSH_USERS", "-m", "owner", "--uid-owner", uid, "-j", "ACCEPT"
            ])

        await query.message.reply_text(f"⏳ {days} روز به تاریخ انقضای `{username}` اضافه شد.", parse_mode="Markdown")

    # ------------------------------------

    # تمدید حجم
    elif action == "renew_volume" and data.startswith("add_gb_"):
        gb = int(data.replace("add_gb_", ""))
        added_gb = gb
        limits_file = f"/etc/sshmanager/limits/{username}.json"

        if os.path.exists(limits_file):
            with open(limits_file) as f:
                d = json.load(f)
            d["limit"] = int(d.get("limit", 0)) + (gb * 1024)
            with open(limits_file, "w") as f:
                json.dump(d, f)

            subprocess.run(["usermod", "-s", "/bin/bash", username])
            subprocess.run(["passwd", "-u", username])

            rule_check = subprocess.run(
                ["iptables", "-C", "SSH_USERS", "-m", "owner", "--uid-owner", uid, "-j", "ACCEPT"],
                stderr=subprocess.DEVNULL
            )
            if rule_check.returncode != 0:
                subprocess.run([
                    "iptables", "-A", "SSH_USERS", "-m", "owner", "--uid-owner", uid, "-j", "ACCEPT"
                ])

            await query.message.reply_text(
                f"📶 حجم اکانت `{username}` به مقدار {gb}GB افزایش یافت.",
                parse_mode="Markdown"
            )
        else:
            await query.message.reply_text("❌ فایل محدودیت پیدا نشد.")

    # 🔁 پیشنهاد ادامه تمدید
    if added_days > 0 and added_gb == 0:
        keyboard = [[
            InlineKeyboardButton("➕ تمدید حجم", callback_data="renew_volume"),
            InlineKeyboardButton("❌ خیر، پایان", callback_data="end_extend")
        ]]
        await query.message.reply_text("آیا می‌خواهید *حجم* این کاربر را هم افزایش دهید؟", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif added_gb > 0 and added_days == 0:
        keyboard = [[
            InlineKeyboardButton("➕ تمدید زمان", callback_data="renew_time"),
            InlineKeyboardButton("❌ خیر، پایان", callback_data="end_extend")
        ]]
        await query.message.reply_text("آیا می‌خواهید *زمان* این کاربر را هم افزایش دهید؟", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif added_days > 0 and added_gb > 0:
        await query.message.reply_text(
            f"✅ تمدید با موفقیت انجام شد:\n\n"
            f"👤 کاربر: `{username}`\n"
            f"🕒 +{added_days} روز\n"
            f"📶 +{added_gb}GB",
            parse_mode="Markdown"
        )

    return ConversationHandler.END


# تابع پایان عملیات تمدید
async def end_extend_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    username = context.user_data.get("renew_username", "نامشخص")
    await update.callback_query.message.reply_text(f"✅ عملیات تمدید برای `{username}` به پایان رسید.", parse_mode="Markdown")
    return ConversationHandler.END
    
    

#کد_ساخت_اکانت


async def make_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != ADMIN_ID:
        return ConversationHandler.END

    username = context.user_data["username"]
    password = random_str()
    acc_type = context.user_data.get("acc_type", "unlimited")
    volume = context.user_data.get("volume", 0)
    period = query.data.replace("expire_", "")

    # محاسبه تاریخ انقضا
    if period.endswith("h"):
        delta = datetime.timedelta(hours=int(period.replace("h", "")))
        expire_date = datetime.datetime.now() + delta
        period_str = "۲ ساعته تستی"
    else:
        days = int(period.replace("d", ""))
        #اگر دیدی باگ نداشت بعدا پاکش کن
        #delta = datetime.timedelta(days=days)
        delta = timedelta(days=days)
        expire_date = datetime.datetime.now() + delta
        period_str = f"{days} روزه"

    expire_str = expire_date.strftime("%Y-%m-%d %H:%M")

    try:
        # بررسی وجود یوزر
        check_user = subprocess.getoutput(f"id -u {username}")
        if check_user.isdigit():
            await query.message.reply_text("❌ این یوزرنیم قبلاً ساخته شده. لطفاً یوزرنیم جدید وارد کنید.")
            return ConversationHandler.END

        # ساخت یوزر بدون home با شل nologin
        subprocess.run(["sudo", "useradd", "-M", "-s", NOLOGIN_PATH, username], check=True)

        # تعیین رمز
        subprocess.run(["sudo", "chpasswd"], input=f"{username}:{password}".encode(), check=True)

        # تنظیم تاریخ انقضا
        subprocess.run(["sudo", "chage", "-E", expire_date.strftime("%Y-%m-%d"), username], check=True)

        # افزودن به iptables
        uid = subprocess.getoutput(f"id -u {username}").strip()
        subprocess.run(["sudo", "iptables", "-C", "SSH_USERS", "-m", "owner", "--uid-owner", uid, "-j", "ACCEPT"],
                       stderr=subprocess.DEVNULL)
        subprocess.run(["sudo", "iptables", "-A", "SSH_USERS", "-m", "owner", "--uid-owner", uid, "-j", "ACCEPT"],
                       check=True)

        # ساخت فایل محدودیت اگر اکانت حجمی بود
        if acc_type == "limited":
            limits_dir = Path("/etc/sshmanager/limits")
            limits_dir.mkdir(parents=True, exist_ok=True)
            limit_file = limits_dir / f"{username}.json"
            data = {
                "limit": volume,     # MB
                "used": 0,           # در آینده با cron بروزرسانی میشه
                "type": "limited",
                "expire": expire_str
            }
            with limit_file.open("w") as f:
                json.dump(data, f)

        # لاگ ساده
        print(f"[+] اکانت ساخته شد: {username}, UID: {uid}, نوع: {acc_type}, حجم: {volume} MB")

        # پیام موفقیت
        await query.message.reply_text(
            f"✅ اکانت با موفقیت ساخته شد ({period_str}):\n\n{format_config(username, password, expire_str)}"
        )
    except subprocess.CalledProcessError as e:
        await query.message.reply_text(f"❌ خطا در ساخت اکانت یا تنظیم فایروال:\n\n{e}")
    except Exception as e:
        await query.message.reply_text(f"❌ خطای پیش‌بینی‌نشده:\n\n{e}")

    return ConversationHandler.END

#کد_حذف_کاربر

async def delete_user_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    if update.effective_user.id != ADMIN_ID:
        return
    await update.callback_query.message.reply_text("لطفاً نام کاربری را برای حذف وارد کنید:")
    context.user_data["awaiting_delete"] = True

#کد_قفل_کردن_کاربر_به_صورت_دستی

async def ask_user_to_lock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    if update.effective_user.id != ADMIN_ID:
        return
    context.user_data["awaiting_lock"] = True
    await update.callback_query.message.reply_text("🛑 نام کاربری را برای *قفل کردن* وارد کنید:", parse_mode="Markdown")

#تعریف_تابع_پیام_متنی_برای_قفل_کاربر
async def handle_lock_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("awaiting_lock") != True:
        return

    username = update.message.text.strip()
    context.user_data["awaiting_lock"] = False

    # بررسی اینکه یوزر سیستمی نباشد
    if subprocess.getoutput(f"id -u {username}").isdigit():
        uid = int(subprocess.getoutput(f"id -u {username}"))
        if uid < 1000:
            await update.message.reply_text("⛔️ این کاربر سیستمی است و نمی‌توان آن را قفل کرد.")
            return

    # قفل کردن کاربر
    success = lock_user_account(username)
    if success:
        await update.message.reply_text(f"🔒 اکانت `{username}` قفل شد.", parse_mode="Markdown")
    else:
        await update.message.reply_text("❌ خطا در قفل‌کردن اکانت.")
    

#کد_آنلاک_کردن_کاربر_به_صورت_دستی

async def ask_user_to_unlock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.from_user.id != ADMIN_ID:
        return

    context.user_data["awaiting_unlock"] = True
    await query.message.reply_text("✅ لطفاً *نام کاربری* مورد نظر برای باز کردن قفل را وارد کنید:", parse_mode="Markdown")


#کد_مشاهده_کاربران_حجمی

async def show_limited_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    limits_dir = Path("/etc/sshmanager/limits")
    if not limits_dir.exists():
        await update.callback_query.message.reply_text("❌ پوشه محدودیت پیدا نشد.")
        return

    msg = " لیست کاربران حجمی:\n\n"
    found = False

    for file in limits_dir.glob("*.json"):
        try:
            with file.open() as f:
                data = json.load(f)

            username = file.stem
            if data.get("type") != "limited":
                continue

            used = int(data.get("used", 0))
            limit = int(data.get("limit", 1))  # جلوگیری از تقسیم بر صفر
            percent = int((used / limit) * 100)

            # زمان انقضا (اختیاری)
            expire_text = ""
            if "expire_timestamp" in data:
                expire_ts = int(data["expire_timestamp"])
                now_ts = int(datetime.now().timestamp())
                days_left = (expire_ts - now_ts) // 86400
                if days_left >= 0:
                    expire_text = f" | ⏳ {days_left} روز مانده"
                else:
                    expire_text = " | ⌛ منقضی‌شده"

            # نمایش با رنگ یا ایموجی ویژه
            emoji = "🟢"
            if percent >= 90:
                emoji = "🔴"
            elif percent >= 80:
                emoji = "🟠"
            elif percent >= 60:
                emoji = "🟡"

            msg += f"{emoji} `{username}` → {used}/{limit} KB ({percent}٪){expire_text}\n"
            found = True

        except Exception as e:
            print(f"[!] خطا در خواندن {file}: {e}")

    if not found:
        msg = "⚠️ هیچ کاربر حجمی پیدا نشد."

    await update.callback_query.message.reply_text(msg, parse_mode="Markdown")

#کد_مشاهده_نمایش_مسدودی_ها

async def show_blocked_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    blocked_users = []
    try:
        result = subprocess.getoutput("getent passwd")
        for line in result.splitlines():
            parts = line.split(":")
            if len(parts) >= 7 and parts[6].strip() == "/usr/sbin/nologin":
                username = parts[0]
                if username not in ["nobody"]:  # اگه کاربری سیستمی نباشه
                    blocked_users.append(username)
    except Exception as e:
        return await update.callback_query.message.reply_text(f"❌ خطا در دریافت لیست: {e}")

    if not blocked_users:
        return await update.callback_query.message.reply_text("✅ هیچ کاربر مسدودی وجود ندارد.")
    
    msg = "🚫 لیست کاربران مسدودشده:\n\n" + "\n".join(f"🔒 {u}" for u in blocked_users)
    await update.callback_query.message.reply_text(msg)


#بررسی_و_تکمیل_مرحله_قفل_و_باز_کردن_اکانت_به_صورت_دستی

#def lock_user_account(username):
    #try:
        #subprocess.run(["sudo", "usermod", "-s", "/usr/sbin/nologin", username], check=True)
        #subprocess.run(["sudo", "passwd", "-l", username], check=True)
        #return True
    #except:
        #return False

#هندل_تکس

    
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    text = update.message.text.strip()

    # حذف اکانت
if context.user_data.get("awaiting_delete"):
    username = text

    # ✅ جلوگیری از حذف کاربران سیستمی
    if subprocess.getoutput(f"id -u {username}").isdigit():
        uid = int(subprocess.getoutput(f"id -u {username}"))
        if uid < 1000:
            await update.message.reply_text("⛔️ این کاربر سیستمی است و حذف نمی‌شود.")
            context.user_data["awaiting_delete"] = False
            return

    try:
        subprocess.run(["sudo", "userdel", "-f", username], check=True)
        await update.message.reply_text(f"✅ اکانت `{username}` حذف شد.", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ حذف با خطا مواجه شد:\n`{e}`", parse_mode="Markdown")

    context.user_data["awaiting_delete"] = False
    return

# قفل‌کردن اکانت
    #if context.user_data.get("awaiting_lock"):
        #text = update.message.text.strip()
        #try:
            #success = lock_user_account(text)
            #if success:
                #await update.message.reply_text(f"🔒 اکانت `{text}` با موفقیت قفل شد.", parse_mode="Markdown")
                #await context.bot.send_message(
                    #chat_id=ADMIN_ID,
                    #text=f"📛 اکانت کاربر `{text}` قفل شد.",
                    #parse_mode="Markdown"
                #)
            #else:
                #await update.message.reply_text("❌ خطا در قفل کردن یوزر.")
        #except Exception as e:
            #await update.message.reply_text(f"❌ خطا در قفل کردن یوزر:\n`{e}`", parse_mode="Markdown")
        #context.user_data["awaiting_lock"] = False
        #return



    #باز کردن قفل اکانت
    if context.user_data.get("awaiting_unlock"):
        username = update.message.text.strip()
        context.user_data["awaiting_unlock"] = False

    try:
        # بررسی اینکه کاربر محدود شده نباشه
        limits_file = f"/etc/sshmanager/limits/{username}.json"
        is_restricted = False

        if os.path.exists(limits_file):
            with open(limits_file) as f:
                data = json.load(f)

            # بررسی مصرف حجمی
            limit = int(data.get("limit", 0))
            used = int(data.get("used", 0))
            if limit > 0 and used >= limit:
                is_restricted = True

            # بررسی انقضا
            if "expire_timestamp" in data:
                now = int(datetime.datetime.now().timestamp())
                expire_ts = int(data["expire_timestamp"])
                if now >= expire_ts:
                    is_restricted = True

        if is_restricted:
            await update.message.reply_text(
                f"⚠️ اکانت `{username}` به‌دلیل *اتمام زمان یا حجم* محدود شده است.\n"
                f"برای رفع این محدودیت، از دکمه *تمدید اشتراک* استفاده کن.",
                parse_mode="Markdown"
            )
            return

        # اگر محدود نبود، ادامه می‌دیم به رفع مسدودسازی دستی

        subprocess.run(["sudo", "usermod", "-s", "/bin/bash", username], check=True)
        subprocess.run(["sudo", "passwd", "-u", username], check=True)

        # بررسی rule در iptables
        uid = subprocess.getoutput(f"id -u {username}").strip()
        check_rule = subprocess.run(
            ["iptables", "-C", "SSH_USERS", "-m", "owner", "--uid-owner", uid, "-j", "ACCEPT"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )

        if check_rule.returncode != 0:
            subprocess.run(
                ["iptables", "-A", "SSH_USERS", "-m", "owner", "--uid-owner", uid, "-j", "ACCEPT"],
                check=True
            )

        await update.message.reply_text(
            f"✅ اکانت `{username}` با موفقیت *باز شد*.",
            parse_mode="Markdown"
        )

    except Exception as e:
        await update.message.reply_text(
            f"❌ خطا در فعال‌سازی اکانت:\n`{e}`",
            parse_mode="Markdown"
        )

    # سایر عملیات متنی
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
        await update.message.reply_text(get_all_users_usage(), reply_markup=main_menu_keyboard)
    elif text == "بازگشت به منو":
        await update.message.reply_text("↩ بازگشت به منوی اصلی", reply_markup=main_menu_keyboard)

async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    await update.message.reply_text("📋 منوی اصلی:", reply_markup=main_menu_keyboard)

def fix_iptables():
    try:
        subprocess.run(["sudo", "bash", FIX_IPTABLES_SCRIPT], check=True)
    except Exception as e:
        print(f"[!] خطا در اجرای fix_iptables: {e}")
        
#تابع_اجرای_ربات

def run_bot():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # کانورسیشن ساخت اکانت
    conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(ask_username, pattern="^create_user$")],
        states={
            ASK_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, ask_account_type)],
            ASK_TYPE: [CallbackQueryHandler(handle_account_type, pattern="^acc_type_")],
            ASK_VOLUME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_volume_input)],
            ASK_EXPIRE: [CallbackQueryHandler(make_account, pattern="^expire_\\d+[hd]$")]
        },
        fallbacks=[]
    )

    # کانورسیشن تمدید اکانت
    extend_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_extend_user, pattern="^extend_user$")],
        states={
            ASK_RENEW_USERNAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_extend_username)
            ],
            ASK_RENEW_ACTION: [
                CallbackQueryHandler(handle_extend_action, pattern="^renew_")
            ],
            ASK_RENEW_VALUE: [ 
                CallbackQueryHandler(handle_extend_value, pattern="^add_")
            ],
        },
        fallbacks=[]
    )

    # هندلرهای عمومی و کنترلی
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu))

    app.add_handler(conv_handler)
    app.add_handler(extend_conv)  # کانورسیشن تمدید

    app.add_handler(CallbackQueryHandler(delete_user_handler, pattern="^delete_user$"))
    app.add_handler(CallbackQueryHandler(ask_user_to_lock, pattern="^lock_user$"))
    app.add_handler(CallbackQueryHandler(ask_user_to_unlock, pattern="^unlock_user$"))
    app.add_handler(CallbackQueryHandler(show_limited_users, pattern="^show_limited$"))
    app.add_handler(CallbackQueryHandler(show_blocked_users, pattern="^show_blocked$"))
    app.add_handler(CallbackQueryHandler(start_extend_user, pattern="^extend_user$"))
    app.add_handler(CallbackQueryHandler(end_extend_handler, pattern="^end_extend$"))

    # حواست باشه که هندلر خاص‌تر باید قبل از عمومی ثبت بشه
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_lock_input))  # برای قفل‌کردن
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))  # عمومی‌ترین هندلر

    app.run_polling()

if __name__ == "__main__":
    run_bot()
EOF
