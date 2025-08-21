cat > /usr/local/bin/check_users_expire.py << 'EOF'
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, json, subprocess, sys
from datetime import datetime

LIMITS_DIR = "/etc/sshmanager/limits"
# --- FIX: Point to the master lock script for all locking operations ---
LOCK_SCRIPT = "/root/sshmanager/lock_user.py"

def log(msg):
    # Use print for systemd logs
    print(f"EXPIRE_CHECK: {msg}")
    sys.stdout.flush()

def to_int(v, default=None):
    try:
        return int(v)
    except (ValueError, TypeError):
        try:
            return int(float(v))
        except (ValueError, TypeError):
            return default

def human_ts(ts):
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError, OSError):
        return str(ts)

def process_user_file(path):
    try:
        with open(path, "r") as f:
            j = json.load(f)
    except Exception as e:
        return False, False, f"JSON read error for {path}: {e}"

    username = os.path.basename(path)[:-5]
    
    # If user is already blocked, do nothing
    if j.get("is_blocked"):
        return False, True, f"{username}: Already blocked, skipping."

    expire_ts = to_int(j.get("expire_timestamp"), default=None)
    now = int(datetime.now().timestamp())

    if not expire_ts or expire_ts <= 0:
        return False, True, f"{username}: No valid expire_timestamp; skipping."
    
    if now < expire_ts:
        return False, True, f"{username}: Not yet expired (expires at {human_ts(expire_ts)})."

    # --- FIX: Delegate locking to the master script ---
    log(f"{username}: Expired. Attempting to lock...")
    try:
        p = subprocess.run(["/usr/bin/python3", LOCK_SCRIPT, username, "expire"], capture_output=True, text=True, timeout=60)
        if p.returncode == 0:
            log(f"{username}: Successfully locked by lock_user.py.")
            return True, True, f"{username}: Locked due to expiration (expire @ {human_ts(expire_ts)})."
        else:
            log(f"{username}: ERROR: lock_user.py failed. RC={p.returncode}. Stderr: {p.stderr}")
            return True, False, f"{username}: Lock failed. See logs."
    except Exception as e:
        log(f"{username}: ERROR: Exception while calling lock_user.py: {e}")
        return True, False, f"{username}: Lock failed due to an exception."

def main():
    if not os.path.isdir(LIMITS_DIR):
        log(f"Limits directory {LIMITS_DIR} not found; exiting.")
        sys.exit(0)

    files = [os.path.join(LIMITS_DIR, f) for f in os.listdir(LIMITS_DIR) if f.endswith(".json")]
    if not files:
        log("No user files to check.")
        sys.exit(0)

    any_error = False
    log(f"Starting expiration check for {len(files)} users in {LIMITS_DIR}")
    for path in sorted(files):
        acted, ok, msg = process_user_file(path)
        if not ok: 
            any_error = True
        if acted or not ok:
            log(msg)

    if any_error:
        log("Finished with one or more errors.")
        sys.exit(2)
    else:
        log("Finished successfully.")
        sys.exit(0)

if __name__ == "__main__":
    main()

EOF
#

chmod +x /usr/local/bin/check_users_expire.py

