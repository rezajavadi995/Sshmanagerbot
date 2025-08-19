cat > /root/fix-iptables.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

CHAIN_ACCT="SSH_USERS"
CHAIN_MARK="SSH_MARK"
LIMITS_DIR="/etc/sshmanager/limits"

# pick iptables binary
if command -v iptables-legacy >/dev/null 2>&1; then IPT="iptables-legacy"; else IPT="iptables"; fi
if $IPT -w -L >/dev/null 2>&1; then IPT="$IPT -w"; fi

# modules (best effort)
modprobe xt_cgroup   2>/dev/null || true
modprobe xt_owner    2>/dev/null || true
modprobe xt_connmark 2>/dev/null || true

# ensure chains exist
$IPT -t mangle -N "$CHAIN_ACCT" 2>/dev/null || true
$IPT -t mangle -N "$CHAIN_MARK"  2>/dev/null || true

# global jumps (idempotent; no flush)
$IPT -t mangle -C OUTPUT  -j "$CHAIN_MARK"  2>/dev/null || $IPT -t mangle -I OUTPUT 1  -j "$CHAIN_MARK"
$IPT -t mangle -C OUTPUT  -j "$CHAIN_ACCT" 2>/dev/null || $IPT -t mangle -I OUTPUT 2  -j "$CHAIN_ACCT"
$IPT -t mangle -C FORWARD -j "$CHAIN_ACCT" 2>/dev/null || $IPT -t mangle -I FORWARD 1 -j "$CHAIN_ACCT"
$IPT -t mangle -C PREROUTING -j CONNMARK --restore-mark 2>/dev/null || $IPT -t mangle -I PREROUTING 1 -j CONNMARK --restore-mark

# helper: ensure a rule exists (by grep signature)
ensure_rule() {
  local table="$1" chain="$2" pattern="$3" add_cmd="$4"
  if ! $IPT -t "$table" -S "$chain" | grep -F -- "$pattern" >/dev/null 2>&1; then
    eval "$add_cmd"
  fi
}

# collect target UIDs
declare -A WANT
# real users >=1000
while IFS=: read -r user _ uid _; do
  [[ "$uid" =~ ^[0-9]+$ ]] || continue
  (( uid >= 1000 )) || continue
  [[ "$user" != "nobody" ]] || continue
  WANT["$uid"]="$user"
done < <(getent passwd)

# include any usernames in limits dir
if [[ -d "$LIMITS_DIR" ]]; then
  for f in "$LIMITS_DIR"/*.json; do
    [[ -f "$f" ]] || continue
    u=$(jq -r '.username // empty' "$f" 2>/dev/null || true)
    if [[ -n "$u" ]]; then
      uid=$(getent passwd "$u" | cut -d: -f3 || true)
      [[ "$uid" =~ ^[0-9]+$ && "$uid" -ge 1000 ]] && WANT["$uid"]="$u"
    fi
  done
fi

# add rules per user (no flush)
for uid in $(printf "%s\n" "${!WANT[@]}" | sort -n); do
  user="${WANT[$uid]}"
  path="/user.slice/user-${uid}.slice"

  # 1) cgroup path (preferred)
  sig=" -m cgroup --path ${path} -m comment --comment sshmanager:user=${user};uid=${uid};mode=cgroup "
  ensure_rule mangle "$CHAIN_ACCT" "$sig" \
    "$IPT -t mangle -A $CHAIN_ACCT -m cgroup --path $path -m comment --comment 'sshmanager:user=${user};uid=${uid};mode=cgroup' -j ACCEPT"

  # 2) owner (backup for OUTPUT)
  sig2=" -m owner --uid-owner ${uid} -m comment --comment sshmanager:user=${user};uid=${uid};mode=owner "
  ensure_rule mangle "$CHAIN_ACCT" "$sig2" \
    "$IPT -t mangle -A $CHAIN_ACCT -m owner --uid-owner $uid -m comment --comment 'sshmanager:user=${user};uid=${uid};mode=owner' -j ACCEPT"

  # 3) connmark (backup for FORWARD)
  mark=$((0x10000 + uid)); hex=$(printf "0x%X" "$mark")
  sig3=" -m connmark --mark ${mark} -m comment --comment sshmanager:user=${user};uid=${uid};mode=connmark;mark=${hex} "
  ensure_rule mangle "$CHAIN_ACCT" "$sig3" \
    "$IPT -t mangle -A $CHAIN_ACCT -m connmark --mark $mark -m comment --comment 'sshmanager:user=${user};uid=${uid};mode=connmark;mark=${hex}' -j ACCEPT"

  # mark/save in MARK chain (for new conns)
  ensure_rule mangle "$CHAIN_MARK" " --uid-owner ${uid} -j MARK --set-mark ${mark}" \
    "$IPT -t mangle -A $CHAIN_MARK -m owner --uid-owner $uid -j MARK --set-mark $mark"
  ensure_rule mangle "$CHAIN_MARK" " --uid-owner ${uid} -j CONNMARK --save-mark" \
    "$IPT -t mangle -A $CHAIN_MARK -m owner --uid-owner $uid -j CONNMARK --save-mark"
done

echo "[OK] iptables (cgroup/owner/connmark) ensured without flushing."
EOF

########
chmod +x /root/fix-iptables.sh


##########


systemctl daemon-reload || true
systemctl start fix-iptables.service 2>/dev/null || bash /root/fix-iptables.sh


chmod +x /root/fix-iptables.sh
systemctl enable --now fix-iptables.service
