#cat > /root/sshmanager/lock_user.py << 'EOF'
#!/usr/bin/env python3
import subprocess
import sys
import requests
import json
import os
from datetime import datetime

BOT_TOKEN = "8152962391:AAG4kYisE21KI8dAbzFy9oq-rn9h9RCQyBM"
ADMIN_ID = 8062924341
LIMITS_DIR = "/etc/sshmanager/limits"
LOG_FILE = "/var/log/sshmanager-traffic.log"



def run_cmd(cmd, timeout=30):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except subprocess.TimeoutExpired as e:
        return 124, "", f"timeout: {e}"
    except Exception as e:
        log.exception("run_cmd unexpected error: %s", cmd)
        return 1, "", str(e)


def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": ADMIN_ID, "text": text}
    try:
        requests.post(url, data=data, timeout=5)
    except:
        pass

def lock_user(username, reason="quota"):
    """
    Lock a Linux user for SSH tunneling only (no interactive shell).
    reason: "quota", "expire", or "manual"
    """
    try:
        # شل نال شده (nologin) — اجازه تونل ولی بدون ترمینال تعاملی
        rc, out, err = run_cmd(["sudo", "usermod", "-s", "/usr/sbin/nologin", username])
        if rc != 0:
            log.warning("usermod -s failed for %s: rc=%s err=%s out=%s", username, rc, err, out)
            send_telegram_message(f"❌ خطا در قفل‌کردن `{username}` — اجرای دستور ناموفق بود. جزئیات در لاگ.")
            return False

        # هوم غیرواقعی برای امنیت
        rc, out, err = run_cmd(["sudo", "usermod", "-d", "/nonexistent", username])
        if rc != 0:
            log.warning("usermod -d failed for %s: rc=%s err=%s out=%s", username, rc, err, out)
            send_telegram_message(f"❌ خطا در قفل‌کردن `{username}` — اجرای دستور ناموفق بود. جزئیات در لاگ.")
            return False

        # قفل پسورد (برای جلوگیری از لاگین با پسورد)
        rc, out, err = run_cmd(["sudo", "passwd", "-l", username])
        if rc != 0:
            log.warning("passwd -l failed for %s: rc=%s err=%s out=%s", username, rc, err, out)
            send_telegram_message(f"❌ خطا در قفل‌کردن `{username}` — اجرای دستور ناموفق بود. جزئیات در لاگ.")
            return False

        # قطع نشست‌های فعال کاربر (خطاها نادیده گرفته می‌شوند اما در صورت نیاز لاگ کن)
        run_cmd(["sudo", "pkill", "-u", username])

        # بروزرسانی فایل محدودیت
        limit_file_path = os.path.join(LIMITS_DIR, f"{username}.json")
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
            user_data["alert_sent"] = True

            try:
                with open(limit_file_path, "w") as f:
                    json.dump(user_data, f, indent=4)
            except Exception:
                pass

        # حذف rule از iptables (در صورت وجود)
        uid = subprocess.getoutput(f"id -u {username}").strip()
        if uid.isdigit():
            run_cmd(["sudo", "iptables", "-D", "SSH_USERS", "-m", "owner", "--uid-owner", uid, "-j", "ACCEPT"])

        # پیام به ادمین
        reason_map = {
            "quota": "اتمام حجم",
            "expire": "اتمام تاریخ انقضا",
            "manual": "قفل دستی"
        }
        send_telegram_message(f"🔒 اکانت `{username}` به دلیل {reason_map.get(reason, reason)} مسدود شد.")

        return True

    except Exception:
        log.exception("Unexpected error in lock_user for %s", username)
        send_telegram_message(f"⚠️ خطای داخلی هنگام مسدودسازی `{username}` — جزئیات در لاگ.")
        return False

if __name__ == "__main__":
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

