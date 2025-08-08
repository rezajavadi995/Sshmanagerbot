هشدار مصرف (بررسی ساعتی و پیام به ادمین)

📌 ایده کلی:

هر ساعت، اسکریپتی اجرا بشه که:

فایل‌های /etc/sshmanager/limits/*.json رو بخونه

اگر used بیش از ۹۰٪ limit بود → پیام هشدار به ادمین بفرسته (با bot)

مراحل زیر رو دنبال کن

#########################################

ساخت فایل اسکریپت بررسی مصرف

cat > /usr/local/bin/check_user_usage.py << 'EOF'
#!/usr/bin/env python3
import os, json
import requests
from datetime import datetime

LIMITS_DIR = "/etc/sshmanager/limits"
BOT_TOKEN = "8152962391:AAG4kYisE21KI8dAbzFy9oq-rn9h9RCQyBM"
ADMIN_ID = "8062924341"

# ... سایر کدها

def send_alert(username, percent):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    msg = (
        f"⚠️ کاربر `{username}` بیش از ۹۰٪ از حجم مجاز خود را مصرف کرده است.\n"
        f"📊 میزان مصرف: {percent:.0f}%\n"
        f"🕒 زمان بررسی: {now}"
    )
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": ADMIN_ID, "text": msg, "parse_mode": "Markdown"})

for file in os.listdir(LIMITS_DIR):
    if file.endswith(".json"):
        path = os.path.join(LIMITS_DIR, file)
        with open(path) as f:
            data = json.load(f)
            
            # New check: Skip if user is blocked
            if data.get("is_blocked", False):
                continue
            
            used = int(data.get("used", 0))
            limit = int(data.get("limit", 1))  # پیش‌فرض برای جلوگیری از تقسیم بر صفر
            percent = (used / limit) * 100
            
            if percent >= 90 and not data.get("alert_sent", False):
                username = file.replace(".json", "")
                send_alert(username, percent)
                
                # Set alert_sent to True to prevent repeated alerts
                data["alert_sent"] = True
                with open(path, "w") as fw:
                    json.dump(data, fw, indent=4)

EOF

chmod +x /usr/local/bin/check_user_usage.py

###################################

ساخت systemd.timer برای اجرا هر ساعت:


cat > /etc/systemd/system/check-usage.timer << 'EOF'
[Unit]
Description=Check SSH User Traffic Hourly

[Timer]
OnBootSec=5min
OnUnitActiveSec=1h

[Install]
WantedBy=timers.target
EOF



##################################### 

سرویس اجرا کننده:

cat > /etc/systemd/system/check-usage.service << 'EOF'
[Unit]
Description=Run check_user_usage.py script

[Service]
ExecStart=/usr/local/bin/check_user_usage.py
EOF



################################ 

سپس فعال‌سازی:

systemctl daemon-reexec
systemctl daemon-reload
systemctl enable --now check-usage.timer

#####################
✅ از این به بعد، هر یک ساعت مصرف بررسی می‌شه و اگر زیاد بود، بهت پیام می‌ده.
