
#cat > /usr/local/bin/log_user_traffic.py << 'EOF'

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Scan SSH_USERS iptables counters and update /etc/sshmanager/limits/*.json
- Robust legacy/nft handling (tries iptables-save, fallback).
- No deletion of existing important fields.
- Optional auto-lock via wrapper /usr/local/bin/lock_user.sh <username> [reason].
"""
import os, re, json, time, subprocess

LIMITS_DIR = "/etc/sshmanager/limits"
CHAIN_NAME = "SSH_USERS"
LOG_DIR    = "/var/log/sshmanager"
DBG_LOG    = os.path.join(LOG_DIR, "log-user-traffic-debug.log")

# قفل خودکار (در صورت نیاز)
ENABLE_AUTO_LOCK = True
LOCK_USER_WRAPPER = "/usr/local/bin/lock_user.sh"   # رپر استاندارد (توصیه‌شده)

UID_RE   = re.compile(r"--uid-owner\s+(\d+)\b")
CTR_RE   = re.compile(r"\[(\d+):(\d+)\]")
CHAIN_RE = re.compile(rf"-A\s+{re.escape(CHAIN_NAME)}\b")

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} TRAF: {msg}"
    print(line)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(DBG_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def run_iptables_save():
    for cmd in (["/usr/sbin/iptables-save","-c"],
                ["iptables-save","-c"],
                ["/usr/sbin/iptables-legacy-save","-c"]):
        try:
            p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
            out = (p.stdout or "")
            if out.strip():
                return out
        except Exception as e:
            log(f"run {cmd!r} failed: {e}")
    # fallback (بدون counters مفید)
    for cmd in (["/usr/sbin/iptables-save"], ["iptables-save"], ["/usr/sbin/iptables-legacy-save"]):
        try:
            p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
            out = (p.stdout or "")
            if out.strip():
                return out
        except Exception as e:
            log(f"run {cmd!r} failed (noc): {e}")
    return ""

def parse_uid_bytes(dump):
    res = {}
    if not dump:
        return res
    for ln in dump.splitlines():
        if not CHAIN_RE.search(ln):
            continue
        if "--uid-owner" not in ln:
            continue
        m_uid = UID_RE.search(ln); m_ctr = CTR_RE.search(ln)
        if not m_uid: 
            continue
        uid = int(m_uid.group(1))
        bytes_count = int(m_ctr.group(2)) if m_ctr else 0
        res[uid] = bytes_count
    return res

def uid_to_username(uid):
    try:
        import pwd
        return pwd.getpwuid(uid).pw_name
    except Exception:
        return None

def to_int(x, d=0):
    try:
        return int(x)
    except Exception:
        try:
            return int(float(x))
        except Exception:
            return d

def clamp_delta(cur, last):
    # wrap/reset handling:
    if last is None:
        return 0
    if cur >= last:
        return cur - last
    # counters reset (iptables flush/restart) => کل cur را حساب کن
    return cur

def lock_user(username, reason="limit_exceeded"):
    """
    check=False می‌گذاریم تا کل فرآیند به‌خاطر یک خطای قفل نشکند.
    ولی کد بازگشتی را لاگ می‌کنیم.
    """
    try:
        p = subprocess.run([LOCK_USER_WRAPPER, username, reason], text=True)
        if p.returncode != 0:
            log(f"lock_user: non-zero rc={p.returncode} for {username} ({reason})")
            return False
        return True
    except FileNotFoundError:
        log("lock_user wrapper not found.")
        return False
    except Exception as e:
        log(f"lock_user error: {e}")
        return False

def main():
    dump = run_iptables_save()
    uid_bytes = parse_uid_bytes(dump)

    users_map = {}  # username -> cur_bytes
    for uid, cur_b in uid_bytes.items():
        name = uid_to_username(uid)
        if name:
            users_map[name] = cur_b

    updated = 0
    for fn in os.listdir(LIMITS_DIR):
        if not fn.endswith(".json"): 
            continue
        user = fn[:-5]
        path = os.path.join(LIMITS_DIR, fn)
        try:
            with open(path, "r", encoding="utf-8") as f:
                j = json.load(f)
        except Exception:
            j = {}

        cur_bytes = to_int(users_map.get(user, 0), 0)
        last = j.get("last_iptables_bytes", None)
        last = last if isinstance(last, int) and last >= 0 else None

        delta = clamp_delta(cur_bytes, last)
        used  = to_int(j.get("traffic_used_bytes", 0), 0) + delta

        j["last_iptables_bytes"] = int(cur_bytes)
        j["traffic_used_bytes"]   = int(used)
        j["last_checked"]         = int(time.time())

        # قفل خودکار اختیاری (اگر limit/expire تعریف کرده‌ای)
        limit_bytes = to_int(j.get("traffic_limit_bytes", j.get("limit_bytes", 0)), 0)
        is_blocked  = bool(j.get("is_blocked", False))
        if ENABLE_AUTO_LOCK and not is_blocked:
            expire_ts = to_int(j.get("expire_timestamp", 0), 0)
            now = int(time.time())
            over_quota = (limit_bytes > 0 and used >= limit_bytes)
            expired    = (expire_ts > 0 and now >= expire_ts)
            if over_quota or expired:
                if lock_user(user, "quota" if over_quota else "expire"):
                    j["is_blocked"]   = True
                    j["block_reason"] = "quota" if over_quota else "expire"
                    j["alert_sent"]   = True

        with open(path, "w", encoding="utf-8") as fw:
            json.dump(j, fw, ensure_ascii=False, indent=2)
        updated += 1

    log(f"Finished in ~0s; updated {updated} users.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())



#EOF

#chmod +x /usr/local/bin/log_user_traffic.py
