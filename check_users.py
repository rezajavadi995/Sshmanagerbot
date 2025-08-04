#این فایل فعلا کاربردی نداره

cat > /usr/local/bin/check_users.py << 'EOF'
#!/usr/bin/env python3
import os, subprocess, json

LIMITS_DIR = "/etc/sshmanager/limits"

def get_usage(username):
    try:
        output = subprocess.getoutput(f"vnstat -u {username} && vnstat -i {username} --json")
        data = json.loads(output)
        total_rx = data["interfaces"][0]["traffic"]["total"]["rx"]
        total_tx = data["interfaces"][0]["traffic"]["total"]["tx"]
        return (total_rx + total_tx) / 1024  # MB
    except:
        return -1

for file in os.listdir(LIMITS_DIR):
    if not file.endswith(".json"): continue
    username = file.replace(".json", "")
    try:
        with open(os.path.join(LIMITS_DIR, file)) as f:
            data = json.load(f)
        limit = data["limit"]  # in MB
        usage = get_usage(username)

        if usage == -1:
            print(f"[!] خطا در گرفتن مصرف {username}")
            continue

        if usage > limit:
            subprocess.run(["python3", "/root/sshmanager/lock_user.py", username])
        elif usage > limit * 0.97:
            # فقط لاگ می‌گیریم. در آینده می‌تونه به بات هم پیام بده.
            print(f"[⚠️] {username} نزدیک حجم مجاز: {int(usage)}/{limit} MB")
    except Exception as e:
        print(f"[!] خطا برای {username}: {e}")
EOF
 
###################


#دسترسی اجرا 


chmod +x /usr/local/bin/check_users.py


#اضافه کردن به کرون جاب 
(crontab -l 2>/dev/null; echo "0 */3 * * * /usr/local/bin/check_users.py") | crontab -
#######################################
#قبل از اجرای اسکریپت، مطمئن شو:

#vnstat نصب باشه (sudo apt install vnstat -y)

#هر کاربری که ساختی با vnstat -u username فعال شده باشه

#اسکریپت بالا رو بعداً با sudo /usr/local/bin/check_users.py تست کن



