#/root/sshmanager/lock_user.py
cat > /root/sshmanager/lock_user.py << 'EOF'
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import subprocess, sys, requests, json, os, logging, time
from datetime import datetime

BOT_TOKEN = "8152962391:AAG4kYisE21KI8dAbzFy9oq-rn9h9RCQyBM"
ADMIN_ID = 8062924341
LIMITS_DIR = "/etc/sshmanager/limits"
LOG_FILE = "/var/log/sshmanager-traffic.log"
NOLOGIN_PATHS = ["/usr/sbin/nologin", "/sbin/nologin", "/usr/bin/nologin"]
FIX_IPTABLES_SCRIPT = "/root/fix-iptables.sh"

logging.basicConfig(filename=LOG_FILE, level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("lock_user")

def run_cmd(cmd, timeout=30):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception as e:
        log.exception("run_cmd unexpected error: %s", cmd)
        return 1, "", str(e)

def send_telegram_message(text):
    if not BOT_TOKEN or not ADMIN_ID: return
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                      data={"chat_id": ADMIN_ID, "text": text, "parse_mode":"Markdown"},
                      timeout=10)
    except Exception as e:
        log.warning("Failed to send telegram message: %s", e)

def atomic_write(path, data):
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    os.replace(tmp, path)

def _first_existing(pths):
    for p in pths:
        if os.path.exists(p): return p
    return "/bin/false"

def lock_user(username, reason="quota"):
    failures, warnings, successes = [], [], []

    # 1. Kill user processes first and wait
    rc, out, err = run_cmd(["pkill", "-u", username])
    if rc in (0, 1): # 0 = killed, 1 = no process found
        successes.append("pkill")
        time.sleep(0.5)
    else:
        warnings.append(f"pkill may have failed: rc={rc} err={err or out}")

    # 2. Modify user account
    nologin = _first_existing(NOLOGIN_PATHS)
    for cmd_tuple in (
        (["usermod", "-s", nologin, username], "Shell set to nologin"),
        (["passwd", "-l", username], "Password locked"),
    ):
        rc, out, err = run_cmd(cmd_tuple[0])
        if rc == 0:
            successes.append(cmd_tuple[1])
        else:
            # It's not critical, so we'll log it as a warning
            warnings.append(f"Command '{' '.join(cmd_tuple[0])}' failed: {err or out}")

    # 3. Update the JSON file to reflect the locked state
    limit_file_path = os.path.join(LIMITS_DIR, f"{username}.json")
    try:
        data = {}
        if os.path.exists(limit_file_path):
            try: data = json.load(open(limit_file_path, "r", encoding="utf-8")) or {}
            except Exception: data = {}
        data["is_blocked"] = True
        data["blocked_at"] = int(datetime.now().timestamp())
        data["block_reason"] = reason
        data["alert_sent"] = True
        os.makedirs(LIMITS_DIR, exist_ok=True)
        atomic_write(limit_file_path, data)
        successes.append("JSON file marked as blocked")
    except Exception as e:
        failures.append(f"Failed to write limits file: {e}")
    
    # 4. Run the master iptables script. It will automatically remove rules for locked users.
    if os.path.exists(FIX_IPTABLES_SCRIPT):
        rc_fix, out_fix, err_fix = run_cmd(["sudo", "bash", FIX_IPTABLES_SCRIPT])
        if rc_fix == 0:
            successes.append("iptables rules synchronized")
        else:
            warnings.append(f"iptables script had an error: {err_fix}")
    else:
        warnings.append(f"iptables script not found at {FIX_IPTABLES_SCRIPT}")

    # 5. Report the result
    reason_map = {"quota":"اتمام حجم","expire":"اتمام تاریخ انقضا","manual":"قفل دستی"}
    reason_text = reason_map.get(reason, reason)
    
    if failures:
        status = "❌ قفل ناموفق"
        summary_lines = [f"- {f}" for f in failures]
    else:
        status = "✅ قفل موفق" if not warnings else "⚠️ قفل با هشدار"
        summary_lines = [f"کاربر `{username}` به دلیل *{reason_text}* مسدود شد."]

    details = "```\n" + "\n".join(successes) + "\n"
    if warnings:
        details += "\nWarnings:\n" + "\n".join(warnings) + "\n"
    details += "```"

    message = f"🔒 **{status}**\n\n" + "\n".join(summary_lines) + "\n\n**جزئیات عملیات:**\n" + details
    send_telegram_message(message)

    if failures:
        log.error("lock_user failed for %s: %s", username, failures)
        return False
    
    log.info("User %s locked (reason=%s) — successes: %s warnings: %s", username, reason, successes, warnings)
    return True

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <username> [reason: quota|expire|manual]")
        sys.exit(1)
    username = sys.argv[1]
    reason = sys.argv[2] if len(sys.argv) > 2 else "manual"
    if lock_user(username, reason):
        sys.exit(0)
    else:
        sys.exit(1)

EOF
#

mkdir -p /root/sshmanager
#
chmod +x /root/sshmanager/lock_user.py
