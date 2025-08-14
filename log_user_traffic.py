
#cat > /usr/local/bin/log_user_traffic.py << 'EOF'
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
log_user_traffic.py
ثبت ترافیک کاربران SSH محدودحجمی + بلاک خودکار
نسخه‌ی نهایی پایتون (ادغام‌شده با منطق Bash دیباگ) و سازگار با ساختار فعلی ربات
"""

import os, json, pwd, time, subprocess, tempfile

LIMITS_DIR = "/etc/sshmanager/limits"
DEBUG_LOG  = "/var/log/sshmanager/log-user-traffic-debug.log"
CHAIN_NAME = "SSH_USERS"

os.makedirs(os.path.dirname(DEBUG_LOG), exist_ok=True)
os.makedirs(LIMITS_DIR, exist_ok=True)

# ---------- ابزار لاگ ----------
def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    try:
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write("====================\n" if "آغاز شد" in msg else "")
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass

# ---------- اجرای دستور سیستم ----------
def run(cmd):
    return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()

def safe_int(x, default=0):
    try:
        return int(x)
    except Exception:
        return default

# ---------- خواندن شمارنده‌ها از iptables ----------
def parse_save():
    """
    iptables-save -c
    خطوط نمونه:
    [95095:11561601] -A SSH_USERS -m owner --uid-owner 1006 -j ACCEPT
    یا بعضی نسخه‌ها: -c pkts bytes
    """
    res = {}
    try:
        out = run(["iptables-save", "-c"])
    except Exception:
        return res

    for ln in out.splitlines():
        if f"-A {CHAIN_NAME} " not in ln or "--uid-owner" not in ln:
            continue

        # bytes: اولویت با فرمت [pkts:bytes]
        bts = None
        if ln.startswith("[") and "]" in ln:
            try:
                inside = ln[1:ln.index("]")]
                parts = inside.split(":")
                if len(parts) == 2:
                    bts = safe_int(parts[1], None)
            except Exception:
                bts = None

        # جایگزین: -c pkts bytes
        if bts is None and " -c " in ln:
            try:
                sp = ln.split()
                i = sp.index("-c")
                bts = safe_int(sp[i+2], None)  # -c pkts bytes
            except Exception:
                bts = None

        if bts is None:
            continue

        # uid
        uid = None
        sp = ln.split()
        for i, p in enumerate(sp):
            if p == "--uid-owner" and i + 1 < len(sp):
                uid = safe_int(sp[i+1], None)
                break
        if uid is not None:
            res[uid] = bts
    return res

def parse_list():
    """
    iptables -L SSH_USERS -v -n -x
    ستون دوم bytes است (با -x اعداد دقیق)
    """
    res = {}
    try:
        out = run(["iptables", "-L", CHAIN_NAME, "-v", "-n", "-x"])
    except Exception:
        return res

    for ln in out.splitlines():
        sp = ln.split()
        # عبور از هدرها
        if not sp or sp[0] in ("Chain", "pkts", "target"):
            continue
        if len(sp) < 8:
            continue

        # bytes در ایندکس 1
        bts = safe_int(sp[1], None)
        if bts is None:
            continue

        # پیدا کردن --uid-owner
        uid = None
        if "--uid-owner" in sp:
            i = sp.index("--uid-owner")
            if i + 1 < len(sp):
                uid = safe_int(sp[i+1], None)

        if uid is not None:
            res[uid] = bts
    return res

# ---------- JSON ----------
def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def atomic_save_json(path, data):
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", dir=os.path.dirname(path))
    os.close(tmp_fd)
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)

# ---------- بلاک iptables ----------
def remove_accept_rule_for_uid(uid):
    # تلاش برای حذف rule ACCEPT مربوط به UID
    try:
        subprocess.run(
            ["iptables", "-D", CHAIN_NAME, "-m", "owner", "--uid-owner", str(uid), "-j", "ACCEPT"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
    except Exception:
        pass

# ---------- اصلی ----------
def main():
    log("اجرای log-user-traffic آغاز شد")

    # وجود chain
    try:
        run(["iptables", "-S", CHAIN_NAME])
    except Exception:
        log(f"⚠️ زنجیره {CHAIN_NAME} پیدا نشد")
        return  # امن‌تر از exit(1) برای سرویس‌های تریگر شده

    # اولویت با iptables-save -c
    by_uid = parse_save()
    if not by_uid:
        by_uid = parse_list()

    if not by_uid:
        log("⚠️ هیچ شمارنده‌ای یافت نشد (by_uid تهی)")
        log("اجرای log-user-traffic پایان یافت")
        return

    now = int(time.time())

    for uid, cur_bytes in by_uid.items():
        # کاربران سیستمی رو رد کن، ولی 65534 (nobody) هم رد میشه
        if uid is None or uid < 1000:
            continue

        # تبدیل UID ↔ username (حتی اگر nologin باشد مهم نیست)
        try:
            username = pwd.getpwuid(uid).pw_name
        except KeyError:
            log(f"UID {uid} → کاربر نامشخص، رد شد")
            continue

        limit_file = os.path.join(LIMITS_DIR, f"{username}.json")
        if not os.path.exists(limit_file):
            log(f"کاربر {username} فایل محدودیت ندارد ({limit_file})")
            continue

        data = load_json(limit_file)
        # مقداردهی اولیه کلیدها
        data.setdefault("username", username)
        data.setdefault("type", "limited")  # یا free در ربات شما
        data.setdefault("limit", 0)         # واحد: KB
        data.setdefault("used", 0)          # واحد: KB
        data.setdefault("is_blocked", False)
        data.setdefault("block_reason", None)

        used_kb  = safe_int(data.get("used", 0), 0)
        last_bts = data.get("last_iptables_bytes", None)

        log(f"کاربر: {username} | UID: {uid} | bytes فعلی: {cur_bytes} | bytes قبلی: {last_bts if last_bts is not None else 0} | مصرف قبلی: {used_kb} KB")

        # اولین اجرا برای این کاربر
        if last_bts is None:
            data["last_iptables_bytes"] = int(cur_bytes)
            data["last_checked"] = now
            atomic_save_json(limit_file, data)
            log(f"مقداردهی اولیه شمارنده برای {username}")
            continue

        # محاسبه delta
        delta = int(cur_bytes) - int(last_bts)
        if delta < 0:
            # reset شده
            log(f"⚠️ شمارنده reset شده برای {username}")
            delta = int(cur_bytes)

        # افزودن مصرف (bytes -> KB)
        if delta > 0:
            used_kb += int(delta / 1024)

        data["used"] = used_kb
        data["last_iptables_bytes"] = int(cur_bytes)
        data["last_checked"] = now

        atomic_save_json(limit_file, data)
        log(f"↪️ بروزرسانی شد: مصرف جدید {used_kb} KB")

        # بررسی محدودیت‌ها
        acc_type   = str(data.get("type", "limited"))
        is_blocked = bool(data.get("is_blocked", False))
        limit_kb   = safe_int(data.get("limit", 0), 0)

        if acc_type == "limited" and not is_blocked and limit_kb > 0:
            if used_kb >= limit_kb:
                log(f"🚫 حجم کاربر {username} تمام شد، بلاک می‌شود")
                # حذف Rule پذیرش
                remove_accept_rule_for_uid(uid)
                # به‌روز رسانی وضعیت بلاک
                data["is_blocked"] = True
                data["block_reason"] = "limit_exceeded"
                atomic_save_json(limit_file, data)
            else:
                try:
                    percent = int((used_kb * 100) / max(limit_kb, 1))
                except Exception:
                    percent = 0
                if percent >= 90:
                    # این هشدار را ربات تلگرام شما مصرف می‌کند
                    log(f"⚠️ کاربر {username} بیش از 90٪ مصرف کرده (≈{percent}٪)")
    log("اجرای log-user-traffic پایان یافت")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"❌ خطای غیرمنتظره: {e}")
        # عمداً raise نمی‌کنیم تا سرویس کرش نده


#EOF

#chmod +x /usr/local/bin/log_user_traffic.py
