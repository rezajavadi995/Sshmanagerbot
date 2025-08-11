#نسخه جدید  ساخت فایل لاگ 
#حتما بعد خرید سرور این دستورو بزن
#بعد بهش با دستور زیر دسترسی اجرا بده: 
#chmod +x /usr/local/bin/log_user_traffic.py

#cat > /usr/local/bin/log_user_traffic.py << 'EOF'
#!/usr/bin/env python3
import subprocess
import json
import os
from datetime import datetime
import requests

# مسیرها و تنظیمات
LIMITS_DIR = "/etc/sshmanager/limits"
LOCK_SCRIPT = "/root/sshmanager/lock_user.py"
BOT_TOKEN = "توکن_ربات_اینجا"
ADMIN_ID = "آیدی_ادمین_اینجا"
LOG_FILE = "/var/log/sshmanager-traffic.log"

def send_telegram_message(text):
    """ارسال پیام تلگرام به ادمین"""
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
    """ذخیره امن JSON بدون خراب کردن فایل در صورت خطا"""
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=4)
    os.replace(tmp, path)

def parse_iptables_lines():
    """خواندن داده‌های مصرف از iptables"""
    out = subprocess.getoutput("iptables -L SSH_USERS -v -n -x 2>/dev/null")
    result = {}
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith(("Chain", "pkts", "target")):
            continue
        if "--uid-owner" not in line:
            continue

        parts = line.split()
        try:
            bytes_counter = int(parts[1])
        except ValueError:
            # جستجوی اولین عدد معتبر
            bytes_counter = next((int(tok) for tok in parts if tok.isdigit()), None)
            if bytes_counter is None:
                continue

        # استخراج UID
        try:
            toks = line.split()
            if "--uid-owner" in toks:
                uid = toks[toks.index("--uid-owner") + 1]
            else:
                import re
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
            continue

        try:
            with open(limits_file, "r") as f:
                data = json.load(f)
        except Exception:
            data = {}

        used_kb = int(data.get("used", 0))
        limit_kb = int(data.get("limit", 0))
        last_bytes = int(data.get("last_iptables_bytes", 0))

        # محاسبه تغییر مصرف
        delta_bytes = current_bytes - last_bytes
        if delta_bytes < 0:
            delta_bytes = current_bytes
        delta_kb = int(delta_bytes / 1024)

        if delta_kb > 0:
            used_kb += delta_kb
            data["used"] = int(used_kb)

        # بروزرسانی آخرین مقادیر
        data["last_iptables_bytes"] = int(current_bytes)
        data["last_checked"] = now_ts

        # درصد مصرف
        percent = (used_kb / limit_kb) * 100 if limit_kb > 0 else 0.0

        # هشدار یکبار مصرف در ۹۰٪
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

        # مسدودسازی اگر حجم تمام شد
        if limit_kb > 0 and used_kb >= limit_kb and not data.get("is_blocked", False):
            try:
                subprocess.run(["python3", LOCK_SCRIPT, username, "quota"], check=False)
            except Exception as e:
                with open(LOG_FILE, "a") as lf:
                    lf.write(f"{datetime.now().isoformat()} lock_user call failed for {username}: {e}\n")
            data["is_blocked"] = True
            data["block_reason"] = "quota"
            data["alert_sent"] = True
            send_telegram_message(f"⛔️ حجم کاربر `{username}` تمام شد و اکانت مسدود شد.")

        # ذخیره فایل
        try:
            atomic_write(limits_file, data)
        except Exception as e:
            with open(LOG_FILE, "a") as lf:
                lf.write(f"{datetime.now().isoformat()} write failed for {limits_file}: {e}\n")

if __name__ == "__main__":
    main()

#EOF
