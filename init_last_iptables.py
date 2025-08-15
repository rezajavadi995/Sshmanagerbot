sudo tee /usr/local/bin/init_last_iptables.py > /dev/null <<'PY'
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Initialize last_iptables_bytes using SSH_UIDS (connmark counters)
Safe to run once after migration so that log_user_traffic deltas درست محاسبه شود
"""
import subprocess, json, os, re, shutil, argparse, pwd
from datetime import datetime

LIMITS_DIR = "/etc/sshmanager/limits"
CHAIN_UIDS = "SSH_UIDS"
BACKUP_DIR_BASE = "/root/backups/limits-init"
LOG_FILE = "/var/log/sshmanager-traffic.log"

def log(s):
    ts = datetime.now().isoformat(timespec="seconds")
    msg = f"{ts} INIT: {s}"
    print(msg)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(msg + "\n")
    except Exception:
        pass

def pick_save_cmd():
    for cmd in (["iptables-save","-c"], ["iptables-legacy-save","-c"], ["iptables-nft-save","-c"]):
        try:
            out = subprocess.check_output(cmd, text=True, errors="ignore")
            if f"\n:{CHAIN_UIDS} " in out or f" -A {CHAIN_UIDS} " in out:
                return cmd[0]
        except Exception:
            pass
    return "iptables-save"

SAVE_CMD = pick_save_cmd()

RE_DEC = re.compile(r"\[(\d+):(\d+)\].*?\b-A\s+%s\b.*?-m\s+connmark\s+--mark\s+(\d+)" % re.escape(CHAIN_UIDS))
RE_HEX = re.compile(r"\[(\d+):(\d+)\].*?\b-A\s+%s\b.*?ctmark\s+match\s+0x([0-9A-Fa-f]+)(?:/0x[0-9A-Fa-f]+)?" % re.escape(CHAIN_UIDS))

def parse_iptables():
    try:
        out = subprocess.check_output([SAVE_CMD,"-c"], text=True, errors="ignore")
    except Exception as e:
        log(f"{SAVE_CMD} failed: {e}")
        return {}
    res = {}
    for ln in out.splitlines():
        if f" -A {CHAIN_UIDS} " not in ln:
            continue
        m = RE_DEC.search(ln) or RE_HEX.search(ln)
        if not m: 
            continue
        pkts, by, mark = m.groups()
        uid = int(mark, 16) if m.re is RE_HEX else int(mark)
        res[str(uid)] = int(by)
    return res

def uid_for_username(username):
    try: return pwd.getpwnam(username).pw_uid
    except Exception: return None

def backup_limits():
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = f"{BACKUP_DIR_BASE}-{ts}"
    try:
        if os.path.isdir(LIMITS_DIR):
            shutil.copytree(LIMITS_DIR, dest)
            log(f"Backup: {dest}")
            return dest
    except Exception as e:
        log(f"Backup failed: {e}")
    return None

def main(force=False, backup=True):
    if not os.path.isdir(LIMITS_DIR):
        log(f"{LIMITS_DIR} not found")
        return 2
    if backup:
        backup_limits()

    counters = parse_iptables()
    changed = errors = 0
    for fn in sorted(os.listdir(LIMITS_DIR)):
        if not fn.endswith(".json"): 
            continue
        path = os.path.join(LIMITS_DIR, fn)
        username = fn[:-5]
        try:
            data = json.load(open(path)) or {}
        except Exception as e:
            log(f"{path}: read json failed: {e}")
            errors += 1
            continue

        if isinstance(data.get("last_iptables_bytes"), int) and data["last_iptables_bytes"] >= 0:
            continue  # already initialized

        uid = uid_for_username(username)
        if uid is None:
            log(f"{username}: no uid, skip")
            continue

        cur = counters.get(str(uid))
        if cur is None and not force:
            log(f"{username}: no counter found, skip (use --force to set 0)")
            continue

        val = int(cur or 0)
        data["last_iptables_bytes"] = val
        data["last_checked"] = int(datetime.now().timestamp())

        tmp = path + ".tmp"
        try:
            json.dump(data, open(tmp,"w"), indent=4, ensure_ascii=False)
            os.replace(tmp, path)
            changed += 1
            log(f"{username}: last_iptables_bytes={val}")
        except Exception as e:
            errors += 1
            log(f"{username}: write failed: {e}")

    log(f"INIT finished: changed={changed} errors={errors}")
    return 0 if errors == 0 else 2

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-backup", dest="backup", action="store_false")
    ap.add_argument("--force", action="store_true", help="set 0 if no counter exists")
    args = ap.parse_args()
    raise SystemExit(main(force=args.force, backup=args.backup))

PY

# sudo chmod +x /usr/local/bin/init_last_iptables.py
# sudo /usr/bin/python3 /usr/local/bin/init_last_iptables.py  اجرا 
