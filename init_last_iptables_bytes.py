sudo tee /usr/local/bin/init_last_iptables_bytes.py > /dev/null <<'PY'
#!/usr/bin/env python3
import json
import os
import re
import subprocess

LIMITS_DIR = "/etc/sshmanager/limits"
COMMENT_RE = re.compile(r'sshmanager:user=(\w+);uid=(\d+);mode=([a-zA-Z0-9_]+)(?:;mark=(0x[0-9A-F]+))?')

def read_counters():
    try:
        out = subprocess.check_output(["iptables-save", "-c"], text=True)
    except Exception:
        return []
    results = []
    for line in out.splitlines():
        if "sshmanager:" not in line:
            continue
        m = COMMENT_RE.search(line)
        if not m:
            continue
        user, uid, mode, mark = m.groups()
        m2 = re.match(r'^\[(\d+):(\d+)\]', line)
        if not m2:
            continue
        pkts, bytes_ = int(m2.group(1)), int(m2.group(2))
        results.append({
            "user": user,
            "uid": int(uid),
            "mode": mode,
            "bytes": bytes_
        })
    return results

def init_limits(counters):
    os.makedirs(LIMITS_DIR, exist_ok=True)
    for c in counters:
        user = c["user"]
        uid = c["uid"]
        fname = os.path.join(LIMITS_DIR, f"{user}.json")
        if os.path.exists(fname):
            try:
                with open(fname, "r") as f:
                    js = json.load(f)
            except Exception:
                js = {}
        else:
            js = {"username": user, "uid": uid}

        if c["mode"] == "owner":
            js["traffic_up_bytes"] = c["bytes"]
        elif c["mode"] == "connmark":
            js["traffic_down_bytes"] = c["bytes"]

        js["used"] = (js.get("traffic_up_bytes", 0) + js.get("traffic_down_bytes", 0)) // 1024

        with open(fname, "w") as f:
            json.dump(js, f, indent=2)

def main():
    counters = read_counters()
    init_limits(counters)

if __name__ == "__main__":
    main()

PY
#############

sudo chmod +x /usr/local/bin/init_last_iptables_bytes.py
sudo /usr/bin/python3 /usr/local/bin/init_last_iptables_bytes.py  
اجرا 
