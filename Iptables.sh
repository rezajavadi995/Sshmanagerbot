
cat > /root/fix-iptables.sh << 'EOF'
# /root/fix-iptables.sh
#!/usr/bin/env bash
set -euo pipefail

echo "[+] Fixing iptables for SSH per-UID accounting..."

CHAIN="SSH_USERS"
LIMITS_DIR="/etc/sshmanager/limits"

# انتخاب iptables
if command -v iptables-legacy >/dev/null 2>&1; then
  IPT="iptables-legacy"
elif command -v iptables-nft >/dev/null 2>&1; then
  IPT="iptables-nft"
else
  IPT="iptables"
fi

# بررسی پشتیبانی -w (بدون گیر کردن)
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

# ساخت chain اگر وجود نداشت
$IPT_W -N "$CHAIN" 2>/dev/null || true

# تضمین اینکه OUTPUT خط ۱ -> CHAIN
if $IPT_W -C OUTPUT -j "$CHAIN" 2>/dev/null; then
  # اگر پرش اول نیست، همه پرش‌های قبلی را حذف کن و در خط ۱ بگذار
  first_target="$($IPT_W -L OUTPUT --line-numbers -n | awk 'NR==3{print $3}')"
  if [[ "${first_target:-}" != "$CHAIN" ]]; then
    while $IPT_W -C OUTPUT -j "$CHAIN" 2>/dev/null; do
      $IPT_W -D OUTPUT -j "$CHAIN" || true
    done
    $IPT_W -I OUTPUT 1 -j "$CHAIN"
  fi
else
  $IPT_W -I OUTPUT 1 -j "$CHAIN"
fi

# پاکسازی رول‌های قبلی CHAIN
$IPT_W -F "$CHAIN"

# جمع‌آوری UIDها
declare -A WANT_UIDS
while IFS=: read -r user _ uid _; do
  [[ -n "$user" && "$uid" =~ ^[0-9]+$ ]] || continue
  if (( uid >= 1000 )) && [[ "$user" != "nobody" ]]; then
    WANT_UIDS["$uid"]=1
  fi
done < <(getent passwd)

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

# اضافه کردن دقیقاً یک رول ACCEPT برای هر UID
for uid in $(printf "%s\n" "${!WANT_UIDS[@]}" | sort -n); do
  $IPT_W -A "$CHAIN" -m owner --uid-owner "$uid" -j ACCEPT
done

echo "[i] OUTPUT head:"
$IPT_W -L OUTPUT -v -n --line-numbers | sed -n '1,5p'
echo "[i] ${CHAIN} head:"
$IPT_W -L "$CHAIN" -v -n -x | sed -n '1,12p'

echo "[✓] iptables fixed."


EOF

chmod +x /root/fix-iptables.sh
systemctl enable --now fix-iptables.service
