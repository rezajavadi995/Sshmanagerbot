sudo tee /usr/local/bin/init_last_iptables_bytes.py > /dev/null <<'PY'
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, json, pwd, subprocess, time

LIMITS_DIR = "/etc/sshmanager/limits"
CHAINS_TO_LOG = ["SSH_USERS_OUT", "SSH_USERS_FWD"]

LOG_DIR = "/var/log/sshmanager"
DEBUG_LOG = os.path.join(LOG_DIR, "init-last-iptables.log")

CHAIN_RES = {name: re.compile(rf"^-A\s+{re.escape(name)}\b") for name in CHAINS_TO_LOG}
COUNTERS  = re.compile(r"(?:-c\s+(\d+)\s+(\d+)|\[(\d+):(\d+)\])")
UID_OWNER = re.compile(r"--uid-owner\s+(\d+)\b")
COMMENT   = re.compile(r'-m\s+comment\s+--comment\s+(?:"([^"]+)"|\'([^\']+)\'|([^\s]+))')

def log(msg):
    try: os.makedirs(LOG_DIR, exist_ok=True)
    except Exception: pass
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} INIT: {msg}"
    print(line)
    try:
        with open(DEBUG_LOG, "a", encoding="utf-8") as f: f.write(line + "\n")
    except Exception: pass

def run(cmd):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)

def run_iptables_save():
    for cmd in (["/usr/sbin/iptables-save","-c"], ["iptables-save","-c"], ["/usr/sbin/iptables-legacy-save","-c"]):
        p = run(cmd)
        out = (p.stdout or "")
        if out.strip(): return out
    for cmd in (["/usr/sbin/iptables-save"], ["iptables-save"], ["/usr/sbin/iptables-legacy-save"]):
        p = run(cmd)
        out = (p.stdout or "")
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

def _parse_comment_user(text: str):
    parts = {}
    for seg in (text or "").split(";"):
        if "=" in seg:
            k, v = seg.split("=", 1)
            parts[k.strip()] = v.strip()
    return parts.get("sshmanager:user") or parts.get("user") or None

def current_bytes_per_user(text: str):
    agg = {}
    if not text: return agg
    for ln in text.splitlines():
        ok = False
        for _, cre in CHAIN_RES.items():
            if cre.search(ln):
                ok = True
                break
        if not ok: continue

        mctr = COUNTERS.search(ln)
        if not mctr: continue
        g = mctr.groups()
        bytes_count = safe_int(g[1] or g[3], 0)
        if bytes_count <= 0: continue

        user = None
        mcom = COMMENT.search(ln)
        if mcom:
            comment_text = mcom.group(1) or mcom.group(2) or mcom.group(3) or ""
            user = _parse_comment_user(comment_text)

        if not user:
            mo = UID_OWNER.search(ln)
            if mo:
                uid = safe_int(mo.group(1), -1)
                if uid >= 0:
                    user = uid_to_username(uid)

        if user:
            agg[user] = agg.get(user, 0) + bytes_count
    return agg

def main():
    if not os.path.isdir(LIMITS_DIR):
        log(f"limits dir not found: {LIMITS_DIR}")
        return 0

    out = run_iptables_save()
    if not out:
        log("iptables-save output is empty")
        return 0

    agg = current_bytes_per_user(out)
    now = int(time.time())
    changed = 0

    # روی همه‌ی userهای موجود در limits هم initialize کن
    all_users = [fn[:-5] for fn in os.listdir(LIMITS_DIR) if fn.endswith(".json")]

    for user in all_users:
        path = os.path.join(LIMITS_DIR, f"{user}.json")
        cur = safe_int(agg.get(user, 0), 0)
        try:
            with open(path, "r", encoding="utf-8") as f:
                j = json.load(f) or {}
        except Exception:
            j = {}

        old = j.get("last_iptables_bytes", None)
        if not isinstance(old, int) or old != int(cur):
            j["last_iptables_bytes"] = int(cur)
            j["last_checked"] = now
            with open(path, "w", encoding="utf-8") as fw:
                json.dump(j, fw, ensure_ascii=False, indent=2)
            changed += 1
            log(f"{user}: last_iptables_bytes={cur} (was: {old})")

    log(f"Done. checked={len(all_users)} changed={changed}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
PY
#############

sudo chmod +x /usr/local/bin/init_last_iptables_bytes.py
#
#
sudo /usr/bin/python3 /usr/local/bin/init_last_iptables_bytes.py  
#

اجرا 
