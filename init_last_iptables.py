sudo tee /usr/local/bin/init_last_iptables.py > /dev/null <<'PY'
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
init_last_iptables.py
- Initialize missing/invalid last_iptables_bytes fields in /etc/sshmanager/limits
- By default: only sets when iptables has a counter for the user's UID
- Optional: --force sets 0 when no counter found
"""
import subprocess, json, os, re, shutil, argparse
from datetime import datetime

LIMITS_DIR = "/etc/sshmanager/limits"
CHAIN_NAME = "SSH_USERS"
BACKUP_DIR_BASE = "/root/backups/limits-init"
LOG_FILE = "/var/log/sshmanager-traffic.log"

def safe_int(v, d=0):
    try:
        return int(v)
    except Exception:
        try:
            return int(float(v))
        except Exception:
            return d

def log(s):
    ts = datetime.now().isoformat()
    msg = f"{ts} INIT: {s}"
    print(msg)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(msg + "\\n")
    except Exception:
        pass

def parse_iptables_save():
    """
    Return dict uid_str -> bytes (raw) from iptables-save -c
    """
    try:
        p = subprocess.run(["iptables-save","-c"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        out = p.stdout or ""
    except Exception as e:
        log(f"iptables-save failed: {e}")
        return {}

    res = {}
    for ln in out.splitlines():
        if f"-A {CHAIN_NAME}" not in ln or "--uid-owner" not in ln:
            continue
        # parse [pkts:bytes]
        lb = ln.find('['); rb = ln.find(']')
        bytes_count = 0
        if lb != -1 and rb != -1 and rb > lb:
            counters = ln[lb+1:rb]
            parts = counters.split(":")
            if len(parts) == 2:
                bytes_count = safe_int(parts[1], 0)
        m = re.search(r"--uid-owner\\s+(\\d+)", ln)
        if m:
            uid = m.group(1)
            res[str(uid)] = int(bytes_count)
    return res

def uid_for_username(username):
    try:
        p = subprocess.run(["getent","passwd",username], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        out = p.stdout.strip()
        if out:
            return out.split(":")[2]
    except Exception:
        pass
    return None

def backup_limits():
    ts = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dest = f"{BACKUP_DIR_BASE}-{ts}"
    try:
        if os.path.exists(LIMITS_DIR):
            shutil.copytree(LIMITS_DIR, dest)
            log(f"Backup created: {dest}")
            return dest
    except Exception as e:
        log(f"Backup failed: {e}")
    return None

def main(force=False, backup=True):
    if not os.path.isdir(LIMITS_DIR):
        log(f"{LIMITS_DIR} not found; exiting.")
        return 2

    if backup:
        backup_limits()

    counters = parse_iptables_save()  # uid -> bytes
    changed = []
    skipped = []
    errors = []

    for fn in sorted(os.listdir(LIMITS_DIR)):
        if not fn.endswith(".json"):
            continue
        path = os.path.join(LIMITS_DIR, fn)
        username = fn[:-5]
        try:
            with open(path, "r") as f:
                data = json.load(f) or {}
        except Exception as e:
            errors.append((path, f"read json failed: {e}"))
            continue

        last_bytes = data.get("last_iptables_bytes", None)
        # consider invalid if not an int or negative
        valid = isinstance(last_bytes, int) and last_bytes >= 0

        if valid:
            skipped.append((username, "already set"))
            continue

        # find uid
        uid = uid_for_username(username)
        if uid and uid in counters:
            cur_bytes = int(counters[uid])
            data["last_iptables_bytes"] = cur_bytes
            data["last_checked"] = int(datetime.now().timestamp())
            try:
                tmp = path + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(data, f, indent=4, ensure_ascii=False)
                os.replace(tmp, path)
                changed.append((username, cur_bytes))
                log(f"{username}: set last_iptables_bytes={cur_bytes} (from iptables)")
            except Exception as e:
                errors.append((path, f"write failed: {e}"))
        else:
            if force:
                # set to 0 forcibly
                data["last_iptables_bytes"] = 0
                data["last_checked"] = int(datetime.now().timestamp())
                try:
                    tmp = path + ".tmp"
                    with open(tmp, "w") as f:
                        json.dump(data, f, indent=4, ensure_ascii=False)
                    os.replace(tmp, path)
                    changed.append((username, 0))
                    log(f"{username}: force-set last_iptables_bytes=0 (no iptables record)")
                except Exception as e:
                    errors.append((path, f"write failed: {e}"))
            else:
                skipped.append((username, "no iptables record"))

    # summary
    log(f"INIT finished: changed={len(changed)} skipped={len(skipped)} errors={len(errors)}")
    if changed:
        log("Changed entries:")
        for u,v in changed:
            log(f"  - {u}: last_iptables_bytes={v}")
    if skipped:
        log("Skipped entries:")
        for u,reason in skipped[:50]:
            log(f"  - {u}: {reason}")
    if errors:
        log("Errors:")
        for p,e in errors:
            log(f"  - {p}: {e}")
    return 0

if __name__ == '__main__':
    ap = argparse.ArgumentParser(description="Initialize last_iptables_bytes for sshmanager limits")
    ap.add_argument("--no-backup", dest="backup", action="store_false", help="Do not backup limits dir before changes")
    ap.add_argument("--force", action="store_true", help="Force set last_iptables_bytes=0 when iptables has no record")
    args = ap.parse_args()
    rc = main(force=args.force, backup=args.backup)
    raise SystemExit(rc)
PY

# sudo chmod +x /usr/local/bin/init_last_iptables.py
# sudo /usr/bin/python3 /usr/local/bin/init_last_iptables.py  اجرا 
