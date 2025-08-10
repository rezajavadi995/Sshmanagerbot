#اول دستور زیرو بزن تا پوشه ساخته بشه



#cat > /root/sshmanager/lock_user.py << 'EOF'
#!/usr/bin/env python3
#!/usr/bin/env python3
import subprocess
import sys
import requests
import json
import os
from datetime import datetime

BOT_TOKEN = "8152962391:AAG4kYisE21KI8dAbzFy9oq-rn9h9RCQyBM"
ADMIN_ID = 8062924341

def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": ADMIN_ID, "text": text}
    try:
        requests.post(url, data=data, timeout=5)
    except:
        pass

def lock_user(username, reason="quota"):
    """
    Lock a Linux user for SSH access/tunneling.
    reason: "quota", "expire", or "manual" (defaults to "quota")
    """
    try:
        # غیرفعال کردن شل (nologin) و قفل کردن پسورد
        subprocess.run(["usermod", "-s", "/usr/sbin/nologin", username], check=True)
        subprocess.run(["passwd", "-l", username], check=True)
        
        # قطع کردن اتصالات فعال SSH کاربر
        subprocess.run(["pkill", "-u", username], check=False)
        
        # OLD (مشکل‌ساز) - تغییر تاریخ انقضا؛ به دلیل مشکلات سازگاری کامنت شده
        # subprocess.run(["usermod", "--expiredate", "1", username], check=True)

        # به‌روزرسانی فایل JSON کاربر برای جلوگیری از پیام‌های اشتباه
        limit_file_path = f"/etc/sshmanager/limits/{username}.json"
        if os.path.exists(limit_file_path):
            try:
                with open(limit_file_path, "r") as f:
                    user_data = json.load(f)
            except Exception:
                user_data = {}

            # نگهداری فیلدهای قبلی (بدون حذف) و تنظیم مقادیر لازم
            # (کلیدهای فعلی پروژه‌ات: used_bytes, total_bytes, limited, is_blocked ...)
            user_data["used_bytes"] = 0
            user_data["total_bytes"] = 0
            user_data["limited"] = False
            user_data["is_blocked"] = True

            # اگر دلیل قفل از قبل 'manual' باشد، آن را حفظ کن (ادمین دستی)
            prev_reason = user_data.get("block_reason")
            if prev_reason == "manual":
                # حفظ دلیل دستی، حتی اگر این فراخوانی از اسکریپت خودکار باشد
                pass
            else:
                # مقداردهی دلیل قفل بر اساس آرگومان ورودی (یا fallback به quota)
                user_data["block_reason"] = reason if reason else "quota"

            # (اختیاری) ذخیره زمان بلاک
            user_data["blocked_at"] = int(datetime.now().timestamp())

            try:
                with open(limit_file_path, "w") as f:
                    json.dump(user_data, f, indent=4)
            except Exception:
                # در صورت خطا، تلاش می‌کنیم لاگ نکنیم اما بهتر است بررسی شود
                pass

        # حذف rule از iptables (اگر وجود داشته باشد)
        uid = subprocess.getoutput(f"id -u {username}").strip()
        if uid and uid.isdigit():
            subprocess.run(
                ["iptables", "-D", "SSH_USERS", "-m", "owner", "--uid-owner", uid, "-j", "ACCEPT"],
                stderr=subprocess.DEVNULL,
            )

        # پیام به تلگرام با توضیح دلیل (فارسی)
        reason_map = {
            "quota": "اتمام حجم",
            "expire": "اتمام تاریخ انقضا",
            "manual": "قفل دستی"
        }
        reason_text = reason_map.get(reason, reason)
        send_telegram_message(f"🔒 اکانت کاربر `{username}` به دلیل {reason_text} مسدود شد.")
    except Exception as e:
        send_telegram_message(f"⚠️ خطا در مسدودسازی کاربر {username}: {e}")

if __name__ == "__main__":
    # اجازه دو حالت: python3 lock_user.py <username> [<reason>]
    if len(sys.argv) < 2:
        print("Usage: python3 lock_user.py <username> [reason]")
        sys.exit(1)
    username = sys.argv[1]
    reason = sys.argv[2] if len(sys.argv) > 2 else "quota"
    lock_user(username, reason)

#EOF

##############

#قابل اجراش کن: 

#chmod +x /root/sshmanager/lock_user.py

