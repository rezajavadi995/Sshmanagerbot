cat > /root/fix-iptables.sh << 'EOF'
#!/bin/bash
set -e

echo "[+] بررسی و ترمیم iptables برای کاربران SSH"

# ساخت chain در صورت نیاز
sudo iptables -N SSH_USERS 2>/dev/null || true

# اتصال chain به OUTPUT (idempotent)
sudo iptables -C OUTPUT -j SSH_USERS 2>/dev/null || sudo iptables -I OUTPUT -j SSH_USERS

# افزودن Rule برای هر کاربر واقعی (UID >= 1000)
while IFS=: read -r user _ uid _; do
  if [ "$uid" -ge 1000 ] && [ "$user" != "nobody" ]; then
    sudo iptables -C SSH_USERS -m owner --uid-owner "$uid" -j ACCEPT 2>/dev/null || \
    sudo iptables -A SSH_USERS -m owner --uid-owner "$uid" -j ACCEPT
  fi
done < <(getent passwd)

echo "[✓] تکمیل شد."

EOF


###########

وقتی فایل fix iptable رو اجرا کردی
=

وقتی این دو تا فایل رو ساختی و فعال کردی با دستور:

sudo systemctl daemon-reexec
sudo systemctl daemon-reload
sudo systemctl enable fix-iptables.service
sudo systemctl start fix-iptables.service

هر بار سرور ریبوت بشه، همه چیز سر جاش هست. 🔄
