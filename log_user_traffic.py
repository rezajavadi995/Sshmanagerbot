
cat > /usr/local/bin/log_user_traffic.py << 'EOF'

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, json, pwd, subprocess, time

LIMITS_DIR = "/etc/sshmanager/limits"
CHAIN_NAME = "SSH_USERS"
DEBUG_DIR  = "/var/log/sshmanager"
DEBUG_LOG  = os.path.join(DEBUG_DIR, "init-last-iptables-debug.log")

CHAIN_RE   = re.compile(rf"^-A\s+{CHAIN_NAME}\b")
COUNTERS   = re.compile(r"(?:-c\s+(\d+)\s+(\d+)|\[(\d+):(\d+)\])")
COMMENT    = re.compile(r'-m\s+comment\s+--comment\s+"([^"]+)"')
UID_OWNER  = re.compile(r"--uid-owner\s+(\d+)\b")
CGROUPPATH = re.compile(r"--path\s+([^\s]+)")

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} INIT: {msg}"
    print(line)
    try:
        os.makedirs(DEBUG_DIR, exist_ok=True)
        with open(DEBUG_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def run(cmd):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)

def run_iptables_save():
    for cmd in (["/usr/sbin/iptables-save","-c"], ["iptables-save","-c"], ["/usr/sbin/iptables-legacy-save","-c"]):
        p = run(cmd)
        out = (p.stdout or "")
        if out.strip():
            return out
    for cmd in (["/usr/sbin/iptables-save"], ["iptables-save"], ["/usr/sbin/iptables-legacy-save"]):
        p = run(cmd)
        out = (p.stdout or "")
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

def current_bytes_per_user(text: str):
    agg = {}
    if not text: return agg
    for ln in text.splitlines():
        if not CHAIN_RE.search(ln): continue
        mctr = COUNTERS.search(ln)
        if not mctr: continue
        g = mctr.groups()
        bytes_count = safe_int(g[1] or g[3], 0)
        if bytes_count <= 0: continue

        user = None
        mcom = COMMENT.search(ln)
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

        if not user:
            mo = UID_OWNER.search(ln)
            if mo:
                uid = safe_int(mo.group(1), None)
                if uid is not None:
                    user = uid_to_username(uid)

        if not user and mcom:
            # cgroup path fallback (قدیمی)
            txt = mcom.group(1)
            m = re.search(r"user-(\d+)\.slice", txt)
            if m:
                uid = safe_int(m.group(1), None)
                if uid is not None:
                    user = uid_to_username(uid)

        if user:
            agg[user] = agg.get(user, 0) + bytes_count

    return agg

def main():
    if not os.path.isdir(LIMITS_DIR):
        log(f"{LIMITS_DIR} missing.")
        return 0

    dump = run_iptables_save()
    if not dump:
        log("iptables-save produced no output.")
        return 0

    agg = current_bytes_per_user(dump)
    changed = 0
    now = int(time.time())
    for user, cur_bytes in agg.items():
        path = os.path.join(LIMITS_DIR, f"{user}.json")
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                j = json.load(f)
        except Exception:
            j = {}
        old = j.get("last_iptables_bytes", None)
        if not isinstance(old, int) or old != int(cur_bytes):
            j["last_iptables_bytes"] = int(cur_bytes)
            j["last_checked"] = now
            with open(path, "w", encoding="utf-8") as fw:
                json.dump(j, fw, ensure_ascii=False, indent=2)
            changed += 1
            log(f"{user}: last_iptables_bytes={cur_bytes} (was: {old})")
    log(f"Done. changed={changed}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())


EOF

#

chmod +x /usr/local/bin/log_user_traffic.py
