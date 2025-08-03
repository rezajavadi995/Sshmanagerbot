cat > /root/fix-iptables.sh << 'EOF'
#!/bin/bash
echo "[+] در حال بررسی کاربران و افزودن به iptables..."

# ایجاد chain در صورت نبود
sudo iptables -N SSH_USERS 2>/dev/null || true

# اطمینان از اتصال chain به OUTPUT
sudo iptables -D OUTPUT -j SSH_USERS 2>/dev/null
sudo iptables -I OUTPUT -j SSH_USERS

# افزودن کاربران UID >= 1000
for user in $(getent passwd | awk -F: '$3 >= 1000 {print $1}'); do
  uid=$(id -u $user)
  sudo iptables -C SSH_USERS -m owner --uid-owner $uid -j ACCEPT 2>/dev/null || sudo iptables -A SSH_USERS -m owner --uid-owner $uid -j ACCEPT
done
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
