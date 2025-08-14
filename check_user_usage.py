# /usr/local/bin/check_user_usage.py
#cat > /usr/local/bin/check_user_usage.py << 'EOF'
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, json, requests
from datetime import datetime

LIMITS_DIR = "/etc/sshmanager/limits"
BOT_TOKEN = "8152962391:AAG4kYisE21KI8dAbzFy9oq-rn9h9RCQyBM"
ADMIN_ID = "8062924341"

def safe_int(v, default=0):
    try:
        return int(v)
    except Exception:
        try:
            return int(float(v))
        except Exception:
            return default

def send_alert(username, percent):
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    msg = (
        f"⚠️ کاربر `{username}` بیش از ۹۰٪ از حجم مجاز خود را مصرف کرده است.\n"
        f"📊 میزان مصرف: {percent:.0f}%\n"
        f"🕒 زمان بررسی: {now}"
    )
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": ADMIN_ID, "text": msg, "parse_mode": "Markdown"},
            timeout=5
        )
    except Exception:
        pass

def main():
    if not os.path.isdir(LIMITS_DIR):
        return
    for fn in os.listdir(LIMITS_DIR):
        if not fn.endswith(".json"): continue
        path = os.path.join(LIMITS_DIR, fn)
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except Exception:
            continue

        if data.get("is_blocked", False):
            # در حالت قفل‌شده، هشدار نیاز نیست
            continue

        used = safe_int(data.get("used", 0))
        limit = safe_int(data.get("limit", 0))
        percent = (used / limit * 100) if limit > 0 else 0

        if limit > 0 and percent >= 90 and not data.get("alert_sent", False):
            send_alert(fn[:-5], percent)
            data["alert_sent"] = True
            try: open(path, "w").write(json.dumps(data, indent=4, ensure_ascii=False))
            except Exception: pass
        elif percent < 90 and data.get("alert_sent", False):
            data["alert_sent"] = False
            try: open(path, "w").write(json.dumps(data, indent=4, ensure_ascii=False))
            except Exception: pass

if __name__ == "__main__":
    main()
#EOF
