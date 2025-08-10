#فایل چک یوزر برای محدود سازی دسترسی کاربر بعد پیام هشدار از ربات  وقتی تاریخ اکانت کاربر تموم شد

#cat > /usr/local/bin/check_users_expire.py << 'EOF'
#!/usr/bin/env python3
import subprocess
import datetime
import requests
from datetime import datetime

BOT_TOKEN = "8152962391:AAG4kYisE21KI8dAbzFy9oq-rn9h9RCQyBM"
ADMIN_ID = "8062924341"


def notify_admin(username, expire_date):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    message = (
        f"⚠️ اکانت `{username}` به دلیل *اتمام تاریخ انقضا* غیرفعال شد.\n"
        f"📅 تاریخ انقضا: `{expire_date}`\n"
        f"⏰ زمان بررسی: `{now}`"
    )
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": ADMIN_ID, "text": message, "parse_mode": "Markdown"}
        )
    except Exception:
        pass

users = subprocess.getoutput("awk -F: '$3 >= 1000 {print $1}' /etc/passwd").splitlines()

for user in users:
    exp_date = subprocess.getoutput(
        f"chage -l {user} | grep 'Account expires' | cut -d: -f2"
    ).strip()

    if exp_date.lower() == "never" or not exp_date:
        continue

    try:
        exp = datetime.strptime(exp_date, "%b %d, %Y")
        if exp < datetime.now():
            # Check if user is already blocked
            limit_file_path = f"/etc/sshmanager/limits/{user}.json"
            is_blocked = False
            if os.path.exists(limit_file_path):
                with open(limit_file_path, "r") as f:
                    user_data = json.load(f)
                is_blocked = user_data.get("is_blocked", False)

            if not is_blocked:
                # استفاده از lock_user.py برای قفل حرفه‌ای
                subprocess.run(["python3", "/root/sshmanager/lock_user.py", user, "expire"])
                notify_admin(user, exp.strftime("%Y-%m-%d"))
    except Exception:
        continue

        
#EOF

###################################

#دسترسی اجرا بده بهش بعدش: 
#chmod +x /usr/local/bin/check_users_expire.py
##################amu reza####################


