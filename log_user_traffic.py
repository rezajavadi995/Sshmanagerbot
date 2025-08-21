
cat > /usr/local/bin/log_user_traffic.py << 'EOF'

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, json, subprocess, time, pwd, tempfile, shutil

LIMITS_DIR = "/etc/sshmanager/limits"
CHAINS_TO_LOG = ["SSH_USERS_OUT", "SSH_USERS_FWD"]

LOG_DIR = "/var/log/sshmanager"
DEBUG_LOG = os.path.join(LOG_DIR, "log_user_traffic.log")
LOCK_SCRIPT = "/root/sshmanager/lock_user.py"

# --- regex ها: کانترها، owner و comment (سازگار با "، '، بدون کوت)
CHAIN_RES = {name: re.compile(rf"^-A\s+{re.escape(name)}\b") for name in CHAINS_TO_LOG}
COUNTERS  = re.compile(r"(?:-c\s+(\d+)\s+(\d+)|\[(\d+):(\d+)\])")
UID_OWNER = re.compile(r"--uid-owner\s+(\d+)\b")
COMMENT   = re.compile(r'-m\s+comment\s+--comment\s+(?:"([^"]+)"|\'([^\']+)\'|([^\s]+))')

def ensure_logdir():
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
    except Exception:
        pass

def log(msg):
    ensure_logdir()
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    try:
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line)

def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return p.returncode, p.stdout or "", p.stderr or ""

def run_iptables_save():
    # اول تلاش با -c برای کانتر دقیق
    for cmd in (["/usr/sbin/iptables-save","-c"], ["iptables-save","-c"], ["/usr/sbin/iptables-legacy-save","-c"]):
        rc, out, err = run(cmd)
        if out.strip():
            return out
    # فول‌بک بدون -c
    for cmd in (["/usr/sbin/iptables-save"], ["iptables-save"], ["/usr/sbin/iptables-legacy-save"]):
        rc, out, err = run(cmd)
        if out.strip():
            return out
    return ""

def safe_int(x, d=0):
    try: return int(x)
    except Exception:
        try: return int(float(x))
        except Exception: return d

def uid_to_username(uid: int):
    try: return pwd.getpwuid(uid).pw_name
    except KeyError: return None

def _parse_comment_user(text: str):
    # "sshmanager:user=<u>;uid=<id>;..." → dict
    parts = {}
    for seg in (text or "").split(";"):
        if "=" in seg:
            k, v = seg.split("=", 1)
            parts[k.strip()] = v.strip()
    return parts.get("sshmanager:user") or parts.get("user") or None

def parse_chain_bytes(save_text: str):
    """
    iptables-save خروجی
    → جمعِ bytes برای هر username از چند chain هدف
    """
    usage = {}
    if not save_text:
        return usage

    for ln in save_text.splitlines():
        # فقط خطوطی که مربوط به chainهای هدف هستند
        ok = False
        for _, chain_re in CHAIN_RES.items():
            if chain_re.search(ln):
                ok = True
                break
        if not ok:
            continue

        mctr = COUNTERS.search(ln)
        if not mctr:
            continue
        g = mctr.groups()
        # bytes در گروه 2 یا 4 هست
        bytes_count = safe_int(g[1] or g[3], 0)
        if bytes_count <= 0:
            continue

        user = None
        mcom = COMMENT.search(ln)
        if mcom:
            # گروه 1 یا 2 یا 3 می‌تونه پر باشه
            comment_text = mcom.group(1) or mcom.group(2) or mcom.group(3) or ""
            user = _parse_comment_user(comment_text)

        if not user:
            mo = UID_OWNER.search(ln)
            if mo:
                uid = safe_int(mo.group(1), -1)
                if uid >= 0:
                    user = uid_to_username(uid)

        if user:
            usage[user] = usage.get(user, 0) + bytes_count

    return usage

def clamp_delta(cur: int, last: int | None) -> int:
    # اگر last نداریم، مصرف دفعات بعد محاسبه می‌شه (اینجا صفر)
    if last is None:
        return 0
    if cur >= last:
        return cur - last
    # ریست کانتر → همون cur مصرف جدید این دوره ست
    return cur

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

def maybe_lock_user(j: dict, username: str):
    now = int(time.time())
    used_bytes  = safe_int(j.get("traffic_used_bytes", 0), 0)
    limit_bytes = safe_int(j.get("traffic_limit_bytes", 0), 0)
    expire_ts   = safe_int(j.get("expire_timestamp", 0), 0)

    over_quota = (limit_bytes > 0 and used_bytes >= limit_bytes)
    expired    = (expire_ts > 0 and now >= expire_ts)

    if j.get("is_blocked"):
        return False
    if not (over_quota or expired):
        return False

    reason = "quota" if over_quota else "expire"
    rc, out, err = run(["/usr/bin/python3", LOCK_SCRIPT, username, reason])
    log(f"LOCK {username} reason={reason} rc={rc} out={out.strip()} err={err.strip()}")
    if rc == 0:
        j["is_blocked"] = True
        j["block_reason"] = reason
        j["blocked_at"] = now
        return True
    return False

def main():
    if not os.path.isdir(LIMITS_DIR):
        log(f"limits dir not found: {LIMITS_DIR}")
        return 0

    save_text = run_iptables_save()
    if not save_text:
        log("iptables-save output is empty")
        return 0

    agg_bytes = parse_chain_bytes(save_text)
    updated = 0

    for fn in os.listdir(LIMITS_DIR):
        if not fn.endswith(".json"): continue
        username = fn[:-5]
        path = os.path.join(LIMITS_DIR, fn)

        try:
            with open(path, "r", encoding="utf-8") as f:
                j = json.load(f) or {}
        except Exception:
            j = {}

        cur_b = safe_int(agg_bytes.get(username, 0), 0)
        last  = j.get("last_iptables_bytes", None)
        last  = last if isinstance(last, int) and last >= 0 else None
        delta = clamp_delta(cur_b, last)

        used_bytes_prev = safe_int(j.get("traffic_used_bytes", 0), 0)
        used_bytes      = used_bytes_prev + max(0, delta)

        # limit: KB اصلی شماست
        limit_kb        = safe_int(j.get("limit", 0), 0)
        limit_bytes_alt = safe_int(j.get("traffic_limit_bytes", j.get("limit_bytes", 0)), 0)

        traffic_limit_b = 0
        if limit_kb > 0:
            traffic_limit_b = limit_kb * 1024
        elif limit_bytes_alt > 0 and limit_kb == 0:
            traffic_limit_b = limit_bytes_alt
            limit_kb = limit_bytes_alt // 1024

        j["last_iptables_bytes"] = int(cur_b)
        j["traffic_used_bytes"]  = int(used_bytes)
        j["traffic_limit_bytes"] = int(max(0, traffic_limit_b))
        j["used"]   = int(used_bytes // 1024)   # ← برای ربات (KB)
        if limit_kb > 0:
            j["limit"] = int(limit_kb)          # ← برای ربات (KB)
        j["last_checked"] = int(time.time())

        maybe_lock_user(j, username)
        atomic_write_json(path, j)
        updated += 1

    log(f"Finished; updated {updated} users.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
EOF

#

chmod +x /usr/local/bin/log_user_traffic.py
