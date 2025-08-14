
#cat > /usr/local/bin/log_user_traffic.py << 'EOF'

# /usr/local/bin/log_user_traffic.py
#!/usr/bin/env python3
import json, os, re, subprocess, time, pwd, tempfile, shutil
import fcntl
LOCK_FILE = "/run/log-user-traffic.lock"


LIMITS_DIR = "/etc/sshmanager/limits"
DEBUG_DIR  = "/var/log/sshmanager"
DEBUG_LOG  = os.path.join(DEBUG_DIR, "log-user-traffic-debug.log")
CHAIN      = "SSH_USERS"

os.makedirs(DEBUG_DIR, exist_ok=True)

def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(DEBUG_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")

def pick_cmd(*candidates):
    for c in candidates:
        if shutil.which(c):
            return c
    return None

# انتخاب باینری‌ها
IPT = pick_cmd("iptables-legacy", "iptables-nft", "iptables") or "iptables"
IPT_SAVE = pick_cmd("iptables-legacy-save", "iptables-nft-save", "iptables-save") or "iptables-save"

# سعی برای -w
def ipt_cmd(*args, check=False):
    # اگر -w پشتیبانی شد، استفاده کن
    try:
        subprocess.run([IPT, "-w", "-L"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        cmd = [IPT, "-w", *args]
    except subprocess.CalledProcessError:
        cmd = [IPT, *args]
    except Exception:
        cmd = [IPT, *args]
    return subprocess.run(cmd, check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

def ipt_check(args):
    return subprocess.run([IPT, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0

def ipt_save_lines():
    out = subprocess.check_output([IPT_SAVE, "-c"], text=True, errors="ignore")
    # فقط خطوط Chain خودمان
    return [ln for ln in out.splitlines() if f"-A {CHAIN}" in ln]

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
    d = json.dumps(obj, ensure_ascii=False, indent=2)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(d)
    os.replace(tmp, path)

def del_rule(uid, target):
    # همه رول‌های owner/target آن UID را حذف کن
    while True:
        rc = subprocess.run([IPT, "-D", CHAIN, "-m", "owner", "--uid-owner", str(uid), "-j", target],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if rc.returncode != 0:
            break

def ensure_reject_top(uid):
    """ابتدا همه ACCEPT های این UID را حذف کن، سپس REJECT را در ابتدای chain بگذار"""
    del_rule(uid, "ACCEPT")
    # اگر REJECT وجود ندارد، در ابتدای CHAIN درج کن
    if not ipt_check(["-C", CHAIN, "-m", "owner", "--uid-owner", str(uid), "-j", "REJECT"]):
        ipt_cmd("-I", CHAIN, "1", "-m", "owner", "--uid-owner", str(uid), "-j", "REJECT")

def ensure_accept_exists(uid):
    """اگر REJECTی برای UID هست حذف شود؛ اگر ACCEPT نیست، اضافه گردد (append)"""
    del_rule(uid, "REJECT")
    if not ipt_check(["-C", CHAIN, "-m", "owner", "--uid-owner", str(uid), "-j", "ACCEPT"]):
        ipt_cmd("-A", CHAIN, "-m", "owner", "--uid-owner", str(uid), "-j", "ACCEPT")

# فقط ACCEPT را برای شمارش در نظر بگیر (تا در حالت بلاک، مصرف جلو نرود)
UID_ACCEPT_RE = re.compile(
    r"\[(\d+):(\d+)\]\s+-A\s+%s\b.*?-m\s+owner\s+--uid-owner\s+(\d+)\b.*?-j\s+ACCEPT\b" % re.escape(CHAIN)
)

def main():
    start_ts = time.time()
    # قفل بین‌پردازه‌ای: هم‌زمان فقط یک نمونه اجرا شود
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    with open(LOCK_FILE, "w") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)

        log("="*20)
        log("اجرای log-user-traffic آغاز شد")

    # chain موجود است؟
    if subprocess.run([IPT, "-S", CHAIN], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode != 0:
        log(f"⚠️ زنجیره {CHAIN} پیدا نشد")
        return

    # خطوط iptables-save -c
    lines = ipt_save_lines()

    # map: uid -> bytes_on_accept_rule
    bytes_map = {}
    for ln in lines:
        m = UID_ACCEPT_RE.search(ln)
        if not m:
            continue
        pkts, bytes_str, uid_str = m.groups()
        uid = int(uid_str)
        bytes_map[uid] = int(bytes_str)

    # تمام UIDهایی که در سیستم وجود دارند و JSON دارند را در نظر بگیریم
    considered_uids = set(bytes_map.keys())

    # برای هر UID شمارش‌شده
    for uid, bytes_now in sorted(bytes_map.items()):
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
            # شمارنده reset شده (flush/reboot)
            diff = bytes_now
            log(f"⚠️ شمارنده reset شده برای {username}")

        add_kb = diff // 1024
        if add_kb < 0:
            add_kb = 0

        new_used = used_kb + add_kb

        # بروزرسانی JSON (اتمیک)
        data["last_iptables_bytes"] = bytes_now
        data["used"] = new_used
        data["last_checked"] = int(time.time())
        data.setdefault("username", username)
        data.setdefault("is_blocked", False)
        data.setdefault("block_reason", None)

        write_json_atomic(limit_file, data)
        log(f"↪️ بروزرسانی شد: مصرف جدید {new_used} KB")

        # منطق بلاک/هشدار
        if utype == "limited" and limit_kb > 0:
            # هشدار 90%
            if not is_blocked and (new_used * 100) // limit_kb >= 90 and (new_used < limit_kb):
                log(f"⚠️ کاربر {username} بیش از 90٪ مصرف کرده")

            # تمام شدن حجم
            if new_used >= limit_kb:
                if not is_blocked:
                    log(f"🚫 حجم کاربر {username} تمام شد، بلاک می‌شود")
                    ensure_reject_top(uid)
                    data["is_blocked"] = True
                    data["block_reason"] = "limit_exceeded"
                    write_json_atomic(limit_file, data)
                else:
                    # مطمئن شو REJECT در بالاست
                    ensure_reject_top(uid)
            else:
                # هنوز به حد نرسیده؛ اگر قبلاً بلاک بوده و الان is_blocked=false شده، آن‌بلاک کن
                if is_blocked and not bool(data.get("force_keep_blocked", False)):
                    # اگر دستی is_blocked را false کردند/بات آزاد کرد:
                    log(f"ℹ️ کاربر {username} دیگر بلاک علامت‌گذاری نشده؛ آن‌بلاک فایروال انجام می‌شود")
                    ensure_accept_exists(uid)
                    data["is_blocked"] = False
                    data["block_reason"] = None
                    write_json_atomic(limit_file, data)
        else:
            # کاربر نامحدود یا limit=0 → اگر به اشتباه REJECT دارد، پاک و ACCEPT را مطمئن کن
            ensure_accept_exists(uid)
            if is_blocked:
                data["is_blocked"] = False
                data["block_reason"] = None
                write_json_atomic(limit_file, data)
                log(f"ℹ️ کاربر {username} نامحدود/limit=0 است؛ از حالت بلاک خارج شد")

    log("اجرای log-user-traffic پایان یافت")

if __name__ == "__main__":
    main()


#EOF

#chmod +x /usr/local/bin/log_user_traffic.py
