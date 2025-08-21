
cat > /usr/local/bin/log_user_traffic.py << 'EOF'

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, json, subprocess, time, pwd, tempfile, shutil

LIMITS_DIR = "/etc/sshmanager/limits"
CHAINS_TO_LOG = ["SSH_USERS_OUT", "SSH_USERS_IN", "SSH_USERS_FWD"]  # ← IN اضافه شد
DEBUG_DIR  = "/etc/sshmanager/logs"
DEBUG_LOG  = os.path.join(DEBUG_DIR, "log_user_traffic.log")

CHAIN_RES  = {name: re.compile(rf"^-A\s+{re.escape(name)}\b") for name in CHAINS_TO_LOG}
UID_OWNER  = re.compile(r"--uid-owner\s+(\d+)\b")
COUNTERS   = re.compile(r"(?:-c\s+(\d+)\s+(\d+)|\[(\d+):(\d+)\])")
COMMENT    = re.compile(r'-m\s+comment\s+--comment\s+"([^"]+)"')

def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} LOG: {msg}"
    print(line)
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        with open(DEBUG_LOG, "a", encoding="utf-8") as f: f.write(line + "\n")
    except Exception: pass

def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
    return p.returncode, (p.stdout or ""), (p.stderr or "")

def run_iptables_save():
    for cmd in (["/usr/sbin/iptables-save","-c"], ["iptables-save","-c"], ["/usr/sbin/iptables-legacy-save","-c"]):
        rc, out, err = run(cmd)
        if out.strip(): return out
    for cmd in (["/usr/sbin/iptables-save"], ["iptables-save"], ["/usr/sbin/iptables-legacy-save"]):
        rc, out, err = run(cmd)
        if out.strip(): return out
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
    usage = {}
    if not text: return usage
    for ln in text.splitlines():
        if not any(r.search(ln) for r in CHAIN_RES.values()): continue
        mctr = COUNTERS.search(ln)
        if not mctr: continue
        g = mctr.groups()
        bytes_count = safe_int(g[1] or g[3], 0)
        if bytes_count <= 0: continue
        user = None
        mcom = COMMENT.search(ln)
        if mcom:
            tags = dict(part.split("=", 1) for part in mcom.group(1).split(";") if "=" in part)
            user = tags.get("sshmanager:user") or tags.get("user")
        if not user:
            mo = UID_OWNER.search(ln)
            if mo:
                uid = safe_int(mo.group(1), None)
                if uid is not None:
                    user = uid_to_username(uid)
        if user:
            usage[user] = usage.get(user, 0) + bytes_count
    return usage

def atomic_write_json(path, data):
    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

def clamp_delta(cur, last):
    if last is None: return 0
    if cur < last:    # counters reset
        return cur
    return cur - last

def main():
    if not os.path.isdir(LIMITS_DIR):
        log(f"{LIMITS_DIR} missing."); return 0
    dump = run_iptables_save()
    if not dump:
        log("iptables-save produced no output."); return 0

    agg_bytes = parse_chain_bytes(dump)

    updated = 0
    now = int(time.time())
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

        used_prev = safe_int(j.get("traffic_used_bytes", j.get("used", 0)*1024), 0)
        used_now  = used_prev + max(0, delta)

        # سازگاری با ساختار فعلی فایل‌های limits
        limit_kb        = safe_int(j.get("limit", 0), 0)
        limit_bytes_alt = safe_int(j.get("traffic_limit_bytes", j.get("limit_bytes", 0)), 0)
        limit_bytes     = limit_kb * 1024 if limit_kb > 0 else limit_bytes_alt

        j["last_iptables_bytes"] = int(cur_b)
        j["traffic_used_bytes"]  = int(used_now)
        j["traffic_limit_bytes"] = int(max(0, limit_bytes))
        j["used"] = int(used_now // 1024)
        if limit_kb > 0: j["limit"] = int(limit_kb)
        j["last_checked"] = now

        atomic_write_json(path, j)
        updated += 1

    log(f"Finished; updated {updated} users.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
EOF

#

chmod +x /usr/local/bin/log_user_traffic.py
