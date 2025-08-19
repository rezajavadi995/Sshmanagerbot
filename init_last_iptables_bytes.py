sudo tee /usr/local/bin/init_last_iptables_bytes.py > /dev/null <<'PY'
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Initialize/repair last_iptables_bytes in /etc/sshmanager/limits/*.json
- Robust to legacy/nft mismatch: tries iptables-save, falls back to iptables-legacy-save.
- Reads [pkts:bytes] counters from SSH_USERS rules with --uid-owner.
- Does NOT delete/override other fields; only reconciles last_iptables_bytes (and last_checked).
"""
import os, re, json, pwd, subprocess, time

LIMITS_DIR = "/etc/sshmanager/limits"
CHAIN_NAME = "SSH_USERS"
DEBUG_DIR  = "/var/log/sshmanager"
DEBUG_LOG  = os.path.join(DEBUG_DIR, "init-last-iptables-debug.log")

UID_RE = re.compile(r"--uid-owner\s+(\d+)\b")
CTR_RE = re.compile(r"\[(\d+):(\d+)\]")
CHAIN_RE = re.compile(rf"-A\s+{re.escape(CHAIN_NAME)}\b")

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

def run_iptables_save():
    # 1) تلاش با iptables-save -c
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
    # آخرین تلاش بدون -c (کم‌فایده ولی بهتر از هیچ)
    for cmd in (["/usr/sbin/iptables-save"], ["iptables-save"], ["/usr/sbin/iptables-legacy-save"]):
        try:
            p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
            out = (p.stdout or "")
            if out.strip():
                return out
        except Exception as e:
            log(f"run {cmd!r} failed (noc): {e}")
    return ""

def safe_int(x, d=0):
    try:
        return int(x)
    except Exception:
        try:
            return int(float(x))
        except Exception:
            return d

def parse_uid_bytes(dump_text):
    """
    از خروجی iptables-save، برای هر uid (در CHAIN_NAME) مقدار bytes را برمی‌گرداند.
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
        m_ctr = CTR_RE.search(ln)
        bytes_count = 0
        if m_ctr:
            # m_ctr.groups() == (pkts, bytes)
            bytes_count = safe_int(m_ctr.group(2), 0)
        res[uid] = bytes_count
    return res

def uname(uid):
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return None

def main():
    if not os.path.isdir(LIMITS_DIR):
        log(f"{LIMITS_DIR} missing.")
        return 0

    dump = run_iptables_save()
    if not dump:
        log("iptables-save produced no output.")
        return 0

    by_uid = parse_uid_bytes(dump)
    changed = 0
    for uid, cur_bytes in by_uid.items():
        user = uname(uid)
        if not user:
            continue
        path = os.path.join(LIMITS_DIR, f"{user}.json")
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                j = json.load(f)
        except Exception:
            j = {}
        old = j.get("last_iptables_bytes", None)
        if not isinstance(old, int) or old < 0 or old != cur_bytes:
            j["last_iptables_bytes"] = int(cur_bytes)
            j["last_checked"] = int(time.time())
            with open(path, "w", encoding="utf-8") as fw:
                json.dump(j, fw, ensure_ascii=False, indent=2)
            changed += 1
            log(f"{user}: last_iptables_bytes={cur_bytes} (was: {old})")
    log(f"Done. changed={changed}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

PY
#############

sudo chmod +x /usr/local/bin/init_last_iptables_bytes.py
sudo /usr/bin/python3 /usr/local/bin/init_last_iptables_bytes.py  
اجرا 
