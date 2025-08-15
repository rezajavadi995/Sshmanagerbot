sudo tee /usr/local/bin/init_last_iptables.py > /dev/null <<'PY'
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, re, json, pwd, subprocess

LIMITS_DIR = "/etc/sshmanager/limits"
CHAIN = "SSH_USERS"
UID_ACCEPT_RE = re.compile(
    r"\[(\d+):(\d+)\]\s+-A\s+%s\b.*?-m\s+owner\s+--uid-owner\s+(\d+)\b.*?-j\s+ACCEPT\b" % re.escape(CHAIN)
)

def safe_int(x, d=0):
    try: return int(x)
    except: 
        try: return int(float(x))
        except: return d

def main():
    try:
        out = subprocess.check_output(["iptables-save","-c"], text=True, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        out = subprocess.check_output(["iptables-save"], text=True)

    by_uid = {}
    for ln in out.splitlines():
        if ("-A %s " % CHAIN) not in ln or "--uid-owner" not in ln:
            continue
        m = UID_ACCEPT_RE.search(ln)
        if not m: 
            continue
        _, bytes_str, uid_str = m.groups()
        uid = safe_int(uid_str, None)
        if uid is None or uid < 1000:
            continue
        by_uid[uid] = safe_int(bytes_str, 0)

    for uid, cur_bytes in by_uid.items():
        try:
            uname = pwd.getpwuid(uid).pw_name
        except KeyError:
            continue
        f = os.path.join(LIMITS_DIR, f"{uname}.json")
        if not os.path.exists(f):
            continue
        try:
            j = json.load(open(f, "r", encoding="utf-8"))
        except Exception:
            j = {}
        j["last_iptables_bytes"] = cur_bytes
        with open(f, "w", encoding="utf-8") as fw:
            json.dump(j, fw, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    main()


PY

# sudo chmod +x /usr/local/bin/init_last_iptables.py
# sudo /usr/bin/python3 /usr/local/bin/init_last_iptables.py  اجرا 
