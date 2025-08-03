cat > /root/setup_full.sh << 'EOF'
#!/bin/bash

# اجرای فقط با کاربر root
if [[ $EUID -ne 0 ]]; then
  echo "❌ لطفاً با کاربر root اجرا شود."
  exit 1
fi

# تنظیم ساعت و تایم‌زون ایران
timedatectl set-timezone Asia/Tehran
echo "⏰ تایم‌زون روی Asia/Tehran تنظیم شد."

# نصب ابزارها
echo "✅ شروع نصب ابزارها و پیش‌نیازها..."
apt update
apt install -y iptables-persistent certbot python3-pip curl unzip software-properties-common

# نصب stunnel فقط در صورت نیاز
if ! command -v stunnel4 &> /dev/null; then
  apt install -y stunnel4
  systemctl enable stunnel4
  echo "✅ stunnel نصب و فعال شد."
else
  echo "ℹ stunnel قبلاً نصب شده بود."
fi

# تنظیم iptables
echo "🧱 پیکربندی iptables..."

iptables -S | grep -q "SSH_USERS" || iptables -N SSH_USERS
iptables -C OUTPUT -m owner --uid-owner 0 -j ACCEPT 2>/dev/null || iptables -I OUTPUT -m owner --uid-owner 0 -j ACCEPT
iptables -C OUTPUT -j SSH_USERS 2>/dev/null || iptables -I OUTPUT -j SSH_USERS

netfilter-persistent save
echo "✅ iptables تنظیم و ذخیره شد."

# نمایش IP عمومی سرور
IP=$(curl -s https://ipinfo.io/ip)
echo "🌐 IP سرور شما: $IP"

# ساخت مسیر ذخیره توکن Cloudflare
mkdir -p /root/.secrets
chmod 700 /root/.secrets

# ذخیره امن توکن
cat > /root/.secrets/cloudflare.ini << CLOUDFLARE_TOKEN
dns_cloudflare_api_token = mInG2zDuSmwqOP0PEWAAO0qQKBMQLJQv4yQg5ZJc
CLOUDFLARE_TOKEN

chmod 600 /root/.secrets/cloudflare.ini
echo "🔐 Cloudflare Token ذخیره شد."

# گرفتن گواهی TLS از Cloudflare DNS
DOMAIN="ssh.ultraspeed.shop"
CERT_PATH="/etc/letsencrypt/live/$DOMAIN/fullchain.pem"

if [[ -f "$CERT_PATH" ]]; then
  echo "📄 گواهی قبلاً دریافت شده است، مرحله رد شد."
else
  echo "📄 درخواست گواهی TLS از طریق Certbot..."

  certbot certonly \
    --dns-cloudflare \
    --dns-cloudflare-credentials /root/.secrets/cloudflare.ini \
    --dns-cloudflare-propagation-seconds 30 \
    -d $DOMAIN \
    --preferred-challenges dns \
    --agree-tos \
    --no-eff-email \
    -m Rezajavadi995@gmail.com

  if [[ ! -f "$CERT_PATH" ]]; then
    echo "❌ خطا در دریافت گواهی TLS. بررسی کنید که دامنه در Cloudflare ثبت شده باشد و رکورد ssh فعال باشد."
    exit 1
  fi
fi

echo "✅ گواهی TLS دریافت یا تأیید شد."

# پیکربندی stunnel
echo "⚙ تنظیم stunnel برای استفاده از TLS روی پورت 443..."

cat > /etc/stunnel/stunnel.conf << STUNNELCONF
cert = /etc/letsencrypt/live/$DOMAIN/fullchain.pem
key = /etc/letsencrypt/live/$DOMAIN/privkey.pem

[ssh]
accept = 443
connect = 127.0.0.1:22
STUNNELCONF

chmod 600 /etc/stunnel/stunnel.conf
systemctl restart stunnel4
echo "✅ stunnel روی پورت 443 راه‌اندازی شد."

# راهنمای نهایی
echo ""
echo "🎯 آماده‌سازی سرور کامل شد."
echo "📌 مراحل بعدی:"
echo "➤ فایل ربات را به مسیر /root/sshmanager.py منتقل کنید."
echo "➤ سپس اجرا کنید:"
echo "    python3 /root/sshmanager.py & disown"
EOF

# اجرای نهایی و مجوز
chmod +x /root/setup_full.sh
echo "✅ اسکریپت ساخته و آماده اجراست: sudo /root/setup_full.sh"


chmod +x /root/setup_full.sh


sudo /root/setup_full.sh

نصب و اجرا
