#نسخه جدید  ساخت فایل لاگ 
#حتما بعد خرید سرور این دستورو بزن
#بعد بهش با دستور زیر دسترسی اجرا بده: 
#chmod +x /usr/local/bin/log_user_traffic.py

#cat > /usr/local/bin/log_user_traffic.py << 'EOF'
#!/usr/bin/env python3
import subprocess
import json
import os
import requests
from datetime import datetime

# تنظیمات
LIMITS_DIR = "/etc/sshmanager/limits"
LOCK_SCRIPT = "/root/sshmanager/lock_user.py"
BOT_TOKEN = "8152962391:AAG4kYisE21KI8dAbzFy9oq-rn9h9RCQyBM"  
ADMIN_ID = "8062924341"
LOG_FILE = "/var/log/sshmanager-traffic.log"

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
        json.dump(data, f, indent=4)
    os.replace(tmp, path)

def parse_iptables_lines():
    """
    Read iptables SSH_USERS chain and return mapping uid_str -> bytes_counter (int bytes)
    """
    out = subprocess.getoutput("iptables -L SSH_USERS -v -n -x 2>/dev/null")
    result = {}
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        # ignore header lines
        if line.startswith("Chain") or line.startswith("pkts") or line.startswith("target"):
            continue
        # We only care lines that contain --uid-owner
        if "--uid-owner" not in line:
            continue
        parts = line.split()
        # common iptables verbose: pkts bytes target prot opt in out source destination options...
        # parts[0]=pkts, parts[1]=bytes
        try:
            bytes_counter = int(parts[1])
        except Exception:
            # best-effort fallback: find first numeric token
            bytes_counter = None
            for tok in parts:
                if tok.isdigit():
                    bytes_counter = int(tok)
                    break
            if bytes_counter is None:
                continue
        # get uid after --uid-owner
        try:
            toks = line.split()
            if "--uid-owner" in toks:
                idx = toks.index("--uid-owner")
                uid = toks[idx + 1]
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

        # normalize fields
        used_kb = int(data.get("used", 0))           # stored in KB
        limit_kb = int(data.get("limit", 0))         # stored in KB
        last_bytes = int(data.get("last_iptables_bytes", 0))  # stored in bytes

        # compute delta (bytes)
        delta_bytes = current_bytes - last_bytes
        if delta_bytes < 0:
            # counter reset -> take current_bytes as delta
            delta_bytes = current_bytes

        delta_kb = int(delta_bytes / 1024)

        if delta_kb > 0:
            used_kb += delta_kb
            data["used"] = int(used_kb)

        # update last counter and timestamp
        data["last_iptables_bytes"] = int(current_bytes)
        data["last_checked"] = now_ts

        # compute percent
        percent = (used_kb / limit_kb) * 100 if limit_kb > 0 else 0.0

        # alert logic: only one-time alert at >=90%
        if limit_kb > 0:
            if percent >= 90 and not data.get("alert_sent", False):
                # Preserve your original style of message (kept Persian)
                send_telegram_message(
                    f"⚠️ کاربر `{username}` بیش از ۹۰٪ از حجم مجاز خود را مصرف کرده است.\n"
                    f"📊 میزان مصرف: {percent:.0f}%\n"
                    f"🕒 زمان بررسی: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                )
                data["alert_sent"] = True
            elif percent < 90 and data.get("alert_sent", False):
                data["alert_sent"] = False

        # if used >= limit -> lock once
        if limit_kb > 0 and used_kb >= limit_kb and not data.get("is_blocked", False):
            try:
                # call lock script with reason "quota"
                subprocess.run(["python3", LOCK_SCRIPT, username, "quota"], check=False)
            except Exception as e:
                with open(LOG_FILE, "a") as lf:
                    lf.write(f"{datetime.now().isoformat()} lock_user call failed for {username}: {e}\n")
            data["is_blocked"] = True
            data["block_reason"] = "quota"
            data["alert_sent"] = True
            send_telegram_message(f"⛔️ حجم کاربر `{username}` تمام شد و اکانت مسدود شد.")

        # persist atomically
        try:
            atomic_write(limits_file, data)
        except Exception as e:
            with open(LOG_FILE, "a") as lf:
                lf.write(f"{datetime.now().isoformat()} write failed for {limits_file}: {e}\n")

if __name__ == "__main__":
    main()
#EOF
