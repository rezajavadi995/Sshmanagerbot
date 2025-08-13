#فایل چک یوزر برای محدود سازی دسترسی کاربر بعد پیام هشدار از ربات  وقتی تاریخ اکانت کاربر تموم شد

#cat > /usr/local/bin/check_users_expire.py << 'EOF'
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_users_expire.py — نسخه نهایی
- قفل خودکار کاربران منقضی‌شده بر مبنای expire_timestamp در /etc/sshmanager/limits/<user>.json
- بازگرداندن کد وضعیت مناسب برای systemd
- گزارش دقیق مرحله‌به‌مرحله
- امکان حذف Rule کاربر از chain: SSH_USERS (با تنظیم REMOVE_IPTABLES_RULE)
"""

import os
import json
import subprocess
import sys
from datetime import datetime

# ===== تنظیمات =====
LIMITS_DIR = "/etc/sshmanager/limits"
REMOVE_IPTABLES_RULE = True         # اگر می‌خواهی Rule حذف شود True کن
IPTABLES_CHAIN = "SSH_USERS"

# ===== ابزار =====
def run(cmd):
    """Run a command, return (rc, stdout_str, stderr_str)."""
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()
    except Exception as e:
        return 999, "", f"EXC: {e}"

def first_existing_path(paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return None

def to_int(v, default=None):
    try:
        return int(v)
    except Exception:
        try:
            # گاهی به‌صورت رشته عددی میاد
            return int(float(v))
        except Exception:
            return default

def human_ts(ts):
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(ts)

def log(msg):
    # خروجی ساده برای journalctl
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()

# ===== عملیات روی کاربر =====
def delete_iptables_rule_for_uid(uid):
    """
    Rule کاربر را از chain حذف می‌کند (اگر چندتا بود، همه را حذف می‌کند).
    با iptables -D در یک حلقه تا وقتی خطا بده (یعنی چیزی نبود).
    """
    changed = False
    while True:
        rc, _, _ = run(["sudo", "iptables", "-D", IPTABLES_CHAIN, "-m", "owner", "--uid-owner", str(uid), "-j", "ACCEPT"])
        if rc == 0:
            changed = True
            log(f"  - iptables: Rule حذف شد (uid={uid})")
            continue
        else:
            break
    return changed

def lock_user(username, remove_rule=REMOVE_IPTABLES_RULE):
    """
    کاربر را قفل می‌کند و در صورت نیاز Rule iptables او را حذف می‌کند.
    خروجی: (success: bool, details: dict)
    """
    details = {"username": username, "steps": [], "warnings": [], "errors": []}
    success = True

    # پیدا کردن UID
    rc, out, err = run(["id", "-u", username])
    if rc != 0 or not out.isdigit():
        details["errors"].append(f"id -u failed: rc={rc}, err={err}")
        return False, details
    uid = int(out)
    details["uid"] = uid

    # انتخاب nologin
    nologin_path = first_existing_path(["/usr/sbin/nologin", "/sbin/nologin", "/usr/bin/nologin"])
    if not nologin_path:
        details["warnings"].append("nologin پیدا نشد؛ از /bin/false استفاده می‌شود")
        nologin_path = "/bin/false"

    # 1) تغییر shell
    rc, _, err = run(["sudo", "usermod", "-s", nologin_path, username])
    if rc != 0:
        success = False
        details["errors"].append(f"usermod -s failed: {err}")
    else:
        details["steps"].append(f"shell -> {nologin_path}")

    # 2) تغییر home به /nonexistent (ایمن)
    rc, _, err = run(["sudo", "usermod", "-d", "/nonexistent", username])
    if rc != 0:
        # غیر بحرانی
        details["warnings"].append(f"usermod -d warn: {err}")
    else:
        details["steps"].append("home -> /nonexistent")

    # 3) قفل پسورد
    rc, _, err = run(["sudo", "passwd", "-l", username])
    if rc != 0:
        success = False
        details["errors"].append(f"passwd -l failed: {err}")
    else:
        details["steps"].append("passwd locked")

    # 4) بستن نشست‌ها
    rc, _, err = run(["sudo", "pkill", "-u", username])
    if rc not in (0, 1):  # 0: پروسه‌ها کشته شدند، 1: پروسه‌ای نبود
        details["warnings"].append(f"pkill warn: rc={rc}, err={err}")
    else:
        details["steps"].append("sessions killed (if any)")

    # 5) iptables (اختیاری)
    if remove_rule:
        try:
            removed = delete_iptables_rule_for_uid(uid)
            if not removed:
                details["warnings"].append("Rule خاصی برای حذف یافت نشد (ممکن است قبلاً حذف شده باشد)")
        except Exception as e:
            success = False
            details["errors"].append(f"iptables remove failed: {e}")
    else:
        details["steps"].append("iptables rule kept (per config)")

    return success, details

# ===== منطق اصلی =====
def process_user_file(path):
    """
    فایل limit را می‌خواند، در صورت انقضا قفل می‌کند و JSON را به‌روزرسانی می‌کند.
    خروجی: (action_taken: bool, success: bool, msg: str)
    """
    try:
        with open(path, "r") as f:
            j = json.load(f)
    except Exception as e:
        return False, False, f"خواندن JSON خطا: {e}"

    username = os.path.basename(path)[:-5]
    expire_ts = j.get("expire_timestamp")
    now = int(datetime.now().timestamp())

    # نرمال‌سازی expire_ts
    expire_ts_int = to_int(expire_ts, default=None)
    if not expire_ts_int or expire_ts_int <= 0:
        return False, True, f"{username}: expire_timestamp ندارد/نامعتبر است؛ کاری انجام نشد."

    if now < expire_ts_int:
        return False, True, f"{username}: هنوز منقضی نشده (expires at {human_ts(expire_ts_int)})."

    # منقضی شده → قفل
    ok, info = lock_user(username, REMOVE_IPTABLES_RULE)
    if ok:
        # به‌روزرسانی JSON
        j["is_blocked"] = True
        j["block_reason"] = "expire"
        j["alert_sent"] = True
        try:
            with open(path, "w") as f:
                json.dump(j, f, indent=4, ensure_ascii=False)
        except Exception as e:
            # قفل انجام شده ولی ذخیره JSON خطا داده
            return True, False, f"{username}: قفل شد اما ذخیره JSON خطا: {e}"
        # گزارش جزئیات
        for s in info.get("steps", []):
            log(f"{username}: {s}")
        for w in info.get("warnings", []):
            log(f"{username}: WARN: {w}")
        return True, True, f"{username}: قفل شد (expire @ {human_ts(expire_ts_int)})."
    else:
        # خطا در قفل
        for e in info.get("errors", []):
            log(f"{username}: ERROR: {e}")
        for w in info.get("warnings", []):
            log(f"{username}: WARN: {w}")
        return True, False, f"{username}: قفل ناموفق."

def main():
    if not os.path.isdir(LIMITS_DIR):
        log(f"مسیر {LIMITS_DIR} یافت نشد؛ خروج.")
        sys.exit(0)

    files = [os.path.join(LIMITS_DIR, f) for f in os.listdir(LIMITS_DIR) if f.endswith(".json")]
    if not files:
        log("فایلی برای بررسی نیست.")
        sys.exit(0)

    any_action = False
    any_error = False

    log(f"شروع بررسی انقضا در {LIMITS_DIR} (فایل‌ها: {len(files)})")
    for path in sorted(files):
        acted, ok, msg = process_user_file(path)
        any_action = any_action or acted
        if not ok:
            any_error = True
        log(msg)

    if any_error:
        # قفل انجام شده ولی برخی خطا/هشدار جدی داشتیم
        sys.exit(2)
    else:
        sys.exit(0)

if __name__ == "__main__":
    main()

        
#EOF

###################################

#دسترسی اجرا بده بهش بعدش: 
#chmod +x /usr/local/bin/check_users_expire.py
##################amu reza####################


