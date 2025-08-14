
#cat > /usr/local/bin/log_user_traffic.py << 'EOF'
#!/usr/bin/env python3
# /usr/local/bin/log_user_traffic.py
import os, json, pwd, subprocess, time, tempfile, shutil

LIMITS_DIR = "/etc/sshmanager/limits"
CHAIN_NAME = "SSH_USERS"

os.makedirs(LIMITS_DIR, exist_ok=True)

def run(cmd):
    return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()

def safe_int(x, default=0):
    try:
        return int(x)
    except:
        return default

def parse_iptables_save_counters():
    """
    تلاش ۱: iptables-save -c
    - برخی نسخه‌ها شکل '-c pkts bytes' دارند.
    - برخی دیگر شکل '[pkts:bytes]' دارند.
    خروجی: dict {uid:int -> bytes:int}
    """
    out = run(["iptables-save", "-c"])
    by_uid = {}
    for ln in out.splitlines():
        if f"-A {CHAIN_NAME} " not in ln:
            continue
        if "--uid-owner" not in ln:
            continue

        # استخراج bytes از دو حالت
        bytes_val = None

        # حالت 1: '-c pkts bytes'
        if " -c " in ln:
            try:
                # ... -c <pkts> <bytes> ...
                parts = ln.split()
                ci = parts.index("-c")
                pkts = safe_int(parts[ci+1])
                bts  = safe_int(parts[ci+2])
                bytes_val = bts
            except Exception:
                bytes_val = None

        # حالت 2: '[pkts:bytes]'
        if bytes_val is None and "[" in ln and "]" in ln:
            try:
                ib = ln.index("[")
                jb = ln.index("]", ib+1)
                pkts_bytes = ln[ib+1:jb]  # "pkts:bytes"
                pb = pkts_bytes.split(":")
                if len(pb) == 2:
                    bytes_val = safe_int(pb[1])
            except Exception:
                bytes_val = None

        if bytes_val is None:
            continue

        # استخراج UID
        uid = None
        parts = ln.split()
        for i, p in enumerate(parts):
            if p == "--uid-owner" and i + 1 < len(parts):
                uid = safe_int(parts[i+1], None)
                break
        if uid is None:
            # برخی نسخه‌ها 'owner UID match' دارند؛ fallback
            if "owner UID match" in ln:
                # تلاش بدوی: آخرین عدد را UID فرض می‌کنیم
                nums = [safe_int(tok, None) for tok in ln.replace(":", " ").split() if tok.isdigit()]
                uid = nums[-1] if nums else None

        if uid is not None:
            by_uid[uid] = bytes_val
    return by_uid

def parse_iptables_list_counters():
    """
    تلاش ۲: iptables -L CHAIN -v -n -x
    ستون‌ها: pkts bytes target prot opt in out source destination ...
    """
    try:
        out = run(["iptables", "-L", CHAIN_NAME, "-v", "-n", "-x"])
    except subprocess.CalledProcessError:
        return {}
    by_uid = {}
    for ln in out.splitlines():
        parts = ln.split()
        if len(parts) < 8:
            continue
        # دومین ستون bytes است
        bts = safe_int(parts[1], None)
        if bts is None:
            continue
        # UID را پیدا کن
        uid = None
        if "--uid-owner" in parts:
            i = parts.index("--uid-owner")
            if i + 1 < len(parts):
                uid = safe_int(parts[i+1], None)
        elif "owner" in parts and "UID" in parts and "match" in parts:
            # برخی ساختارها این‌طوری نشان می‌دهند
            for tok in parts[::-1]:
                if tok.isdigit():
                    uid = safe_int(tok, None)
                    break
        if uid is not None:
            by_uid[uid] = bts
    return by_uid

def iptables_bytes_by_uid():
    by_uid = parse_iptables_save_counters()
    if not by_uid:
        by_uid = parse_iptables_list_counters()
    return by_uid

def username_by_uid(uid: int):
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return None

def atomic_dump_json(path, data):
    d = json.dumps(data, ensure_ascii=False, indent=2)
    dirn = os.path.dirname(path)
    os.makedirs(dirn, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=dirn)
    with os.fdopen(fd, "w") as f:
        f.write(d)
    os.replace(tmp, path)

def main():
    by_uid = iptables_bytes_by_uid()
    now = int(time.time())

    # فقط برای UID های واقعی سیستم (>=1000 معمولاً)
    for uid, cur_bytes in by_uid.items():
        if uid < 1000:
            continue
        uname = username_by_uid(uid)
        if not uname:
            continue

        fpath = os.path.join(LIMITS_DIR, f"{uname}.json")
        if not os.path.exists(fpath):
            # اگر هنوز فایل حدّ وجود ندارد، از آن عبور کن
            continue

        try:
            with open(fpath, "r") as f:
                data = json.load(f)
        except Exception:
            data = {}

        # سازگاری با ساختار فعلی
        data.setdefault("username", uname)
        data.setdefault("type", "limited")
        data.setdefault("limit", 0)            # به MB
        data.setdefault("used", 0)             # به KB (سنت پروژه)
        data.setdefault("is_blocked", False)
        last_bytes = safe_int(data.get("last_iptables_bytes", None), None)

        if last_bytes is None:
            # اولین اجرا: فقط مقدار مرجع را ست کن (مصرف اضافه نکن)
            data["last_iptables_bytes"] = int(cur_bytes)
            data["last_checked"] = now
            atomic_dump_json(fpath, data)
            continue

        delta = int(cur_bytes) - int(last_bytes)
        if delta < 0:
            # کانتر ریست شده؛ از صفر شروع کن
            delta = int(cur_bytes)

        # پروژه تو مصرف را به «KB» ذخیره می‌کند
        used_kb = safe_int(data.get("used", 0))
        used_kb += int(delta / 1024)  # bytes→KB (floor)
        data["used"] = used_kb
        data["last_iptables_bytes"] = int(cur_bytes)
        data["last_checked"] = now

        atomic_dump_json(fpath, data)

if __name__ == "__main__":
    main()

EOF

#chmod +x /usr/local/bin/log_user_traffic.py
