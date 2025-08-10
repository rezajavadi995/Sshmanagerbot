#اول دستور زیرو بزن تا پوشه ساخته بشه



#cat > /root/sshmanager/lock_user.py << 'EOF'
#!/usr/bin/env python3
#!/usr/bin/env python3
import subprocess
import sys
import requests
import json
import os
from datetime import datetime

BOT_TOKEN = "8152962391:AAG4kYisE21KI8dAbzFy9oq-rn9h9RCQyBM"
ADMIN_ID = 8062924341

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": ADMIN_ID, "text": text}
    try:
        requests.post(url, data=data, timeout=5)
    except:
        pass

def lock_user(username, reason="quota"):
    """
    Lock a Linux user for SSH tunneling only (no shell).
    reason: "quota", "expire", or "manual"
    """
    try:
        # شل غیرفعال ولی اجازه تونل
        subprocess.run(["sudo", "usermod", "-s", "/usr/sbin/nologin", username], check=True)
        subprocess.run(["sudo", "usermod", "-d", "/nonexistent", username], check=True)
        subprocess.run(["sudo", "passwd", "-l", username], check=True)

        # قطع تمام نشست‌های فعال
        subprocess.run(["sudo", "pkill", "-u", username], check=False)

        # بروزرسانی فایل محدودیت
        limit_file_path = f"/etc/sshmanager/limits/{username}.json"
        if os.path.exists(limit_file_path):
            try:
                with open(limit_file_path, "r") as f:
                    user_data = json.load(f)
            except Exception:
                user_data = {}

            user_data["is_blocked"] = True
            user_data["blocked_at"] = int(datetime.now().timestamp())
            if user_data.get("block_reason") != "manual":
                user_data["block_reason"] = reason

            with open(limit_file_path, "w") as f:
                json.dump(user_data, f, indent=4)

        # حذف rule از iptables
        uid = subprocess.getoutput(f"id -u {username}").strip()
        if uid.isdigit():
            subprocess.run(
                ["sudo", "iptables", "-D", "SSH_USERS", "-m", "owner", "--uid-owner", uid, "-j", "ACCEPT"],
                stderr=subprocess.DEVNULL
            )

        # پیام به ادمین
        reason_map = {
            "quota": "اتمام حجم",
            "expire": "اتمام تاریخ انقضا",
            "manual": "قفل دستی"
        }
        send_telegram_message(f"🔒 اکانت `{username}` به دلیل {reason_map.get(reason, reason)} مسدود شد.")

    except Exception as e:
        send_telegram_message(f"⚠️ خطا در مسدودسازی `{username}`: {e}")

if __name__ == "__main__":
    # اجازه دو حالت: python3 lock_user.py <username> [<reason>]
    if len(sys.argv) < 2:
        print("Usage: python3 lock_user.py <username> [reason]")
        sys.exit(1)
    username = sys.argv[1]
    reason = sys.argv[2] if len(sys.argv) > 2 else "quota"
    lock_user(username, reason)

#EOF

##############

#قابل اجراش کن: 

#chmod +x /root/sshmanager/lock_user.py

