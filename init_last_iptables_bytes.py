sudo tee /usr/local/bin/init_last_iptables_bytes.py > /dev/null <<'PY'
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, re, json, pwd, subprocess, time

LIMITS_DIR = "/etc/sshmanager/limits"
DEBUG_DIR = "/var/log/sshmanager"
DEBUG_LOG = os.path.join(DEBUG_DIR, "init-last-iptables-debug.log")
CHAIN = "SSH_USERS"

os.makedirs(DEBUG_DIR, exist_ok=True)

def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(DEBUG_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {msg}\n")

UID_ACCEPT_RE = re.compile(
    r"\[(\d+):(\d+)\]\s+-A\s+%s\b.*?-m\s+owner\s+--uid-owner\s+(\d+)\b.*?-j\s+ACCEPT\b"
    % re.escape(CHAIN)
)

def safe_int(x, d=0):
    try:
        return int(x)
    except Exception:
        try:
            return int(float(x))
        except Exception:
            return d

def main():
    log("=" * 20)
    log("شروع init_last_iptables_bytes")

    try:
        out = subprocess.check_output(
            ["iptables-save", "-c"], text=True, stderr=subprocess.DEVNULL
        )
    except subprocess.CalledProcessError:
        out = subprocess.check_output(["iptables-save"], text=True)

    by_uid = {}
    for ln in out.splitlines():
        if ("-A %s " % CHAIN) not in ln or "--uid-owner" not in ln:
            continue
        m = UID_ACCEPT_RE.search(ln)
        if not m:
            log(f"⛔ خط نامعتبر iptables: {ln}")
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
            log(f"⚠️ UID {uid} نامعتبر است (کاربری وجود ندارد)")
            continue

        f = os.path.join(LIMITS_DIR, f"{uname}.json")
        if not os.path.exists(f):
            log(f"⚠️ فایل محدودیت برای {uname} وجود ندارد ({f})")
            continue

        try:
            j = json.load(open(f, "r", encoding="utf-8"))
        except Exception as e:
            log(f"⛔ خطا در خواندن JSON {f}: {e}")
            j = {}

        # بکاپ
        backup_path = f + f".bak_{int(time.time())}"
        try:
            with open(backup_path, "w", encoding="utf-8") as fw:
                json.dump(j, fw, ensure_ascii=False, indent=2)
        except Exception as e:
            log(f"⚠️ خطا در ساخت بکاپ {backup_path}: {e}")

        # ثبت مقدار بایت‌ها
        j["last_iptables_bytes"] = cur_bytes
        try:
            with open(f, "w", encoding="utf-8") as fw:
                json.dump(j, fw, ensure_ascii=False, indent=2)
            log(f"✅ {uname}: مقدار اولیه last_iptables_bytes = {cur_bytes} ثبت شد")
        except Exception as e:
            log(f"⛔ خطا در نوشتن فایل {f}: {e}")

    log("پایان init_last_iptables_bytes")

if __name__ == "__main__":
    main()
PY

# sudo chmod +x /usr/local/bin/init_last_iptables_bytes.py
# sudo /usr/bin/python3 /usr/local/bin/init_last_iptables_bytes.py  اجرا 
