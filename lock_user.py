# /root/sshmanager/lock_user.py
#cat > /root/sshmanager/lock_user.py << 'EOF'
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import subprocess, sys, requests, json, os, logging
from datetime import datetime

BOT_TOKEN = "8152962391:AAG4kYisE21KI8dAbzFy9oq-rn9h9RCQyBM"
ADMIN_ID = 8062924341
LIMITS_DIR = "/etc/sshmanager/limits"
LOG_FILE = "/var/log/sshmanager-traffic.log"
NOLOGIN_PATHS = ["/usr/sbin/nologin", "/sbin/nologin", "/usr/bin/nologin"]

logging.basicConfig(filename=LOG_FILE, level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("lock_user")

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
    try:
        requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                      data={"chat_id": ADMIN_ID, "text": text, "parse_mode":"Markdown"},
                      timeout=5)
    except Exception as e:
        log.warning("Failed to send telegram message: %s", e)

def atomic_write(path, data):
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)
    os.replace(tmp, path)

def _is_benign_usermod_err(err_text):
    if not err_text: return False
    txt = err_text.lower()
    for p in ["currently used by process", "cannot lock the user record"]:
        if p in txt: return True
    return False

def _first_existing(pths):
    for p in pths:
        if os.path.exists(p): return p
    return "/bin/false"

def lock_user(username, reason="quota"):
    failures, warnings, successes = [], [], []

    nologin = _first_existing(NOLOGIN_PATHS)

    for cmd in (["usermod","-s", nologin, username],
                ["usermod","-d","/nonexistent", username],
                ["passwd","-l", username]):
        rc, out, err = run_cmd(cmd)
        if rc == 0: successes.append(" ".join(cmd))
        else:
            (_is_benign_usermod_err(err or out) and
             warnings.append(f"cmd warn: {' '.join(cmd)} | rc={rc} | msg={err or out}")) or \
            failures.append(f"cmd failed: {' '.join(cmd)} | rc={rc} | err={err or out}")

    rc, out, err = run_cmd(["pkill", "-u", username])
    (rc in (0,1) and successes.append("pkill")) or failures.append(f"pkill rc={rc} err={err}")

    limit_file_path = os.path.join(LIMITS_DIR, f"{username}.json")
    try:
        data = {}
        if os.path.exists(limit_file_path):
            try: data = json.load(open(limit_file_path)) or {}
            except Exception: data = {}
        data["is_blocked"] = True
        data["blocked_at"] = int(datetime.now().timestamp())
        data["block_reason"] = reason
        data["alert_sent"] = True
        os.makedirs(LIMITS_DIR, exist_ok=True)
        atomic_write(limit_file_path, data)
        successes.append("limits-file-updated")
    except Exception as e:
        failures.append(f"write limits failed: {e}")

    rc, out, err = run_cmd(["id","-u", username])
    uid = out.strip() if rc == 0 else ""
    if uid.isdigit():
        # حذف idempotent (تا در صورت چند Rule همه حذف شوند)
        removed_any = False
        while True:
            rc2, _, _ = run_cmd(["iptables","-D","SSH_USERS","-m","owner","--uid-owner", uid,"-j","ACCEPT"])
            if rc2 == 0:
                removed_any = True
                continue
            break
        successes.append("iptables-removed" if removed_any else "iptables-not-present")
    else:
        warnings.append("cannot get uid for user")

    reason_map = {"quota":"اتمام حجم","expire":"اتمام تاریخ انقضا","manual":"قفل دستی"}
    header = f"🔒 تلاش برای قفل `{username}` — نتیجه:\n"
    summary_lines = []
    if failures:
        summary_lines.append("❌ خطا(های مهم) وجود دارد:")
        summary_lines += [f"- {f}" for f in failures]
    elif warnings:
        summary_lines.append("⚠️ هشدار(ها) وجود دارد (عملیات اصلی انجام شده):")
        summary_lines += [f"- {w}" for w in warnings]
    else:
        summary_lines.append(f"✅ اکانت `{username}` با موفقیت به دلیل *{reason_map.get(reason, reason)}* مسدود شد.")

    details = ""
    details += f"فایل محدودیت: {limit_file_path}\n"
    details += f"وضعیت فایل limits: {'به‌روزرسانی شد' if os.path.exists(limit_file_path) else 'وجود ندارد'}\n"
    details += f"موفقیت‌ها: {', '.join(successes) or '-'}\n"
    if warnings: details += "هشدارها:\n" + "\n".join(warnings) + "\n"
    if failures: details += "خطاها:\n" + "\n".join(failures) + "\n"
    details += f"\nلاگ: {LOG_FILE}"

    send_telegram_message(header + "\n" + "\n".join(summary_lines) + "\n\n" + "```\n" + details + "\n```")

    if failures: log.warning("lock_user partial failures for %s: %s", username, failures)
    else: log.info("User %s locked (reason=%s) — successes: %s warnings: %s", username, reason, successes, warnings)

    return os.path.exists(limit_file_path)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 lock_user.py <username> [reason]")
        sys.exit(1)
    username = sys.argv[1]
    reason = sys.argv[2] if len(sys.argv) > 2 else "quota"
    ok = lock_user(username, reason)
    sys.exit(0 if ok else 2)
EOF

#chmod +x /root/sshmanager/lock_user.py
