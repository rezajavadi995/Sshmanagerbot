#این نسخه  فعلا باگ  داره ولی درحال اپدیته
#cat > /root/sshmanagerbot.py << 'EOF'
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
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)
from reporting_final import register_reporting_handlers

# ---------- Configuration (use environment variables) ----------
BOT_TOKEN = "8152962391:AAG4kYisE21KI8dAbzFy9oq-rn9h9RCQyBM"
ADMIN_ID = 8062924341
PORT_PUBLIC = 443
DOMAIN = "ssh.ultraspeed.shop"
NOLOGIN_PATH = "/usr/sbin/nologin"
FIX_IPTABLES_SCRIPT = "/root/fix-iptables.sh"
LIMITS_DIR = "/etc/sshmanager/limits"

# ensure directories
Path("/etc/sshmanager/limits").mkdir(parents=True, exist_ok=True)
Path("/etc/sshmanager/logs").mkdir(parents=True, exist_ok=True)

# conversation states
ASK_USERNAME, ASK_TYPE, ASK_VOLUME, ASK_EXPIRE = range(4)
ASK_RENEW_USERNAME, ASK_RENEW_ACTION, ASK_RENEW_VALUE = range(4, 7)
ASK_DELETE_USERNAME = 7
ASK_UNLOCK_USERNAME = 8
ASK_ANOTHER_RENEW = 9


# ---------- utilities ----------
log = logging.getLogger("sshmanager")
logging.basicConfig(level=logging.INFO)
LOCK_SCRIPT = "/root/sshmanager/lock_user.py"
# ---------- Build Application ----------
application = ApplicationBuilder().token(BOT_TOKEN).build()
# ---------- Register Reporting Handlers ----------
register_reporting_handlers(application)

#منو
main_menu_keyboard = InlineKeyboardMarkup([
    [InlineKeyboardButton("✅ ساخت اکانت SSH", callback_data="create_user")],
    [InlineKeyboardButton("❌ حذف اکانت", callback_data="delete_user")],
    [
        InlineKeyboardButton("🔒 قفل‌کردن اکانت", callback_data="lock_user"),
        InlineKeyboardButton("🔓 بازکردن اکانت", callback_data="unlock_user"),
    ],
    [InlineKeyboardButton("📊 کاربران حجمی", callback_data="show_limited")],
    [InlineKeyboardButton("🚫 کاربران مسدود", callback_data="show_blocked")],
    [InlineKeyboardButton("⏳ تمدید اکانت", callback_data="extend_user")],
    [InlineKeyboardButton("🖥 گزارش کاربران", callback_data="report_users")],
])

#

def run_cmd(cmd, timeout=30):
    """
    اجرای دستور سیستم با مدیریت خروجی و خطا
    """
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except subprocess.TimeoutExpired as e:
        return 124, "", f"timeout: {e}"
    except Exception as e:
        log.exception("run_cmd unexpected error: %s", cmd)
        return 1, "", str(e)

def atomic_write(path, data):
    """
    ذخیره امن JSON
    """
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=4)
    os.replace(tmp, path)

#توابع جدید
# --- utils: units, io, math (FINAL) ---

def safe_int(v, default=0):
    try:
        return int(v)
    except Exception:
        return default

def percent_used_kb(used_kb: int, limit_kb: int) -> float:
    if not limit_kb or limit_kb <= 0:
        return 0.0
    try:
        return (float(used_kb) / float(limit_kb)) * 100.0
    except Exception:
        return 0.0

def parse_size_to_kb(text: str) -> int:
    """
    ورودی مانند: '30MB' یا '1.5GB' یا '250' (MB)  -> خروجی KB (int)
    """
    s = text.strip().upper()
    if s.endswith("GB"):
        gb = float(s[:-2].strip())
        return int(gb * 1024 * 1024)
    elif s.endswith("MB"):
        mb = float(s[:-2].strip())
        return int(mb * 1024)
    else:
        # اگر واحد نداشت، بر حسب MB در نظر می‌گیریم
        mb = float(s)
        return int(mb * 1024)

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

####

def update_live_usage(force_run: bool = True) -> None:
    """
    بروزرسانی زنده مصرف را به تنها منبع معتبر واگذار می‌کند:
    /usr/local/bin/log_user_traffic.py
    - از دوبار‌شماری جلوگیری می‌کند
    - فقط ترافیک ACCEPT شمرده می‌شود
    - نوشتن JSON اتمیک است
    """
    try:
        
        subprocess.run(["systemctl", "start", "log-user-traffic.service"], check=False)
        subprocess.run(
            ["/usr/bin/python3", "/usr/local/bin/log_user_traffic.py"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        log.exception("update_live_usage delegation failed")


#  تابع مرتب‌سازی بر اساس زمان ساخت اکانت
def get_sorted_users():
    users_info = subprocess.getoutput(
        "getent passwd | awk -F: '$3 >= 1000 && $1 != \"nobody\" {print $1\":\"$3}'"
    ).splitlines()
    # بر اساس UID مرتب می‌کنیم (به عنوان تقریبی زمان ساخت)
    users_sorted = sorted(users_info, key=lambda x: int(x.split(":")[1]))
    return [u.split(":")[0] for u in users_sorted]

#  تابع ساخت صفحه گزارش
def build_report_page(users, page):
    start, end = page * 10, page * 10 + 10
    page_users = users[start:end]
    report_chunk = ""
    for username in page_users:
        limits_file = os.path.join(LIMITS_DIR, f"{username}.json")
        limit_kb = used_kb = 0
        expire_ts = None
        is_blocked = False
        block_reason = None

        if os.path.exists(limits_file):
            try:
                with open(limits_file, "r") as f:
                    j = json.load(f)
                if isinstance(j, dict):
                    limit_kb = safe_int(j.get("limit", 0))
                    used_kb  = safe_int(j.get("used", 0))
                    expire_ts = j.get("expire_timestamp")
                    is_blocked = bool(j.get("is_blocked", False))
                    block_reason = j.get("block_reason")
            except Exception:
                pass

        if limit_kb > 0:
            pct = percent_used_kb(used_kb, limit_kb)
            usage_str = f"{kb_to_human(used_kb)} / {kb_to_human(limit_kb)} ({pct:.1f}%)"
        else:
            usage_str = "♾ نامحدود"

        expire_str = datetime.fromtimestamp(expire_ts).strftime("%Y-%m-%d") if expire_ts else "—"
        status_str = "🔒" if is_blocked else "✅"
        if is_blocked and block_reason:
            status_str += f" ({block_reason})"

        report_chunk += f"👤 `{username}`\n📊 مصرف: {usage_str}\n⏳ انقضا: {expire_str}\nوضعیت: {status_str}\n\n"

    return report_chunk



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

def lock_user_account(username: str, reason: str = "quota") -> bool:
    """
    Perform a consistent lock for the given username by delegating to LOCK_SCRIPT.
    Returns True on success.
    """
    try:
        # call the external script which already implements locking + json update + iptables delete
        rc, out, err = run_cmd(["python3", LOCK_SCRIPT, username, reason])
        if rc != 0:
            log.warning("lock_user.py failed for %s rc=%s out=%s err=%s", username, rc, out, err)
            return False
        return True
    except Exception:
        log.exception("lock_user_account unexpected error for %s", username)
        return False

def fix_iptables():
    try:
        subprocess.run(["sudo", "bash", FIX_IPTABLES_SCRIPT], check=True)
    except Exception as e:
        log.warning("fix_iptables failed: %s", e)

def format_config(username, password, expire_str):
    return f"""مشخصات اتصال:
🚀 Host: {DOMAIN}
🛸 Port: {PORT_PUBLIC}
👤 Username: {username}
🔑 Password: {password}
🌐 SNI: {DOMAIN}
🗺 TLS: 1.2

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
        return await reply("⛔ لطفا برای خرید به ایدی @UspeedManage پیام بدهید ⛔️")
    fix_iptables()
    Path("/etc/sshmanager/limits").mkdir(parents=True, exist_ok=True)

    await reply("📲 پنل مدیریت SSH:", reply_markup=main_menu_keyboard)

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
    text = (update.message.text or "").strip()
    try:
        volume_kb = parse_size_to_kb(text)
    except Exception:
        await update.message.reply_text("❌ حجم نامعتبر است. مثال: 30MB یا 1.5GB")
        return ASK_VOLUME

    if volume_kb <= 0:
        await update.message.reply_text("❌ حجم باید بزرگتر از صفر باشد.")
        return ASK_VOLUME

    context.user_data["volume"] = volume_kb  # ذخیره بر حسب KB
    return await ask_expire(update, context)




async def ask_expire(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # for both callback and message flows this returns expire selection
    # if called from callback_query, update.callback_query exists; if from message, update.message used earlier
    if hasattr(update, "callback_query") and update.callback_query:
        caller = update.callback_query.message
    else:
        caller = update.message
    
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
        # داخل handle_extend_action وقتی action == "renew_volume":
        limits_file = f"/etc/sshmanager/limits/{username}.json"
        current_volume = "نامشخص"
        if os.path.exists(limits_file):
            try:
                with open(limits_file) as f:
                    data = json.load(f)
                used = safe_int(data.get("used", 0))
                limit = safe_int(data.get("limit", 0))
                current_volume = f"{kb_to_human(used)} / {kb_to_human(limit)}"
            except Exception:
                pass
        keyboard = [
            [InlineKeyboardButton("10 گیگ", callback_data="add_gb_10")],
            [InlineKeyboardButton("20 گیگ", callback_data="add_gb_20")],
            [InlineKeyboardButton("35 گیگ", callback_data="add_gb_35")],
            [InlineKeyboardButton("50 گیگ", callback_data="add_gb_50")],
        ]
        await query.message.reply_text(
            f"📶 حجم فعلی `{username}`: `{current_volume}`\n\nمقدار اضافه:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
        return ASK_RENEW_VALUE


###################



async def handle_extend_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    username = context.user_data.get("renew_username", "")
    action = context.user_data.get("renew_action", "")
    data = (query.data or "").strip()

    if not username or not action:
        await query.message.reply_text("❌ اطلاعات تمدید ناقص است.")
        return ConversationHandler.END

    # UID
    rc, out, err = run_cmd(["id", "-u", username])
    uid = out.strip() if rc == 0 else ""
    limits_file = f"/etc/sshmanager/limits/{username}.json"

    if action == "renew_time" and data.startswith("add_days_"):
        days = int(data.replace("add_days_", "") or "0")
        # تاریخ فعلی انقضا
        output = subprocess.getoutput(f"chage -l {username} 2>/dev/null")
        current_exp = ""
        for line in output.splitlines():
            if "Account expires" in line:
                current_exp = line.split(":", 1)[1].strip()
                break

        if current_exp and current_exp.lower() != "never":
            try:
                current_date = datetime.strptime(current_exp, "%b %d, %Y")
                new_date = current_date + timedelta(days=days)
            except Exception:
                new_date = datetime.now() + timedelta(days=days)
        else:
            new_date = datetime.now() + timedelta(days=days)

        # تمدید در سیستم
        subprocess.run(["sudo", "chage", "-E", new_date.strftime("%Y-%m-%d"), username], check=False)

        # JSON
        try:
            j = {}
            if os.path.exists(limits_file):
                with open(limits_file, "r") as f:
                    j = json.load(f)
            j["expire_timestamp"] = int(new_date.timestamp())
            # اگر بلوک موقت بوده، آزاد کن
            if j.get("is_blocked", False) and j.get("block_reason") != "manual":
                subprocess.run(["sudo", "usermod", "-s", "/bin/bash", username], check=False)
                subprocess.run(["sudo", "usermod", "-d", f"/home/{username}", username], check=False)
                subprocess.run(["sudo", "passwd", "-u", username], check=False)
                subprocess.run(["sudo", "chage", "-E", "-1", username], check=False)
                # iptables rule را اگر نبود، اضافه کن
                if uid:
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
        except Exception:
            pass

        await query.message.reply_text(f"⏳ {days} روز به تاریخ انقضای `{username}` اضافه شد.", parse_mode="Markdown")
        # پیشنهاد تمدید حجم
        keyboard = [
            [InlineKeyboardButton("➕ تمدید حجم", callback_data="renew_volume"),
             InlineKeyboardButton("❌ خیر", callback_data="end_extend")]
        ]
        await query.message.reply_text("آیا می‌خواهید حجم هم تمدید شود؟", reply_markup=InlineKeyboardMarkup(keyboard))
        return ASK_ANOTHER_RENEW

    elif action == "renew_volume" and data.startswith("add_gb_"):
        gb = int(data.replace("add_gb_", "") or "0")
        add_kb = gb * 1024 * 1024

        try:
            j = {}
            if os.path.exists(limits_file):
                with open(limits_file, "r") as f:
                    j = json.load(f)
            old_limit = safe_int(j.get("limit", 0))
            j["limit"] = old_limit + add_kb

            # اگر بلوک بوده و دلیلش دستی نیست، آزاد کن + rule
            if j.get("is_blocked", False) and j.get("block_reason") != "manual":
                subprocess.run(["sudo", "usermod", "-s", "/bin/bash", username], check=False)
                subprocess.run(["sudo", "usermod", "-d", f"/home/{username}", username], check=False)
                subprocess.run(["sudo", "passwd", "-u", username], check=False)
                subprocess.run(["sudo", "chage", "-E", "-1", username], check=False)
                if uid:
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
        except Exception:
            pass

        await query.message.reply_text(f"📶 {gb}GB به حجم `{username}` اضافه شد.", parse_mode="Markdown")
        return ConversationHandler.END

    else:
        await query.message.reply_text("❌ ورودی نامعتبر.")
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
        #uid = subprocess.getoutput(f"id -u {username}").strip()
        rc, out, err = run_cmd(["id", "-u", username])
        uid = out.strip() if rc == 0 else ""
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
    username = update.message.text.strip()
    try:
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

    # call centralized script
    rc, out, err = run_cmd(["python3", LOCK_SCRIPT, username, "manual"])
    if rc != 0:
        await update.message.reply_text("❌ خطا در اجرای عملیات قفل. جزئیات در لاگ.")
        return ConversationHandler.END

    # Ensure JSON synced (script should have updated it, but we double-check)
    limit_file_path = f"/etc/sshmanager/limits/{username}.json"
    if os.path.exists(limit_file_path):
        try:
            with open(limit_file_path, "r") as f:
                user_data = json.load(f)
        except Exception:
            user_data = {}
        user_data["is_blocked"] = True
        user_data["block_reason"] = "manual"
        user_data["blocked_at"] = int(datetime.now().timestamp())
        try:
            with open(limit_file_path, "w") as f:
                json.dump(user_data, f, indent=4)
        except Exception:
            log.exception("failed write limits after manual lock %s", username)

    await update.message.reply_text(f"🔒 اکانت `{username}` با موفقیت قفل شد.", parse_mode="Markdown", reply_markup=main_menu_keyboard)
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
        uid = int(subprocess.getoutput(f"id -u {username}").strip())
        if uid < 1000:
            await update.message.reply_text("⛔️ این کاربر سیستمی است و نمی‌توان آن را باز کرد.")
            return ConversationHandler.END
    except Exception:
        await update.message.reply_text("❌ کاربر یافت نشد.")
        return ConversationHandler.END

    limit_file_path = f"/etc/sshmanager/limits/{username}.json"
    try:
        # اجرای دستورات آنلاک با run_cmd
        run_cmd(["sudo", "passwd", "-u", username])   # unlock password
        run_cmd(["sudo", "chage", "-E", "-1", username])  # remove expire
        # keep shell as NOLOGIN (if your policy allows tunnel-only). If you want interactive shell use "/bin/bash"
        run_cmd(["sudo", "usermod", "-s", NOLOGIN_PATH, username])

        # ensure iptables rule exists (add if missing)
        rc, out, err = run_cmd(["id", "-u", username])
        uid_s = out.strip() if rc == 0 else ""
        if uid_s.isdigit():
            rc2, o2, e2 = run_cmd(["sudo", "iptables", "-C", "SSH_USERS", "-m", "owner", "--uid-owner", uid_s, "-j", "ACCEPT"])
            if rc2 != 0:
                run_cmd(["sudo", "iptables", "-A", "SSH_USERS", "-m", "owner", "--uid-owner", uid_s, "-j", "ACCEPT"])

        # update JSON
        if os.path.exists(limit_file_path):
            try:
                with open(limit_file_path, "r") as f:
                    user_data = json.load(f)
            except Exception:
                user_data = {}
            user_data["is_blocked"] = False
            user_data["block_reason"] = None
            user_data["alert_sent"] = False
            user_data.pop("blocked_at", None)
            try:
                with open(limit_file_path, "w") as f:
                    json.dump(user_data, f, indent=4)
            except Exception:
                log.exception("failed to write limit file during unlock for %s", username)

        await update.message.reply_text(f"✅ اکانت `{username}` با موفقیت باز شد.", parse_mode="Markdown", reply_markup=main_menu_keyboard)
    except Exception:
        log.exception("unlock failed for %s", username)
        await update.message.reply_text("❌ باز کردن اکانت با خطا مواجه شد. جزئیات در لاگ.", reply_markup=main_menu_keyboard)

    return ConversationHandler.END

async def show_limited_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    if update.effective_user.id != ADMIN_ID:
        return

    # به‌روزرسانی مصرف زنده از iptables قبل از نمایش
    try:
        update_live_usage()
    except Exception:
        log.exception("update_live_usage failed")

    limits_dir = Path(LIMITS_DIR)
    if not limits_dir.exists():
        await update.callback_query.message.reply_text("❌ پوشه محدودیت پیدا نشد.")
        return
    msg_lines = []
    for file in limits_dir.glob("*.json"):
        try:
            with open(file) as f:
                data = json.load(f)
            if not isinstance(data, dict):
                continue
            if data.get("type") != "limited":
                continue
            username = file.stem
            used = safe_int(data.get("used", 0))
            limit = safe_int(data.get("limit", 0))
            pct = percent_used_kb(used, limit) if limit > 0 else 0.0
            status_emoji = "🔴" if pct >= 100 else "🟠" if pct >= 90 else "🟢"
            expire_text = ""
            if data.get("expire_timestamp"):
                days_left = (int(data["expire_timestamp"]) - int(datetime.now().timestamp())) // 86400
                expire_text = f" | ⏳ {days_left} روز مانده" if days_left >= 0 else " | ⌛ منقضی‌شده"
            msg_lines.append(f"{status_emoji} `{username}` → {kb_to_human(used)} / {kb_to_human(limit)} ({pct:.0f}%) {expire_text}")
        except Exception:
            log.exception("reading limit file failed")
    if not msg_lines:
        await update.callback_query.message.reply_text("⚠️ هیچ کاربر حجمی پیدا نشد.", reply_markup=main_menu_keyboard)
    else:
        # صفحه‌بندی ساده: اگر خیلی طولانیه، ارسال خلاصه و پیشنهاد report_users
        text = "📊 لیست کاربران حجمی:\n\n" + "\n".join(msg_lines)
        await update.callback_query.message.reply_text(text, parse_mode="Markdown", reply_markup=main_menu_keyboard)

# مشاهده کاربران مسدود با صفحه‌بندی
async def show_blocked_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.callback_query.answer()
        return
    await update.callback_query.answer("در حال دریافت لیست کاربران مسدود شده...")

    blocked_users = []
    limits_dir = LIMITS_DIR

    if not os.path.exists(limits_dir):
        await update.callback_query.message.reply_text("❌ پوشه محدودیت پیدا نشد.")
        return

    for file in os.listdir(limits_dir):
        if not file.endswith(".json"):
            continue
        file_path = os.path.join(limits_dir, file)
        try:
            with open(file_path, "r") as f:
                user_data = json.load(f)
            # فقط دیکشنری‌ها رو قبول می‌کنیم
            if not isinstance(user_data, dict):
                continue
            if user_data.get("is_blocked", False):
                username = file.replace(".json", "")
                reason = user_data.get("block_reason", "نامشخص")
                blocked_users.append(f"🔒 `{username}` ({reason})")
        except Exception as e:
            # خطا در یک فایل → ادامه بده
            log.warning(f"Failed to read {file_path}: {e}")
            continue

    if not blocked_users:
        await update.callback_query.message.reply_text("❗️ هیچ کاربر مسدودی یافت نشد.", reply_markup=main_menu_keyboard)
        return

    # ذخیره لیست و صفحه فعلی در context
    context.user_data["blocked_users_list"] = blocked_users
    context.user_data["blocked_users_page"] = 0

    # ارسال صفحه اول
    await send_blocked_users_page(update.callback_query.message, context)

async def send_blocked_users_page(message, context: ContextTypes.DEFAULT_TYPE):
    blocked_users = context.user_data.get("blocked_users_list", [])
    page = context.user_data.get("blocked_users_page", 0)

    per_page = 10
    start = page * per_page
    end = start + per_page
    chunk = blocked_users[start:end]
    total_pages = (len(blocked_users) - 1) // per_page + 1

    text = f"✅ لیست کاربران مسدودشده (صفحه {page+1}/{total_pages}):\n\n" + "\n".join(chunk)

    # ساخت دکمه‌های صفحه‌بندی
    buttons = []
    if page > 0:
        buttons.append(InlineKeyboardButton("◀ قبلی", callback_data="blocked_prev"))
    if end < len(blocked_users):
        buttons.append(InlineKeyboardButton("بعدی ▶", callback_data="blocked_next"))

    await message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup([buttons]) if buttons else None)

async def blocked_users_pagination_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "blocked_next":
        context.user_data["blocked_users_page"] += 1
    elif query.data == "blocked_prev":
        context.user_data["blocked_users_page"] -= 1

    # محدود کردن به بازه مجاز
    context.user_data["blocked_users_page"] = max(0, min(context.user_data["blocked_users_page"], (len(context.user_data["blocked_users_list"]) - 1) // 10))

    # نمایش صفحه جدید
    await send_blocked_users_page(query.message, context)


# unified text handler for awaiting actions or quick menu commands
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    text = update.message.text.strip()

    
    
# ---------- reporting helper ----------

async def report_all_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = get_reply_func(update)
    update_live_usage()
    users = get_sorted_users()
    if not users:
        await reply("⚠️ هیچ کاربری یافت نشد.")
        return
    context.user_data["report_users"] = users
    context.user_data["report_page"] = 0
    text = build_report_page(users, 0)
    keyboard = []
    if len(users) > 10:
        keyboard.append([InlineKeyboardButton("بعدی ▶", callback_data="report_next")])
    await reply(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


# کال‌بک هندلینگ صفحه‌بندی
async def report_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    users = context.user_data.get("report_users", [])
    page = context.user_data.get("report_page", 0)
    if query.data == "report_next":
        page += 1
    elif query.data == "report_prev":
        page -= 1
    page = max(0, min(page, (len(users) - 1) // 10))
    context.user_data["report_page"] = page
    text = build_report_page(users, page)
    keyboard = []
    if page > 0:
        keyboard.append([InlineKeyboardButton("◀ قبلی", callback_data="report_prev")])
    if (page + 1) * 10 < len(users):
        keyboard.append([InlineKeyboardButton("بعدی ▶", callback_data="report_next")])
    await query.edit_message_text(text=text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))



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

    conv_lock = ConversationHandler(
        entry_points=[CallbackQueryHandler(start_lock_user, pattern="^lock_user$")],
        states={
            ASK_DELETE_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_lock_input)]
        },
        fallbacks=[CommandHandler("cancel", cancel_conversation)],
    )
    
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start ))
    app.add_handler(conv_create)
    app.add_handler(conv_extend)
    app.add_handler(conv_delete)
    app.add_handler(conv_unlock)
    app.add_handler(conv_lock)

    app.add_handler(CallbackQueryHandler(show_limited_users, pattern="^show_limited$"))
    app.add_handler(CallbackQueryHandler(show_blocked_users, pattern="^show_blocked$"))
    app.add_handler(CallbackQueryHandler(blocked_users_pagination_handler, pattern="^blocked_(next|prev)$"))

    app.add_handler(CommandHandler("report_all_users", report_all_users))
    app.add_handler(CallbackQueryHandler(report_callback_handler, pattern="^report_(next|prev)$"))
    app.add_handler(CallbackQueryHandler(lambda u, c: report_all_users(u, c), pattern="^report_users$"))
    
    # MessageHandlerهای متنی را در انتها اضافه کنید تا با مکالمه تداخل نداشته باشند.
    #app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_lock_input))  # for lock flow
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))       # general
    
    app.run_polling()

if __name__ == "__main__":
    run_bot()
#EOF
