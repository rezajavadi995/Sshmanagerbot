#اول دستور زیرو بزن تا پوشه ساخته بشه



cat > /root/sshmanager/lock_user.py << 'EOF'
#!/usr/bin/env python3
import subprocess
import sys
import requests

BOT_TOKEN = "8152962391:AAG4kYisE21KI8dAbzFy9oq-rn9h9RCQyBM"
ADMIN_ID = 8062924341

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": ADMIN_ID, "text": text}
    try:
        requests.post(url, data=data, timeout=5)
    except:
        pass

def lock_user(username):
    try:
        # غیرفعال کردن شل و قفل کردن اکانت
        subprocess.run(["usermod", "-s", "/usr/sbin/nologin", username], check=True)
        subprocess.run(["passwd", "-l", username], check=True)
        
        # قطع کردن اتصالات فعال SSH کاربر
        subprocess.run(["pkill", "-u", username], check=False)
        
        # تغییر زمان اکانت به گذشته
        subprocess.run(["usermod", "--expiredate", "1", username], check=True)

        # حذف rule از iptables
        uid = subprocess.getoutput(f"id -u {username}").strip()
        subprocess.run(
            ["iptables", "-D", "SSH_USERS", "-m", "owner", "--uid-owner", uid, "-j", "ACCEPT"],
            stderr=subprocess.DEVNULL,
        )

        send_telegram_message(f"🔒 اکانت کاربر `{username}` به دلیل اتمام حجم یا زمان مسدود شد.")
    except Exception as e:
        send_telegram_message(f"⚠️ خطا در مسدودسازی کاربر {username}: {e}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python3 lock_user.py <username>")
        sys.exit(1)
    lock_user(sys.argv[1])
EOF

##############

#قابل اجراش کن: 

chmod +x /root/sshmanager/lock_user.py

