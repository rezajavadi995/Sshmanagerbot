
#cat > /usr/local/bin/log_user_traffic.py << 'EOF'

#!/usr/bin/env python3
import json, os, re, subprocess, time, pwd

LIMITS_DIR = "/etc/sshmanager/limits"
DEBUG_DIR  = "/var/log/sshmanager"
DEBUG_LOG  = os.path.join(DEBUG_DIR, "log-user-traffic-debug.log")

# زنجیره‌ای که شمارنده‌های TX+RX روی آن جمع می‌شوند
CHAIN_UIDS = "SSH_UIDS"

# قفل خودکار: اگر اسکریپت قفل موجود است، فعال می‌شود
# اگر نمی‌خواهی قفل خودکار باشد، این را False کن
ENABLE_AUTO_LOCK = False
LOCK_USER_CMD = "/usr/local/bin/lock_user.sh {username}"

os.makedirs(DEBUG_DIR, exist_ok=True)

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(DEBUG_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")

def pick_save_cmd():
    # بر اساس زنجیرهٔ هدف، هرکدام که واقعا آن را دارد انتخاب می‌شود
    for cmd in (["iptables-save","-c"], ["iptables-legacy-save","-c"], ["iptables-nft-save","-c"]):
        try:
            out = subprocess.check_output(cmd, text=True, errors="ignore")
            if CHAIN_UIDS in out:
                return cmd[0]
        except Exception:
            pass
    return "iptables-save"

SAVE_CMD = pick_save_cmd()

# پشتیبانی از فرم‌های مختلف connmark در iptables-save
# -m connmark --mark 1006
# یا حالت hex با ماسک در nft: ctmark match 0x3ee/0xffffffff
RE_DEC = re.compile(r"\[(\d+):(\d+)\].*?-A\s+%s\b.*?(?:-m\s+connmark\s+--mark\s+(\d+))" % CHAIN_UIDS)
RE_HEX = re.compile(r"\[(\d+):(\d+)\].*?-A\s+%s\b.*?(?:ctmark\s+match\s+0x([0-9A-Fa-f]+)(?:/0x[0-9A-Fa-f]+)?)" % CHAIN_UIDS)

def ipt_save_lines():
    out = subprocess.check_output([SAVE_CMD,"-c"], text=True, errors="ignore")
    return [ln for ln in out.splitlines() if (" -A "+CHAIN_UIDS+" ") in ln]

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
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def parse_uid_pkts_bytes(line):
    # تلاش برای DEC
    m = RE_DEC.search(line)
    if m:
        pkts, by, uid = m.groups()
        return int(uid), int(pkts), int(by)
    # تلاش برای HEX
    m = RE_HEX.search(line)
    if m:
        pkts, by, hexmark = m.groups()
        uid = int(hexmark, 16)
        return uid, int(pkts), int(by)
    return None

def try_lock_user(username):
    if not ENABLE_AUTO_LOCK:
        return False
    cmd = LOCK_USER_CMD.format(username=username)
    parts = cmd.split()
    if not os.path.exists(parts[0]):
        log(f"ℹ️ lock_user script not found: {parts[0]} (skipping)")
        return False
    try:
        subprocess.run(parts, check=True)
        log(f"🔒 lock_user executed for {username}")
        return True
    except Exception as e:
        log(f"❌ lock_user failed for {username}: {e}")
        return False

def main():
    log("="*20); log("اجرای log-user-traffic آغاز شد")

    lines = ipt_save_lines()
    if not lines:
        log(f"⚠️ هیچ خطی از {CHAIN_UIDS} پیدا نشد (SAVE_CMD={SAVE_CMD})")
        return

    for ln in lines:
        parsed = parse_uid_pkts_bytes(ln)
        if not parsed:
            continue
        uid, pkts, bytes_now = parsed

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

        log(f"کاربر: {username} | UID: {uid} | bytes فعلی (TX+RX): {bytes_now} | bytes قبلی: {last_bytes} | مصرف قبلی: {used_kb} KB")

        # delta
        diff = bytes_now - last_bytes
        if diff < 0:
            # reset (flush/reboot)
            diff = bytes_now
            log(f"⚠️ شمارنده reset شده برای {username}")

        add_kb  = max(diff // 1024, 0)
        new_used = used_kb + add_kb

        # بروزرسانی JSON
        data["username"] = username
        data["last_iptables_bytes"] = bytes_now
        data["used"] = new_used
        data["last_checked"] = int(time.time())
        data.setdefault("is_blocked", False)
        data.setdefault("block_reason", None)

        write_json_atomic(limit_file, data)
        log(f"↪️ بروزرسانی شد: مصرف جدید {new_used} KB")

        # کنترل محدودیت
        if utype == "limited" and (not is_blocked) and limit_kb > 0:
            if new_used >= limit_kb:
                log(f"🚫 حجم کاربر {username} تمام شد → بلاک")
                data["is_blocked"] = True
                data["block_reason"] = "limit_exceeded"
                write_json_atomic(limit_file, data)
                try_lock_user(username)
            elif (new_used * 100) // limit_kb >= 90:
                log(f"⚠️ کاربر {username} بیش از 90٪ مصرف کرده")
                # اینجا می‌تونی بات تلگرام رو نوتیفای کنی

    log("اجرای log-user-traffic پایان یافت")

if __name__ == "__main__":
    main()



#EOF

#chmod +x /usr/local/bin/log_user_traffic.py
