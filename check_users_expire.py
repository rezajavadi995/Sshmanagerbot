#فایل چک یوزر برای محدود سازی دسترسی کاربر بعد پیام هشدار از ربات  وقتی تاریخ اکانت کاربر تموم شد

cat > /usr/local/bin/check_users_expire.py << 'EOF'
#!/usr/bin/env python3
import subprocess
import datetime
import requests

BOT_TOKEN = "your_token"
ADMIN_ID = "your_id"

def notify_admin(username, reason):
    requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
        data={
            "chat_id": ADMIN_ID,
            "text": f"⚠️ اکانت `{username}` به دلیل {reason} غیرفعال شد.",
            "parse_mode": "Markdown"
        }
    )

users = subprocess.getoutput("awk -F: '$3 >= 1000 {print $1}' /etc/passwd").splitlines()

for user in users:
    exp_date = subprocess.getoutput(f"chage -l {user} | grep 'Account expires' | cut -d: -f2").strip()
    if exp_date.lower() == "never" or not exp_date:
        continue

    try:
        exp = datetime.datetime.strptime(exp_date, "%b %d, %Y")
        if exp < datetime.datetime.now():
            #v1
            #subprocess.call(["usermod", "--expiredate", "1", user])
            
            #v2
            subprocess.run(["python3", "/root/sshmanager/lock_user.py", user])
            notify_admin(user, "اتمام تاریخ انقضا")
    except Exception:
        continue
        
EOF

###################################

#دسترسی اجرا بده بهش بعدش: 
chmod +x /usr/local/bin/check_users_expire.py
###################################


#ساخت systemd Service و Timer
cat > /etc/systemd/system/check-expire.service << 'EOF'
[Unit]
Description=Check and disable expired users

[Service]
Type=oneshot
ExecStart=/usr/local/bin/check_users_expire.py
EOF


###################################

#مرحله ۳
#ساخت تایمر
cat > /etc/systemd/system/check-expire.timer << 'EOF'

[Unit]
Description=Run expire checker every 1 hour

[Timer]
OnBootSec=2min
OnUnitActiveSec=1h

[Install]
WantedBy=timers.target
EOF

###################################

#دسترسی اجرا بده بهش: 
systemctl daemon-reexec
systemctl daemon-reload
systemctl enable --now check-expire.timer
systemctl status check-expire.timer

###################################
#با این کار، هر ۱ ساعت چک می‌کنه و اگر کاربری منقضی شده باشه، اونو غیرفعال می‌کنه و بهت پیام می‌ده.


