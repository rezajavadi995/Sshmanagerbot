cat > /root/fix-iptables.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

CHAIN_OUT="SSH_USERS_OUT"
CHAIN_FWD="SSH_USERS_FWD"
CHAIN_MARK="SSH_MARK"
LIMITS_DIR="/etc/sshmanager/limits"

# pick iptables binary
if command -v iptables-legacy >/dev/null 2>&1; then IPT="iptables-legacy"; else IPT="iptables"; fi
if $IPT -w -L >/dev/null 2>&1; then IPT="$IPT -w"; fi

# modules (owner/connmark)
modprobe xt_owner    2>/dev/null || true
modprobe xt_connmark 2>/dev/null || true

# ensure chains exist
$IPT -t mangle -N "$CHAIN_OUT"  2>/dev/null || true
$IPT -t mangle -N "$CHAIN_FWD"  2>/dev/null || true
$IPT -t mangle -N "$CHAIN_MARK" 2>/dev/null || true

# global jumps (MARK سپس OUT/فوروارد)
$IPT -t mangle -C OUTPUT    -j "$CHAIN_MARK" 2>/dev/null || $IPT -t mangle -I OUTPUT    1 -j "$CHAIN_MARK"
$IPT -t mangle -C OUTPUT    -j "$CHAIN_OUT"  2>/dev/null || $IPT -t mangle -I OUTPUT    2 -j "$CHAIN_OUT"
$IPT -t mangle -C FORWARD   -j "$CHAIN_FWD"  2>/dev/null || $IPT -t mangle -I FORWARD   1 -j "$CHAIN_FWD"
$IPT -t mangle -C PREROUTING -j CONNMARK --restore-mark 2>/dev/null || \
  $IPT -t mangle -I PREROUTING 1 -j CONNMARK --restore-mark

ensure_rule() {
  local table="$1" chain="$2" pattern="$3" add_cmd="$4"
  if ! $IPT -t "$table" -S "$chain" | grep -F -- "$pattern" >/dev/null 2>&1; then
    eval "$add_cmd"
  fi
}

# collect target UIDs (سیستم‌اکانت‌ها حذف)
declare -A WANT
while IFS=: read -r user _ uid _; do
  [[ "$uid" =~ ^[0-9]+$ ]] || continue
  (( uid >= 1000 )) || continue
  [[ "$user" != "nobody" ]] || continue
  WANT["$uid"]="$user"
done < <(getent passwd)

# users from limits/*.json هم اضافه بشن
if [[ -d "$LIMITS_DIR" ]]; then
  for f in "$LIMITS_DIR"/*.json; do
    [[ -e "$f" ]] || continue
    u=$(jq -r '.username // empty' "$f" 2>/dev/null || true)
    if [[ -n "$u" ]]; then
      uid=$(getent passwd "$u" | cut -d: -f3 || true)
      [[ "$uid" =~ ^[0-9]+$ && "$uid" -ge 1000 ]] && WANT["$uid"]="$u"
    fi
  done
fi

# per-user rules
for uid in $(printf "%s\n" "${!WANT[@]}" | sort -n); do
  user="${WANT[$uid]}"
  mark=$((0x10000 + uid))
  hex=$(printf "0x%X" "$mark")

  # OUTPUT: از owner بگیر، با کامنت نشون بده
  sig1=" -m owner --uid-owner ${uid} -m comment --comment sshmanager:user=${user};uid=${uid};mode=owner "
  ensure_rule mangle "$CHAIN_OUT" "$sig1" \
    "$IPT -t mangle -A $CHAIN_OUT -m owner --uid-owner $uid -m comment --comment 'sshmanager:user=${user};uid=${uid};mode=owner' -j ACCEPT"

  # MARK: همه پکت‌های این UID نشانه‌گذاری و ذخیره connmark
  sig2=" --uid-owner ${uid} -j MARK --set-mark ${mark}"
  ensure_rule mangle "$CHAIN_MARK" "$sig2" \
    "$IPT -t mangle -A $CHAIN_MARK -m owner --uid-owner $uid -j MARK --set-mark $mark"

  sig3=" --uid-owner ${uid} -j CONNMARK --save-mark"
  ensure_rule mangle "$CHAIN_MARK" "$sig3" \
    "$IPT -t mangle -A $CHAIN_MARK -m owner --uid-owner $uid -j CONNMARK --save-mark"

  # FORWARD: فقط connmark، با کامنتِ کاربر
  sig4=" -m connmark --mark ${mark} -m comment --comment sshmanager:user=${user};uid=${uid};mode=connmark;mark=${hex} "
  ensure_rule mangle "$CHAIN_FWD" "$sig4" \
    "$IPT -t mangle -A $CHAIN_FWD -m connmark --mark $mark -m comment --comment 'sshmanager:user=${user};uid=${uid};mode=connmark;mark=${hex}' -j ACCEPT"
done

echo "[OK] iptables installed: OUT(owner), FWD(connmark), MARK."
EOF

########
chmod +x /root/fix-iptables.sh


##########


systemctl daemon-reload || true
systemctl start fix-iptables.service 2>/dev/null || bash /root/fix-iptables.sh


chmod +x /root/fix-iptables.sh
systemctl enable --now fix-iptables.service
