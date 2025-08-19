
cat > /usr/local/bin/log_user_traffic.py << 'EOF'

#!/usr/bin/env python3
import json
import subprocess
import os
import re

LIMITS_DIR = "/etc/sshmanager/limits"

# regex برای گرفتن کامنت‌ها
COMMENT_RE = re.compile(r'sshmanager:user=(\w+);uid=(\d+);mode=([a-zA-Z0-9_]+)(?:;mark=(0x[0-9A-F]+))?')

def read_iptables_counters():
    """خروجی iptables-save -c رو می‌خونه و counters رو برمی‌گردونه."""
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
        # [pkts:bytes] همیشه ابتدای خط هست
        m2 = re.match(r'^\[(\d+):(\d+)\]', line)
        if not m2:
            continue
        pkts, bytes_ = int(m2.group(1)), int(m2.group(2))
        results.append({
            "user": user,
            "uid": int(uid),
            "mode": mode,
            "mark": mark,
            "pkts": pkts,
            "bytes": bytes_
        })
    return results

def update_user_limits(counters):
    os.makedirs(LIMITS_DIR, exist_ok=True)
    user_data = {}

    # جمع‌بندی per-UID
    for c in counters:
        uid = c["uid"]
        user = c["user"]
        bytes_ = c["bytes"]
        if uid not in user_data:
            user_data[uid] = {"user": user, "up": 0, "down": 0}
        # ساده‌سازی: هر دو حالت owner و connmark رو با هم جمع می‌کنیم
        if c["mode"] == "owner":
            user_data[uid]["up"] += bytes_
        elif c["mode"] == "connmark":
            user_data[uid]["down"] += bytes_

    # نوشتن در فایل JSON
    for uid, data in user_data.items():
        fname = os.path.join(LIMITS_DIR, f"{data['user']}.json")
        if os.path.exists(fname):
            try:
                with open(fname, "r") as f:
                    js = json.load(f)
            except Exception:
                js = {}
        else:
            js = {"username": data["user"], "uid": uid}

        used_kb = (data["up"] + data["down"]) // 1024

        js["traffic_up_bytes"] = data["up"]
        js["traffic_down_bytes"] = data["down"]
        js["used"] = used_kb

        with open(fname, "w") as f:
            json.dump(js, f, indent=2)

def main():
    counters = read_iptables_counters()
    update_user_limits(counters)

if __name__ == "__main__":
    main()


EOF

#chmod +x /usr/local/bin/log_user_traffic.py
