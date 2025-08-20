
cat > /usr/local/bin/log_user_traffic.py << 'EOF'

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, json, subprocess, time, pwd, tempfile, shutil

LIMITS_DIR = "/etc/sshmanager/limits"
#CHAIN_NAME = "SSH_USERS"
CHAIN_NAME = "SSH_USERS_OUT"
DEBUG_DIR  = "/etc/sshmanager/logs"
DEBUG_LOG  = os.path.join(DEBUG_DIR, "log_user_traffic.log")

CHAIN_RE   = re.compile(rf"^-A\s+{re.escape(CHAIN_NAME)}\b")
UID_OWNER  = re.compile(r"--uid-owner\s+(\d+)\b")
CGROUPPATH = re.compile(r"--path\s+([^\s]+)")
COUNTERS   = re.compile(r"(?:-c\s+(\d+)\s+(\d+)|\[(\d+):(\d+)\])")
COMMENT    = re.compile(r'-m\s+comment\s+--comment\s+"([^"]+)"')

def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} LOG: {msg}"
    print(line)
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    return p.returncode, (p.stdout or ""), (p.stderr or "")

def run_iptables_save():
    for cmd in (["/usr/sbin/iptables-save","-c"],
                ["iptables-save","-c"],
                ["/usr/sbin/iptables-legacy-save","-c"]):
        rc, out, err = run(cmd)
        if out.strip():
            return out
    for cmd in (["/usr/sbin/iptables-save"],
                ["iptables-save"],
                ["/usr/sbin/iptables-legacy-save"]):
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

def parse_chain_bytes(text: str):
    """
    bytes per username from SSH_USERS chain
    supports rules with mode in {owner, connmark, cgroup}
    """
    usage = {}
    if not text: return usage

    for ln in text.splitlines():
        if not CHAIN_RE.search(ln):
            continue

        mctr = COUNTERS.search(ln)
        if not mctr: continue
        g = mctr.groups()
        bytes_count = safe_int(g[1] or g[3], 0)
        if bytes_count <= 0: continue

        mcom = COMMENT.search(ln)
        user = None
        uid  = None
        if mcom:
            tags = {}
            for part in mcom.group(1).split(";"):
                part = part.strip()
                if "=" in part:
                    k,v = part.split("=",1)
                    tags[k.strip()] = v.strip()
                else:
                    tags[part] = True
            user = tags.get("sshmanager:user") or tags.get("user")
            uid  = safe_int(tags.get("uid"), None) if "uid" in tags else None

        if not user:
            mo = UID_OWNER.search(ln)
            if mo:
                uid = safe_int(mo.group(1), None)
                if uid is not None:
                    user = uid_to_username(uid)

        if not user:
            mp = CGROUPPATH.search(ln)
            if mp:
                m = re.search(r"user-(\d+)\.slice", mp.group(1))
                if m:
                    uid = safe_int(m.group(1), None)
                    if uid is not None:
                        user = uid_to_username(uid)

        if not user:
            continue

        usage[user] = usage.get(user, 0) + bytes_count

    return usage

def clamp_delta(cur: int, last: int | None) -> int:
    if last is None: return 0
    if cur >= last: return cur - last
    return cur  # counters reset

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

def to_int(x, d=0): return safe_int(x, d)

def maybe_lock_user(j: dict, username: str):
    now = int(time.time())
    used_bytes  = to_int(j.get("traffic_used_bytes", 0), 0)
    limit_bytes = to_int(j.get("traffic_limit_bytes", 0), 0)
    expire_ts   = to_int(j.get("expire_timestamp", 0), 0)
    over_quota = (limit_bytes > 0 and used_bytes >= limit_bytes)
    expired    = (expire_ts > 0 and now >= expire_ts)

    if j.get("is_blocked"): return False
    if not (over_quota or expired): return False

    reason = "quota" if over_quota else "expire"
    wrapper = "/root/sshmanager/lock_user.py"
    if os.path.exists(wrapper):
        try:
            p = subprocess.run(["/usr/bin/python3", wrapper, username, reason])
            if p.returncode != 0:
                raise RuntimeError(f"lock wrapper rc={p.returncode}")
        except Exception as e:
            log(f"lock_user error: {e}")

    j["is_blocked"] = True
    j["block_reason"] = reason
    j["alert_sent"] = True
    return True

def main():
    if not os.path.isdir(LIMITS_DIR):
        log(f"{LIMITS_DIR} missing.")
        return 0

    dump = run_iptables_save()
    if not dump:
        log("iptables-save produced no output.")
        return 0

    agg_bytes = parse_chain_bytes(dump)  # bytes per username

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

        cur_b = to_int(agg_bytes.get(username, 0), 0)
        last  = j.get("last_iptables_bytes", None)
        last  = last if isinstance(last, int) and last >= 0 else None
        delta = clamp_delta(cur_b, last)

        used_bytes_prev = to_int(j.get("traffic_used_bytes", 0), 0)
        used_bytes      = used_bytes_prev + max(0, delta)

        limit_kb          = to_int(j.get("limit", 0), 0)
        limit_bytes_alt   = to_int(j.get("traffic_limit_bytes", j.get("limit_bytes", 0)), 0)
        traffic_limit_b   = limit_bytes_alt
        if limit_kb > 0:
            traffic_limit_b = limit_kb * 1024
        elif limit_bytes_alt > 0 and limit_kb == 0:
            limit_kb = limit_bytes_alt // 1024

        j["last_iptables_bytes"] = int(cur_b)
        j["traffic_used_bytes"]  = int(used_bytes)
        j["traffic_limit_bytes"] = int(max(0, traffic_limit_b))
        j["used"]  = int(used_bytes // 1024)
        if limit_kb > 0:
            j["limit"] = int(limit_kb)
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
