# /usr/local/bin/check_users_expire.py
#cat > /usr/local/bin/check_users_expire.py << 'EOF'
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, json, subprocess, sys
from datetime import datetime

LIMITS_DIR = "/etc/sshmanager/limits"
REMOVE_IPTABLES_RULE = True
IPTABLES_CHAIN = "SSH_USERS"

def run(cmd):
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception as e:
        return 999, "", f"EXC: {e}"

def to_int(v, default=None):
    try:
        return int(v)
    except Exception:
        try:
            return int(float(v))
        except Exception:
            return default

def human_ts(ts):
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)

def log(msg):
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()

def delete_iptables_rule_for_uid(uid):
    changed = False
    while True:
        rc, _, _ = run(["iptables", "-D", IPTABLES_CHAIN, "-m", "owner", "--uid-owner", str(uid), "-j", "ACCEPT"])
        if rc == 0:
            changed = True
            log(f"  - iptables: Rule حذف شد (uid={uid})")
            continue
        else:
            break
    return changed

def first_existing_path(paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return None

def lock_user(username, remove_rule=REMOVE_IPTABLES_RULE):
    details = {"username": username, "steps": [], "warnings": [], "errors": []}
    success = True

    rc, out, err = run(["id", "-u", username])
    if rc != 0 or not out.isdigit():
        details["errors"].append(f"id -u failed: rc={rc}, err={err}")
        return False, details
    uid = int(out)
    details["uid"] = uid

    nologin_path = first_existing_path(["/usr/sbin/nologin", "/sbin/nologin", "/usr/bin/nologin"]) or "/bin/false"

    rc, _, err = run(["usermod", "-s", nologin_path, username])
    if rc != 0: success = False; details["errors"].append(f"usermod -s failed: {err}")
    else: details["steps"].append(f"shell -> {nologin_path}")

    rc, _, err = run(["usermod", "-d", "/nonexistent", username])
    if rc != 0: details["warnings"].append(f"usermod -d warn: {err}")
    else: details["steps"].append("home -> /nonexistent")

    rc, _, err = run(["passwd", "-l", username])
    if rc != 0: success = False; details["errors"].append(f"passwd -l failed: {err}")
    else: details["steps"].append("passwd locked")

    rc, _, err = run(["pkill", "-u", username])
    if rc not in (0, 1): details["warnings"].append(f"pkill warn: rc={rc} err={err}")
    else: details["steps"].append("sessions killed (if any)")

    if remove_rule:
        try:
            removed = delete_iptables_rule_for_uid(uid)
            if not removed: details["warnings"].append("Rule خاصی برای حذف یافت نشد")
        except Exception as e:
            success = False; details["errors"].append(f"iptables remove failed: {e}")
    else:
        details["steps"].append("iptables rule kept (per config)")

    return success, details

def process_user_file(path):
    try:
        with open(path, "r") as f:
            j = json.load(f)
    except Exception as e:
        return False, False, f"خواندن JSON خطا: {e}"

    username = os.path.basename(path)[:-5]
    expire_ts = j.get("expire_timestamp")
    now = int(datetime.now().timestamp())

    expire_ts_int = to_int(expire_ts, default=None)
    if not expire_ts_int or expire_ts_int <= 0:
        return False, True, f"{username}: expire_timestamp ندارد/نامعتبر است؛ کاری انجام نشد."

    if now < expire_ts_int:
        return False, True, f"{username}: هنوز منقضی نشده (expires at {human_ts(expire_ts_int)})."

    ok, info = lock_user(username, REMOVE_IPTABLES_RULE)
    if ok:
        j["is_blocked"] = True
        j["block_reason"] = "expire"
        j["alert_sent"] = True
        try:
            with open(path, "w") as f:
                json.dump(j, f, indent=4, ensure_ascii=False)
        except Exception as e:
            return True, False, f"{username}: قفل شد اما ذخیره JSON خطا: {e}"

        for s in info.get("steps", []): log(f"{username}: {s}")
        for w in info.get("warnings", []): log(f"{username}: WARN: {w}")
        return True, True, f"{username}: قفل شد (expire @ {human_ts(expire_ts_int)})."
    else:
        for e in info.get("errors", []): log(f"{username}: ERROR: {e}")
        for w in info.get("warnings", []): log(f"{username}: WARN: {w}")
        return True, False, f"{username}: قفل ناموفق."

def main():
    if not os.path.isdir(LIMITS_DIR):
        log(f"مسیر {LIMITS_DIR} یافت نشد؛ خروج.")
        sys.exit(0)

    files = [os.path.join(LIMITS_DIR, f) for f in os.listdir(LIMITS_DIR) if f.endswith(".json")]
    if not files:
        log("فایلی برای بررسی نیست.")
        sys.exit(0)

    any_error = False
    log(f"شروع بررسی انقضا در {LIMITS_DIR} (فایل‌ها: {len(files)})")
    for path in sorted(files):
        acted, ok, msg = process_user_file(path)
        if not ok: any_error = True
        log(msg)

    sys.exit(2 if any_error else 0)

if __name__ == "__main__":
    main()
#EOF

#chmod +x /usr/local/bin/check_users_expire.py
