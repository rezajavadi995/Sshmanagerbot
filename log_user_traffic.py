
#cat > /usr/local/bin/log_user_traffic.py << 'EOF'

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, re, json, time, fcntl, pwd, subprocess
from datetime import datetime

LIMITS_DIR = "/etc/sshmanager/limits"
LOCK_FILE  = "/var/lock/log_user_traffic.lock"
IPT       = "iptables-save"  # برای سرعتِ خواندن کانترها
CHAIN     = "SSH_USERS"
CHECK_INTERVAL = 30  # ثانیه؛ اگر به صورت سرویس/تایمر صدا می‌زنید، می‌تواند نادیده گرفته شود.

# فقط ACCEPT را برای شمارش در نظر بگیر تا در حالت REJECT مصرف جلو نرود
UID_ACCEPT_RE = re.compile(
    r"\[(\d+):(\d+)\]\s+-A\s+%s\b.*?-m\s+owner\s+--uid-owner\s+(\d+)\b.*?-j\s+ACCEPT\b" % re.escape(CHAIN)
)

def log(msg):
    print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)

def safe_int(x, default=0):
    try:
        return int(x)
    except Exception:
        try:
            return int(float(x))
        except Exception:
            return default

def ipt_save_lines():
    try:
        out = subprocess.check_output(["iptables-save","-c"], text=True, stderr=subprocess.DEVNULL)
    except subprocess.CalledProcessError:
        out = subprocess.check_output(["iptables-save"], text=True)
    return [ln for ln in out.splitlines() if ("-A %s " % CHAIN) in ln and "--uid-owner" in ln]

def write_json_atomic(path, obj):
    data = json.dumps(obj, ensure_ascii=False, indent=2)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, path)

def main():
    start_ts = time.time()

    # قفل بین‌پردازه‌ای
    os.makedirs(os.path.dirname(LOCK_FILE), exist_ok=True)
    with open(LOCK_FILE, "w") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)

        log("="*20)
        log("اجرای log_user_traffic آغاز شد")

        # خواندن خطوط iptables-save
        lines = ipt_save_lines()

        # map: uid -> bytes روی rule ACCEPT
        bytes_map = {}
        for ln in lines:
            m = UID_ACCEPT_RE.search(ln)
            if not m:
                continue
            # groups: pkts, bytes, uid
            _, bytes_str, uid_str = m.groups()
            uid = safe_int(uid_str, None)
            if uid is None or uid < 1000:
                continue
            bytes_map[uid] = safe_int(bytes_str, 0)

        # بروزرسانی فایل هر کاربر
        for uid, cur_bytes in bytes_map.items():
            try:
                uname = pwd.getpwuid(uid).pw_name
            except KeyError:
                continue
            limits_file = os.path.join(LIMITS_DIR, f"{uname}.json")
            if not os.path.exists(limits_file):
                continue
            try:
                j = json.load(open(limits_file, "r", encoding="utf-8"))
            except Exception:
                j = {}

            j.setdefault("username", uname)
            j.setdefault("type", "limited")
            j.setdefault("limit", 0)
            j.setdefault("used", 0)
            j.setdefault("is_blocked", False)
            j.setdefault("block_reason", None)

            # مقدار قبلی کانتر iptables روی rule ACCEPT
            last_bytes = safe_int(j.get("last_iptables_bytes", 0), 0)
            used_kb    = safe_int(j.get("used", 0), 0)

            if cur_bytes >= last_bytes:
                delta = cur_bytes - last_bytes
            else:
                # اگر iptables ریست شده باشد
                delta = cur_bytes

            # فقط وقتی افزایش بده که بلوک نیست
            if not j.get("is_blocked", False):
                used_kb += delta // 1024

            j["last_iptables_bytes"] = cur_bytes
            j["used"] = used_kb

            # اگر از limit عبور کرد، علامت‌گذاری برای بلاک (لاک واقعی دستِ بات/سرویس بلاک‌کننده است)
            limit_kb = safe_int(j.get("limit", 0), 0)
            if limit_kb > 0 and used_kb >= limit_kb:
                j["over_limit"] = True
            else:
                j["over_limit"] = False

            write_json_atomic(limits_file, j)

        log(f"تمام شد در {int(time.time()-start_ts)}s؛ {len(bytes_map)} کاربر به‌روزرسانی شد.")

if __name__ == "__main__":
    main()

#EOF

#chmod +x /usr/local/bin/log_user_traffic.py
