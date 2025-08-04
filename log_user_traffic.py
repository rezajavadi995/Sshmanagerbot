#نسخه جدید  ساخت فایل لاگ 
#حتما بعد خرید سرور این دستورو بزن
#بعد بهش با دستور زیر دسترسی اجرا بده: 
chmod +x /usr/local/bin/log_user_traffic.py

cat > /usr/local/bin/log_user_traffic.py << 'EOF'
#!/usr/bin/env python3

import subprocess
import json
import os
import requests
from datetime import datetime

# تنظیمات
LIMITS_DIR = "/etc/sshmanager/limits"
BOT_TOKEN = "your_token"
ADMIN_ID = "your_id"
LOG_FILE = "/var/log/sshmanager-traffic.log"

def send_telegram_message(text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"}
        )
    except Exception as e:
        with open(LOG_FILE, "a") as logf:
            logf.write(f"[ارسال پیام خطا] {str(e)}\n")

# اجرای iptables برای دریافت ترافیک
output = subprocess.getoutput("iptables -L SSH_USERS -v -n -x")
lines = output.strip().split("\n")

for line in lines:
    parts = line.split()
    if len(parts) >= 9 and parts[-1].isdigit():
        try:
            bytes_used = int(parts[1])
            uid = int(parts[-1])
            username = subprocess.getoutput(f"getent passwd {uid} | cut -d: -f1").strip()
            if not username:
                continue

            limits_file = os.path.join(LIMITS_DIR, f"{username}.json")
            if not os.path.exists(limits_file):
                continue

            try:
                with open(limits_file, "r") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                continue

            old_used = int(data.get("used", 0))
            limit = int(data.get("limit", 0))
            new_used = old_used + int(bytes_used / 1024)  # به KB
            data["used"] = new_used

            # اگر حجم مصرفی بیشتر یا مساوی از حد بود → قطع دسترسی
            if limit > 0 and new_used >= limit:
                subprocess.call(["python3", "/root/sshmanager/lock_user.py", username])

            #کامنت شده فعلا از فایل لاک یوزر پاور میگیره
            #اگه باگ داشت فعالش کن
                #subprocess.call([
                    #"iptables", "-D", "SSH_USERS", "-m", "owner",
                    #"--uid-owner", str(uid), "-j", "ACCEPT"
                #])
            
                #send_telegram_message(
                    #f"⛔️ حجم کاربر `{username}` تمام شد و دسترسی او *قطع شد*.\n"
                    #f"📊 مصرف: `{new_used}/{limit}` کیلوبایت"
                #)

            # هشدار نزدیک شدن به حجم
            elif limit > 0:
                percent = new_used / limit
                if percent >= 0.97:
                    send_telegram_message(
                        f"🚨 کاربر `{username}` بیش از ۹۷٪ حجم مجاز را مصرف کرده است.\n"
                        f"📊 مصرف: `{new_used}/{limit}` کیلوبایت"
                    )
                elif percent >= 0.9:
                    send_telegram_message(
                        f"  کاربر `{username}` به {percent:.0%} از حجم مجاز خود رسیده است.\n"
                        f" مصرف: `{new_used}/{limit}` کیلوبایت"
                    )

            # هشدار انقضا
            if "expire_timestamp" in data:
                expire_ts = int(data["expire_timestamp"])
                now_ts = int(datetime.now().timestamp())
                days_left = (expire_ts - now_ts) // 86400
                if 0 <= days_left <= 2:
                    send_telegram_message(
                        f"⏳ فقط {days_left} روز تا پایان اعتبار کاربر `{username}` باقی مانده است."
                    )

            # ذخیره‌سازی فایل به‌روزشده
            with open(limits_file, "w") as f:
                json.dump(data, f)

        except Exception as e:
            with open(LOG_FILE, "a") as logf:
                logf.write(f"[خطا در بررسی {username}] {str(e)}\n")
EOF
