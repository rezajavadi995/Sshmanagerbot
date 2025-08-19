cat > /root/fix-iptables.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

echo "[+] Fixing iptables for SSH per-UID accounting..."

CHAIN="SSH_USERS"
LIMITS_DIR="/etc/sshmanager/limits"

# انتخاب iptables (اولویت: legacy → nft → پیش‌فرض)
if command -v iptables-legacy >/dev/null 2>&1; then
  IPT="iptables-legacy"
elif command -v iptables-nft >/dev/null 2>&1; then
  IPT="iptables-nft"
else
  IPT="iptables"
fi

# بررسی پشتیبانی -w
if $IPT -L >/dev/null 2>&1; then
  if $IPT -w -L >/dev/null 2>&1; then
    IPT_W="$IPT -w"
  else
    IPT_W="$IPT"
  fi
else
  echo "❌ iptables command not found!"
  exit 1
fi

# ساخت chain اگر نبود
$IPT_W -N "$CHAIN" 2>/dev/null || true

# تضمین پرش OUTPUT در خط ۱
if ! $IPT_W -C OUTPUT -j "$CHAIN" 2>/dev/null; then
  $IPT_W -I OUTPUT 1 -j "$CHAIN"
fi

# پاکسازی رول‌های قبلی (ضد duplication)
$IPT_W -F "$CHAIN"

# جمع‌آوری UIDهای واقعی
declare -A WANT_UIDS
while IFS=: read -r user _ uid _; do
  [[ -n "$user" && "$uid" =~ ^[0-9]+$ ]] || continue
  if (( uid >= 1000 )) && [[ "$user" != "nobody" ]]; then
    WANT_UIDS["$uid"]=1
  fi
done < <(getent passwd)

# اگر limits هم هست، اسم‌ها را به UID تبدیل کن و اضافه کن
if [[ -d "$LIMITS_DIR" ]]; then
  for f in "$LIMITS_DIR"/*.json; do
    [[ -f "$f" ]] || continue
    uname=$(jq -r '.username // empty' "$f" 2>/dev/null || true)
    if [[ -n "$uname" ]]; then
      uid=$(getent passwd "$uname" | cut -d: -f3 || true)
      if [[ -n "${uid:-}" && "$uid" =~ ^[0-9]+$ && "$uid" -ge 1000 ]]; then
        WANT_UIDS["$uid"]=1
      fi
    fi
  done
fi

# اضافه‌کردن دقیقاً یک rule ACCEPT برای هر UID
for uid in $(printf "%s\n" "${!WANT_UIDS[@]}" | sort -n); do
  $IPT_W -A "$CHAIN" -m owner --uid-owner "$uid" -j ACCEPT
done

echo "[✓] iptables fixed."

EOF

##########
install -m 755 /root/fix-iptables.sh /usr/local/bin/fix-iptables.sh

systemctl daemon-reload || true
systemctl start fix-iptables.service 2>/dev/null || bash /root/fix-iptables.sh


chmod +x /root/fix-iptables.sh
systemctl enable --now fix-iptables.service
