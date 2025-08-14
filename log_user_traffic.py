
#cat > /usr/local/bin/log_user_traffic.py << 'EOF'
# /usr/local/bin/log_user_traffic.py
#!/usr/bin/env python3
import json, os, re, subprocess, time, pwd, tempfile

LIMITS_DIR = "/etc/sshmanager/limits"
DEBUG_DIR  = "/var/log/sshmanager"
DEBUG_LOG  = os.path.join(DEBUG_DIR, "log-user-traffic-debug.log")
CHAIN      = "SSH_USERS"

os.makedirs(DEBUG_DIR, exist_ok=True)

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(DEBUG_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")

UID_RE = re.compile(r"\[(\d+):(\d+)\].*?-A\s+%s\b.*?--uid-owner\s+(\d+)\b" % re.escape(CHAIN))

def ipt_save_lines():
    out = subprocess.check_output(["iptables-save","-c"], text=True, errors="ignore")
    return [ln for ln in out.splitlines() if CHAIN in ln]

def uid_to_name(uid):
    try:
        return pwd.getpwuid(int(uid)).pw_name
    except Exception:
        return None

def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def write_json_atomic(path, obj):
    d = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=False)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(d)
    os.replace(tmp, path)

def ipt_del_uid(uid):
    # حذف فقط رول owner همون UID (اگر وجود داشت)
    while True:
        try:
            subprocess.run(
                ["iptables","-D",CHAIN,"-m","owner","--uid-owner",str(uid),"-j","ACCEPT"],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except subprocess.CalledProcessError:
            break

def main():
    start_ts = time.time()
    log("="*20)
    log("اجرای log-user-traffic آغاز شد")

    # وجود chain
    rc = subprocess.run(["iptables","-S",CHAIN], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if rc.returncode != 0:
        log(f"⚠️ زنجیره {CHAIN} پیدا نشد")
        return

    for ln in ipt_save_lines():
        m = UID_RE.search(ln)
        if not m:
            continue
        pkts, bytes_str, uid_str = m.groups()
        uid = int(uid_str)
        bytes_now = int(bytes_str)

        username = uid_to_name(uid)
        if not username:
            log(f"UID {uid} → کاربر نامشخص، رد شد")
            continue

        limit_file = os.path.join(LIMITS_DIR, f"{username}.json")
        if not os.path.isfile(limit_file):
            log(f"کاربر {username} فایل محدودیت ندارد ({limit_file})")
            continue

        data = read_json(limit_file) or {}
        last_bytes = int(data.get("last_iptables_bytes", 0) or 0)
        used_kb    = int(data.get("used", 0) or 0)
        limit_kb   = int(data.get("limit", 0) or 0)
        utype      = str(data.get("type", "") or "")
        is_blocked = bool(data.get("is_blocked", False))

        log(f"کاربر: {username} | UID: {uid} | bytes فعلی: {bytes_now} | bytes قبلی: {last_bytes} | مصرف قبلی: {used_kb} KB")

        # delta
        if bytes_now >= last_bytes:
            diff = bytes_now - last_bytes
        else:
            # شمارنده reset شده (flush یا reboot)
            diff = bytes_now
            log(f"⚠️ شمارنده reset شده برای {username}")

        add_kb = diff // 1024
        if add_kb < 0:
            add_kb = 0

        new_used = used_kb + add_kb

        # بروزرسانی JSON
        data["last_iptables_bytes"] = bytes_now
        data["used"] = new_used
        data["last_checked"] = int(time.time())
        if "username" not in data:
            data["username"] = username
        if "is_blocked" not in data:
            data["is_blocked"] = False
        if "block_reason" not in data:
            data["block_reason"] = None

        write_json_atomic(limit_file, data)
        log(f"↪️ بروزرسانی شد: مصرف جدید {new_used} KB")

        # کنترل محدودیت
        if utype == "limited" and not is_blocked and limit_kb > 0:
            if new_used >= limit_kb:
                log(f"🚫 حجم کاربر {username} تمام شد، بلاک می‌شود")
                ipt_del_uid(uid)
                data["is_blocked"] = True
                data["block_reason"] = "limit_exceeded"
                write_json_atomic(limit_file, data)
            elif (new_used * 100) // limit_kb >= 90:
                # جای پیام تلگرام/بات
                log(f"⚠️ کاربر {username} بیش از 90٪ مصرف کرده")

    log("اجرای log-user-traffic پایان یافت")

if __name__ == "__main__":
    main()

#EOF

#chmod +x /usr/local/bin/log_user_traffic.py
