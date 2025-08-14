# /usr/local/bin/log_user_traffic.py
#cat > /usr/local/bin/log_user_traffic.py << 'EOF'
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import subprocess, json, os, re
from datetime import datetime
import requests

LIMITS_DIR = "/etc/sshmanager/limits"
LOCK_SCRIPT = "/root/sshmanager/lock_user.py"
BOT_TOKEN = "8152962391:AAG4kYisE21KI8dAbzFy9oq-rn9h9RCQyBM"
ADMIN_ID = "8062924341"
LOG_FILE = "/var/log/sshmanager-traffic.log"

def safe_int(v, default=0):
    try: return int(v)
    except Exception:
        try: return int(float(v))
        except Exception: return default

def send_telegram_message(text):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            data={"chat_id": ADMIN_ID, "text": text, "parse_mode": "Markdown"},
            timeout=5
        )
    except Exception as e:
        with open(LOG_FILE, "a") as logf:
            logf.write(f"{datetime.now().isoformat()} [ارسال پیام خطا] {str(e)}\n")

def atomic_write(path, data):
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    os.replace(tmp, path)

def parse_iptables_lines():
    out = subprocess.getoutput("iptables -L SSH_USERS -v -n -x 2>/dev/null")
    result = {}
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith(("Chain", "pkts", "target")):
            continue
        if "--uid-owner" not in line:
            continue

        # bytes از ستون دوم (فرمت -x)
        parts = line.split()
        bytes_counter = None
        if len(parts) > 1:
            try:
                bytes_counter = int(parts[1])
            except Exception:
                bytes_counter = None
        if bytes_counter is None:
            m = re.search(r"\b(\d+)\b", line)
            if m: bytes_counter = safe_int(m.group(1), None)
        if bytes_counter is None:
            continue

        # UID
        try:
            toks = line.split()
            if "--uid-owner" in toks:
                uid = toks[toks.index("--uid-owner") + 1]
            else:
                m = re.search(r"--uid-owner\s+(\d+)", line)
                uid = m.group(1) if m else None
            if uid:
                result[uid] = bytes_counter
        except Exception:
            continue
    return result

def main():
    if not os.path.isdir(LIMITS_DIR):
        return

    ipt_map = parse_iptables_lines()
    now_ts = int(datetime.now().timestamp())

    for uid, current_bytes in ipt_map.items():
        username = subprocess.getoutput(f"getent passwd {uid} | cut -d: -f1").strip()
        if not username:
            continue

        limits_file = os.path.join(LIMITS_DIR, f"{username}.json")
        if not os.path.exists(limits_file):
            # اگر فایل وجود ندارد، ایجادش نکن—مطابق ساختار فعلی‌ات
            continue

        try:
            with open(limits_file, "r") as f:
                data = json.load(f) or {}
        except Exception:
            data = {}

        used_kb = safe_int(data.get("used", 0))
        limit_kb = safe_int(data.get("limit", 0))
        last_bytes = safe_int(data.get("last_iptables_bytes", 0))

        # به KB یکسان‌سازی
        current_kb_total = current_bytes // 1024
        last_kb_total = last_bytes // 1024

        delta_kb = current_kb_total - last_kb_total
        if delta_kb < 0:
            # ریست شدن شمارنده‌های iptables
            delta_kb = current_kb_total

        if delta_kb > 0:
            used_kb += delta_kb
            data["used"] = int(used_kb)

        # ذخیره مقدار خام بایت آخر، ولی محاسبات همه بر اساس KB انجام شد
        data["last_iptables_bytes"] = int(current_bytes)
        data["last_checked"] = now_ts

        percent = (used_kb / limit_kb * 100.0) if limit_kb > 0 else 0.0

        # هشدار 90%
        if limit_kb > 0:
            if percent >= 90 and not data.get("alert_sent", False):
                send_telegram_message(
                    f"⚠️ کاربر `{username}` بیش از ۹۰٪ از حجم مجاز خود را مصرف کرده است.\n"
                    f"📊 میزان مصرف: {percent:.0f}%\n"
                    f"🕒 زمان بررسی: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                )
                data["alert_sent"] = True
            elif percent < 90 and data.get("alert_sent", False):
                data["alert_sent"] = False

        # مسدودسازی در اتمام حجم
        if limit_kb > 0 and used_kb >= limit_kb and not data.get("is_blocked", False):
            try:
                subprocess.run(["/usr/bin/python3", LOCK_SCRIPT, username, "quota"], check=False)
            except Exception as e:
                with open(LOG_FILE, "a") as lf:
                    lf.write(f"{datetime.now().isoformat()} lock_user call failed for {username}: {e}\n")
            # بلافاصله فلگ‌ها را نیز ست می‌کنیم (lock_user هم ست می‌کند—اینجا idempotent)
            data["is_blocked"] = True
            data["block_reason"] = "quota"
            data["alert_sent"] = True
            send_telegram_message(f"⛔️ حجم کاربر `{username}` تمام شد و اکانت مسدود شد.")

        try:
            atomic_write(limits_file, data)
        except Exception as e:
            with open(LOG_FILE, "a") as lf:
               lf.write(f"{datetime.now().isoformat()} write failed for {limits_file}: {e}\n")

if __name__ == "__main__":
    main()
EOF

#chmod +x /usr/local/bin/log_user_traffic.py
