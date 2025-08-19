
cat > /usr/local/bin/log_user_traffic.py << 'EOF'

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
log_user_traffic.py — single source of truth for usage accounting

- Reads iptables-save -c counters in the SSH_USERS chain per UID
- Calculates delta vs last_iptables_bytes (handles wrap/flush safely)
- Updates both *bytes-based* and *KB-based* fields to stay backward-compatible:
    traffic_used_bytes      (bytes, authoritative)
    traffic_limit_bytes     (bytes, mirror of limit KB if present)
    used                    (KB, mirrored from bytes for UI/reporting)
    limit                   (KB, kept if already used by bot and UI)
- Optional auto lock when quota/expire exceeded (same behavior as before)
"""

import os, re, json, subprocess, time, pwd, tempfile, shutil

LIMITS_DIR = "/etc/sshmanager/limits"
CHAIN_NAME = "SSH_USERS"

# Debug logging (kept as in your current script)
DEBUG_DIR  = "/etc/sshmanager/logs"
DEBUG_LOG  = os.path.join(DEBUG_DIR, "log_user_traffic.log")

# Regex to parse `iptables-save -c` lines
CHAIN_RE = re.compile(rf"\b-A\s+{re.escape(CHAIN_NAME)}\b")
UID_RE   = re.compile(r"--uid-owner\s+(\d+)")
# Matches either "-c pkts bytes" or "[pkts:bytes]" styles
CTR_PAIR = re.compile(r"(?:-c\s+(\d+)\s+(\d+)|\[(\d+):(\d+)\])")

ENABLE_AUTO_LOCK = True
LOCK_USER_WRAPPER = "/root/sshmanager/lock_user.py"  # if exists

def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} INIT: {msg}"
    print(line)
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def safe_int(x, d=0):
    try:
        return int(x)
    except Exception:
        try:
            return int(float(x))
        except Exception:
            return d

def run(cmd):
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except Exception as e:
        return 999, "", f"EXC: {e}"

def run_iptables_save():
    # Try common variants that include counters
    for cmd in (["/usr/sbin/iptables-save","-c"],
                ["iptables-save","-c"],
                ["/usr/sbin/iptables-legacy-save","-c"]):
        rc, out, err = run(cmd)
        if out.strip():
            return out
    # Fallback without counters (won't update, but avoids crashing)
    for cmd in (["/usr/sbin/iptables-save"], ["iptables-save"], ["/usr/sbin/iptables-legacy-save"]):
        rc, out, err = run(cmd)
        if out.strip():
            return out
    return ""

def parse_uid_bytes(dump_text: str):
    """
    Returns dict: {uid: bytes} from iptables-save output for CHAIN_NAME
    """
    res = {}
    if not dump_text:
        return res
    for ln in dump_text.splitlines():
        if not CHAIN_RE.search(ln):
            continue
        if "--uid-owner" not in ln:
            continue

        m_uid = UID_RE.search(ln)
        if not m_uid:
            continue
        uid = safe_int(m_uid.group(1), -1)
        if uid < 0:
            continue

        # Find counters
        m_ctr = CTR_PAIR.search(ln)
        bytes_count = 0
        if m_ctr:
            # supports both styles; select the "bytes" capture
            # groups: (pk1, by1, pk2, by2)
            g = m_ctr.groups()
            bytes_count = safe_int(g[1] or g[3], 0)

        res[uid] = bytes_count
    return res

def uid_to_username(uid: int):
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return None

def clamp_delta(cur: int, last: int | None) -> int:
    """
    Prevent double counting; handle counter resets/rollovers.
    - If last is None -> delta=0 (first observation)
    - If cur >= last -> delta=cur-last
    - If cur < last -> assume counters reset -> delta=cur
    """
    if last is None:
        return 0
    if cur >= last:
        return cur - last
    # counter reset / flush
    return cur

def to_int(x, d=0):
    return safe_int(x, d)

def lock_user(username: str, reason="quota") -> bool:
    try:
        if not os.path.exists(LOCK_USER_WRAPPER):
            log("lock_user wrapper not found.")
            return False
        p = subprocess.run(["/usr/bin/python3", LOCK_USER_WRAPPER, username, reason], text=True)
        if p.returncode != 0:
            log(f"lock_user: non-zero rc={p.returncode} for {username} ({reason})")
            return False
        return True
    except Exception as e:
        log(f"lock_user error: {e}")
        return False

def atomic_write_json(path: str, data: dict):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d, text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        shutil.move(tmp, path)
    finally:
        try: os.unlink(tmp)
        except Exception: pass

def main():
    if not os.path.isdir(LIMITS_DIR):
        log(f"{LIMITS_DIR} missing.")
        return 0

    dump = run_iptables_save()
    if not dump:
        log("iptables-save produced no output.")
        return 0

    uid_bytes = parse_uid_bytes(dump)

    updated = 0
    for fn in os.listdir(LIMITS_DIR):
        if not fn.endswith(".json"):
            continue
        username = fn[:-5]
        path = os.path.join(LIMITS_DIR, fn)

        # فقط کاربرانی که rule دارند را آپدیت کن؛ بقیه دست‌نخورده بمانند
        # (اگر می‌خواهی همه را آپدیت کنی، شرط زیر را بردار)
        # ولی اگر فایل هست و rule نیست، همچنان last_checked را می‌زنیم
        cur_b = 0
        # ترجیح می‌دهیم از uid_bytes استفاده کنیم (سریع‌تر)
        try:
            uid = safe_int(subprocess.getoutput(f"id -u {username}").strip(), -1)
            if uid in uid_bytes:
                cur_b = to_int(uid_bytes.get(uid, 0), 0)
        except Exception:
            pass

        try:
            with open(path, "r", encoding="utf-8") as f:
                j = json.load(f) or {}
        except Exception:
            j = {}

        # خواندن آخرین شمارنده‌ی iptables
        last = j.get("last_iptables_bytes", None)
        last = last if isinstance(last, int) and last >= 0 else None

        # افزایش واقعی از آخرین مشاهده
        delta = clamp_delta(cur_b, last)

        # مصرف بایتِ مرجع
        used_bytes_prev = to_int(j.get("traffic_used_bytes", 0), 0)
        used_bytes = used_bytes_prev + max(0, delta)

        # سینک فیلدهای مرجع و قدیمی (KB)
        limit_kb = to_int(j.get("limit", 0), 0)
        limit_bytes_alt = to_int(j.get("traffic_limit_bytes", j.get("limit_bytes", 0)), 0)

        # اگر limit_kb داریم، نسخهٔ بایتی را هم آینه کنیم
        traffic_limit_bytes = limit_bytes_alt
        if limit_kb > 0:
            traffic_limit_bytes = limit_kb * 1024
        elif limit_bytes_alt > 0 and limit_kb == 0:
            # فقط بایت داشتیم؛ آینهٔ KB را تنظیم کن تا UI درست شود
            limit_kb = limit_bytes_alt // 1024

        # فیلدهای خروجی واحد‌مند
        j["last_iptables_bytes"] = int(cur_b)
        j["traffic_used_bytes"] = int(used_bytes)
        j["traffic_limit_bytes"] = int(max(0, traffic_limit_bytes))

        # فیلدهای سازگار با UI قدیمی (KB)
        j["used"]  = int(used_bytes // 1024)
        if limit_kb > 0:
            j["limit"] = int(limit_kb)

        j["last_checked"] = int(time.time())

        # قفل خودکار (منطبق با منطق فعلی)
        is_blocked = bool(j.get("is_blocked", False))
        if ENABLE_AUTO_LOCK and not is_blocked:
            expire_ts = to_int(j.get("expire_timestamp", 0), 0)
            now = int(time.time())
            # اولویت با نسخهٔ بایتی
            eff_limit_bytes = j.get("traffic_limit_bytes", 0)
            if not eff_limit_bytes and limit_kb > 0:
                eff_limit_bytes = limit_kb * 1024
            over_quota = (eff_limit_bytes > 0 and used_bytes >= eff_limit_bytes)
            expired = (expire_ts > 0 and now >= expire_ts)
            if over_quota or expired:
                reason = "quota" if over_quota else "expire"
                if lock_user(username, reason):
                    j["is_blocked"]   = True
                    j["block_reason"] = reason
                    j["alert_sent"]   = True

        atomic_write_json(path, j)
        updated += 1

    log(f"Finished; updated {updated} users.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())


EOF

#chmod +x /usr/local/bin/log_user_traffic.py
