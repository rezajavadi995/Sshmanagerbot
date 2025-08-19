cat > /root/fix-iptables.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

echo "[+] Fixing iptables for SSH per-UID accounting (MARK + CONNMARK)..."

CHAIN_ACCT="SSH_USERS"          # حسابداری
CHAIN_MARK="SSH_MARK"           # ست کردن مارک‌ها
LIMITS_DIR="/etc/sshmanager/limits"

# انتخاب iptables (legacy → nft → default)
if command -v iptables-legacy >/dev/null 2>&1; then
  IPT="iptables-legacy"
elif command -v iptables-nft >/dev/null 2>&1; then
  IPT="iptables-nft"
else
  IPT="iptables"
fi

# iptables-save برای counters
if command -v iptables-save >/dev/null 2>&1; then
  IPTS="iptables-save"
elif command -v iptables-legacy-save >/dev/null 2>&1; then
  IPTS="iptables-legacy-save"
else
  IPTS="iptables-save"
fi

# پشتیبانی -w
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

# ماژول‌های لازم (بدون خطا اگر نبودند)
modprobe xt_owner     2>/dev/null || true
modprobe xt_mark      2>/dev/null || true
modprobe xt_connmark  2>/dev/null || true

# ساخت chainها
$IPT_W -t mangle -N "$CHAIN_ACCT" 2>/dev/null || true
$IPT_W -t mangle -N "$CHAIN_MARK" 2>/dev/null || true

# پرش‌های عمومی
if ! $IPT_W -t mangle -C OUTPUT -j "$CHAIN_MARK" 2>/dev/null; then
  $IPT_W -t mangle -I OUTPUT 1 -j "$CHAIN_MARK"
fi
if ! $IPT_W -t mangle -C OUTPUT -j "$CHAIN_ACCT" 2>/dev/null; then
  $IPT_W -t mangle -I OUTPUT 2 -j "$CHAIN_ACCT"
fi
# FORWARD → اگر نشد، فقط هشدار بده
if ! $IPT_W -t mangle -C FORWARD -j "$CHAIN_ACCT" 2>/dev/null; then
  if ! $IPT_W -t mangle -I FORWARD 1 -j "$CHAIN_ACCT" 2>/dev/null; then
    echo "[!] Warning: kernel does not allow FORWARD → skipping..."
  fi
fi
# restore-mark در PREROUTING
if ! $IPT_W -t mangle -C PREROUTING -j CONNMARK --restore-mark 2>/dev/null; then
  $IPT_W -t mangle -I PREROUTING 1 -j CONNMARK --restore-mark
fi

# خالی‌کردن chainها
$IPT_W -t mangle -F "$CHAIN_MARK"
$IPT_W -t mangle -F "$CHAIN_ACCT"

# جمع‌آوری UIDهای هدف
declare -A WANT_UIDS
while IFS=: read -r user _ uid _; do
  [[ -n "$user" && "$uid" =~ ^[0-9]+$ ]] || continue
  if (( uid >= 1000 )) && [[ "$user" != "nobody" ]]; then
    WANT_UIDS["$uid"]="$user"
  fi
done < <(getent passwd)

if [[ -d "$LIMITS_DIR" ]]; then
  for f in "$LIMITS_DIR"/*.json; do
    [[ -f "$f" ]] || continue
    uname=$(jq -r '.username // empty' "$f" 2>/dev/null || true)
    if [[ -n "$uname" ]]; then
      uid=$(getent passwd "$uname" | cut -d: -f3 || true)
      if [[ -n "${uid:-}" && "$uid" =~ ^[0-9]+$ && "$uid" -ge 1000 ]]; then
        WANT_UIDS["$uid"]="$uname"
      fi
    fi
  done
fi

# اضافه کردن قوانین برای هر UID
for uid in $(printf "%s\n" "${!WANT_UIDS[@]}" | sort -n); do
  user="${WANT_UIDS[$uid]}"
  MARK_DEC=$(( 0x10000 + uid ))
  MARK_HEX=$(printf "0x%X" "$MARK_DEC")

  # ست‌مارک + ذخیره
  $IPT_W -t mangle -A "$CHAIN_MARK" -m owner --uid-owner "$uid" \
    -j MARK --set-mark "$MARK_DEC"
  $IPT_W -t mangle -A "$CHAIN_MARK" -m owner --uid-owner "$uid" \
    -j CONNMARK --save-mark

  # حسابداری OUTPUT
  $IPT_W -t mangle -A "$CHAIN_ACCT" -m owner --uid-owner "$uid" \
    -m comment --comment "sshmanager:user=${user};uid=${uid};mode=owner" \
    -j ACCEPT

  # حسابداری FORWARD/connmark
  $IPT_W -t mangle -A "$CHAIN_ACCT" -m connmark --mark "$MARK_DEC" \
    -m comment --comment "sshmanager:user=${user};uid=${uid};mode=connmark;mark=${MARK_HEX}" \
    -j ACCEPT

  echo "[+] rules added for $user (uid=$uid, mark=$MARK_HEX)"
done

echo "[✓] iptables fixed (MARK/CONNMARK)."

EOF

########
chmod +x /root/fix-iptables.sh


##########
install -m 755 /root/fix-iptables.sh /usr/local/bin/fix-iptables.sh

systemctl daemon-reload || true
systemctl start fix-iptables.service 2>/dev/null || bash /root/fix-iptables.sh


chmod +x /root/fix-iptables.sh
systemctl enable --now fix-iptables.service
