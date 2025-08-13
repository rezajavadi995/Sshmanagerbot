#بعد بهش با دستور زیر دسترسی اجرا بده: 
#chmod +x /usr/local/bin/log_user_traffic.py

#cat > /usr/local/bin/log_user_traffic.py << 'EOF'
#!/usr/bin/env python3
# /root/sshmanager/log_user_traffic.py
import os, json, subprocess
from datetime import datetime

LIMITS_DIR = "/etc/sshmanager/limits"
LOCK_SCRIPT = "/root/sshmanager/lock_user.py"

os.makedirs(LIMITS_DIR, exist_ok=True)

def atomic_write(path, data):
    tmp = f"{path}.tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=4)
    os.replace(tmp, path)

def run_cmd(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()

def lock_user(username, reason="quota"):
    run_cmd(["python3", LOCK_SCRIPT, username, reason])

def parse_iptables():
    out = subprocess.getoutput("iptables -L SSH_USERS -v -n -x 2>/dev/null")
    res = {}
    for line in out.splitlines():
        line = line.strip()
        if not line or line.startswith(("Chain", "pkts", "target")):
            continue
        parts = line.split()
        # bytes
        try:
            bytes_counter = int(parts[1])
        except Exception:
            nums = [int(tok) for tok in parts if tok.isdigit()]
            if not nums:
                continue
            bytes_counter = nums[0]
        # uid
        uid = None
        if "--uid-owner" in parts:
            uid = parts[parts.index("--uid-owner")+1]
        else:
            import re
            m = re.search(r"--uid-owner\s+(\d+)", line)
            if m: uid = m.group(1)
        if uid:
            res[uid] = bytes_counter
    return res

def uid_map():
    m = {}
    rc, out, _ = run_cmd(["getent", "passwd"])
    if rc == 0:
        for ln in out.splitlines():
            p = ln.split(":")
            if len(p) >= 3:
                m[p[2]] = p[0]
    return m

def main():
    ipt = parse_iptables()
    if not ipt:
        return
    umap = uid_map()
    now = int(datetime.now().timestamp())
    for uid, cur_bytes in ipt.items():
        user = umap.get(uid)
        if not user:
            continue
        lf = os.path.join(LIMITS_DIR, f"{user}.json")
        if not os.path.exists(lf):
            continue
        try:
            with open(lf, "r") as f:
                data = json.load(f)
        except Exception:
            data = {}

        used_kb  = int(data.get("used", 0))
        limit_kb = int(data.get("limit", 0))
        blocked  = bool(data.get("is_blocked", False))
        reason   = data.get("block_reason") or None
        last_b   = int(data.get("last_iptables_bytes", 0))

        if last_b == 0 and not data.get("last_checked"):
            data["last_iptables_bytes"] = int(cur_bytes)
            data["last_checked"] = now
            atomic_write(lf, data)
            continue

        delta = cur_bytes - last_b
        if delta < 0:
            delta = cur_bytes
        add_kb = int(delta / 1024)
        if add_kb > 0:
            used_kb += add_kb
            data["used"] = used_kb

        data["last_iptables_bytes"] = int(cur_bytes)
        data["last_checked"] = now

        # اعمال محدودیت
        if limit_kb > 0 and used_kb >= limit_kb:
            if not blocked or reason != "quota":
                lock_user(user, "quota")
                data["is_blocked"] = True
                data["block_reason"] = "quota"
                data["alert_sent"] = True

        atomic_write(lf, data)

if __name__ == "__main__":
    main()



#EOF
