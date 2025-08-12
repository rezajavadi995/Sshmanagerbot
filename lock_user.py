#cat > /root/sshmanager/lock_user.py << 'EOF'
#!/usr/bin/env python3
import subprocess
import sys
import requests
import json
import os
from datetime import datetime
import logging

BOT_TOKEN = "8152962391:AAG4kYisE21KI8dAbzFy9oq-rn9h9RCQyBM"
ADMIN_ID = 8062924341
LIMITS_DIR = "/etc/sshmanager/limits"
LOG_FILE = "/var/log/sshmanager-traffic.log"
NOLOGIN_PATH = "/usr/sbin/nologin"

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
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": ADMIN_ID, "text": text, "parse_mode":"Markdown"}
    try:
        requests.post(url, data=data, timeout=5)
    except Exception as e:
        log.warning("Failed to send telegram message: %s", e)

def atomic_write(path, data):
    tmp = f"{path}.tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=4)
        os.replace(tmp, path)
    except Exception:
        log.exception("atomic write failed for %s", path)
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except:
                pass

def _is_benign_usermod_err(err_text):
    """
    خطاهایی که میتوان آن‌ها را بی‌خطر در نظر گرفت (مثل 'is currently used by process')
    """
    if not err_text:
        return False
    txt = err_text.lower()
    benign_patterns = [
        "currently used by process",
        "is currently used by process",
        "user .* is currently used by process",
        "cannot lock the user record",
    ]
    for p in benign_patterns:
        if p in txt:
            return True
    return False

def lock_user(username, reason="quota"):
    """
    Lock a Linux user for SSH tunnel-only usage (no interactive login).
    reason: "quota", "expire", or "manual"
    Returns True if limits file exists/was updated (logical success).
    """
    failures = []
    warnings = []
    successes = []

    try:
        # 1) Run main commands
        cmds = [
            ["sudo", "usermod", "-s", NOLOGIN_PATH, username],
            ["sudo", "usermod", "-d", "/nonexistent", username],
            ["sudo", "passwd", "-l", username],
        ]
        for cmd in cmds:
            rc, out, err = run_cmd(cmd)
            if rc == 0:
                successes.append(" ".join(cmd))
            else:
                # اگر خطا بی‌ضرر باشه، به warnings اضافه کن، وگرنه failures
                if _is_benign_usermod_err(err or out):
                    warnings.append(f"cmd warning: {' '.join(cmd)} | rc={rc} | msg={err or out}")
                else:
                    failures.append(f"cmd failed: {' '.join(cmd)} | rc={rc} | err={err or out}")

        # 2) Kill active sessions (pkill)
        rc, out, err = run_cmd(["sudo", "pkill", "-u", username])
        if rc in (0, 1):
            successes.append("pkill")
        else:
            failures.append(f"pkill rc={rc} err={err}")

        # 3) Update limits JSON (ضروری برای بات)
        limit_file_path = os.path.join(LIMITS_DIR, f"{username}.json")
        try:
            if os.path.exists(limit_file_path):
                try:
                    with open(limit_file_path, "r") as f:
                        user_data = json.load(f)
                except Exception:
                    user_data = {}
            else:
                user_data = {}

            user_data["is_blocked"] = True
            user_data["blocked_at"] = int(datetime.now().timestamp())
            user_data["block_reason"] = reason
            user_data["alert_sent"] = True

            os.makedirs(LIMITS_DIR, exist_ok=True)
            atomic_write(limit_file_path, user_data)
            successes.append("limits-file-updated")
        except Exception as e:
            failures.append(f"write limits failed: {e}")

        # 4) Remove iptables rule if exists
        rc, out, err = run_cmd(["id", "-u", username])
        uid = out.strip() if rc == 0 else ""
        if uid.isdigit():
            rc2, out2, err2 = run_cmd(["sudo", "iptables", "-D", "SSH_USERS", "-m", "owner", "--uid-owner", uid, "-j", "ACCEPT"])
            if rc2 == 0:
                successes.append("iptables-removed")
            else:
                rc_check, ocheck, echeck = run_cmd(["sudo", "iptables", "-C", "SSH_USERS", "-m", "owner", "--uid-owner", uid, "-j", "ACCEPT"])
                if rc_check == 0:
                    failures.append(f"iptables -D failed rc={rc2} err={err2}")
                else:
                    successes.append("iptables-not-present")
        else:
            warnings.append("cannot get uid for user")

        # 5) Send friendly telegram summary
        reason_map = {"quota": "اتمام حجم", "expire": "اتمام تاریخ انقضا", "manual": "قفل دستی"}
        header = f"🔒 تلاش برای قفل `{username}` — نتیجه:\n"
        summary_lines = []
        if failures:
            summary_lines.append("❌ خطا(های مهم) وجود دارد:")
            for f in failures:
                summary_lines.append(f"- {f}")
        if warnings and not failures:
            summary_lines.append("⚠️ هشدار(ها) وجود دارد (عملیات اصلی انجام شده):")
            for w in warnings:
                summary_lines.append(f"- {w}")
        if not failures and not warnings:
            summary_lines.append(f"✅ اکانت `{username}` با موفقیت به دلیل *{reason_map.get(reason, reason)}* مسدود شد.")

        # یک بلوک کدِ قابل کپی شامل جزئیات
        details = ""
        details += f"فایل محدودیت: {limit_file_path}\n"
        details += f"وضعیت فایل limits: {'به‌روزرسانی شد' if os.path.exists(limit_file_path) else 'وجود ندارد'}\n"
        details += f"موفقیت‌ها: {', '.join(successes) or '-'}\n"
        if warnings:
            details += f"هشدارها:\n" + "\n".join(warnings) + "\n"
        if failures:
            details += f"خطاها:\n" + "\n".join(failures) + "\n"
        details += f"\nلاگ: {LOG_FILE}"

        # ارسال پیام خلاصه + بلوک کد (برای کپی راحت)
        send_telegram_message(header + "\n" + "\n".join(summary_lines) + "\n\n" + "```\n" + details + "\n```")

        if failures:
            log.warning("lock_user partial failures for %s: %s", username, failures)
        else:
            log.info("User %s locked (reason=%s) — successes: %s warnings: %s", username, reason, successes, warnings)

        return os.path.exists(limit_file_path)

    except Exception:
        log.exception("Unexpected error in lock_user for %s", username)
        send_telegram_message(f"⚠️ خطای داخلی هنگام مسدودسازی `{username}` — جزئیات در لاگ.")
        return False

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 lock_user.py <username> [reason]")
        sys.exit(1)
    username = sys.argv[1]
    reason = sys.argv[2] if len(sys.argv) > 2 else "quota"
    ok = lock_user(username, reason)
    sys.exit(0 if ok else 2)


#EOF

##############

#قابل اجراش کن: 

#chmod +x /root/sshmanager/lock_user.py

