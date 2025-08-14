# /root/fix-iptables.sh
cat > /root/fix-iptables.sh << 'EOF'
#!/bin/bash
set -euo pipefail

echo "[+] بررسی و ترمیم iptables برای کاربران SSH"

# ساخت chain در صورت نیاز
iptables -N SSH_USERS 2>/dev/null || true

# اتصال chain به OUTPUT (idempotent)
iptables -C OUTPUT -j SSH_USERS 2>/dev/null || iptables -I OUTPUT -j SSH_USERS

# افزودن Rule برای هر کاربر واقعی (UID >= 1000)
getent passwd | while IFS=: read -r user _ uid _; do
  if [ "$uid" -ge 1000 ] && [ "$user" != "nobody" ]; then
    iptables -C SSH_USERS -m owner --uid-owner "$uid" -j ACCEPT 2>/dev/null || \
    iptables -A SSH_USERS -m owner --uid-owner "$uid" -j ACCEPT
  fi
done

echo "[✓] تکمیل شد."
EOF

chmod +x /root/fix-iptables.sh
systemctl enable --now fix-iptables.service
