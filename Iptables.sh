cat > /root/fix-iptables.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

# ================================
# Chains
# ================================
CHAIN_OUT="SSH_USERS_OUT"      # for OUTPUT (owner + cgroup)
CHAIN_FWD="SSH_USERS_FWD"      # for FORWARD (cgroup + connmark)
CHAIN_MARK="SSH_MARK"          # for marking new connections
LIMITS_DIR="/etc/sshmanager/limits"

# ================================
# pick iptables binary
# ================================
if command -v iptables-legacy >/dev/null 2>&1; then IPT="iptables-legacy"; else IPT="iptables"; fi
if $IPT -w -L >/dev/null 2>&1; then IPT="$IPT -w"; fi

# ================================
# modules (best effort)
# ================================
modprobe xt_cgroup   2>/dev/null || true
modprobe xt_owner    2>/dev/null || true
modprobe xt_connmark 2>/dev/null || true

# ================================
# ensure chains exist
# ================================
$IPT -t mangle -N "$CHAIN_OUT" 2>/dev/null || true
$IPT -t mangle -N "$CHAIN_FWD" 2>/dev/null || true
$IPT -t mangle -N "$CHAIN_MARK" 2>/dev/null || true

# ================================
# global jumps (idempotent; no flush)
# ================================
# OUTPUT → MARK + OUT
$IPT -t mangle -C OUTPUT -j "$CHAIN_MARK" 2>/dev/null || $IPT -t mangle -I OUTPUT 1 -j "$CHAIN_MARK"
$IPT -t mangle -C OUTPUT -j "$CHAIN_OUT" 2>/dev/null || $IPT -t mangle -I OUTPUT 2 -j "$CHAIN_OUT"

# FORWARD → FWD
$IPT -t mangle -C FORWARD -j "$CHAIN_FWD" 2>/dev/null || $IPT -t mangle -I FORWARD 1 -j "$CHAIN_FWD"

# PREROUTING → restore mark
$IPT -t mangle -C PREROUTING -j CONNMARK --restore-mark 2>/dev/null || \
  $IPT -t mangle -I PREROUTING 1 -j CONNMARK --restore-mark

# ================================
# helper: ensure a rule exists
# ================================
ensure_rule() {
  local table="$1" chain="$2" pattern="$3" add_cmd="$4"
  if ! $IPT -t "$table" -S "$chain" | grep -F -- "$pattern" >/dev/null 2>&1; then
    eval "$add_cmd"
  fi
}

# ================================
# collect target UIDs
# ================================
declare -A WANT

# real users >=1000
while IFS=: read -r user _ uid _; do
  [[ "$uid" =~ ^[0-9]+$ ]] || continue
  (( uid >= 1000 )) || continue
  [[ "$user" != "nobody" ]] || continue
  WANT["$uid"]="$user"
done < <(getent passwd)

# include usernames from limits dir
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

# ================================
# add rules per user (no flush)
# ================================
for uid in $(printf "%s\n" "${!WANT[@]}" | sort -n); do
  user="${WANT[$uid]}"
  path="/user.slice/user-${uid}.slice"
  mark=$((0x10000 + uid))
  hex=$(printf "0x%X" "$mark")

  # ---- OUTPUT chain: owner + cgroup
  sig_out=" -m owner --uid-owner ${uid} -m comment --comment sshmanager:user=${user};uid=${uid};mode=owner "
  ensure_rule mangle "$CHAIN_OUT" "$sig_out" \
    "$IPT -t mangle -A $CHAIN_OUT -m owner --uid-owner $uid -m comment --comment 'sshmanager:user=${user};uid=${uid};mode=owner' -j ACCEPT"

  sig_out2=" -m cgroup --path ${path} -m comment --comment sshmanager:user=${user};uid=${uid};mode=cgroup "
  ensure_rule mangle "$CHAIN_OUT" "$sig_out2" \
    "$IPT -t mangle -A $CHAIN_OUT -m cgroup --path $path -m comment --comment 'sshmanager:user=${user};uid=${uid};mode=cgroup' -j ACCEPT"

  # ---- FORWARD chain: only cgroup + connmark
  sig_fwd=" -m cgroup --path ${path} -m comment --comment sshmanager:user=${user};uid=${uid};mode=cgroup "
  ensure_rule mangle "$CHAIN_FWD" "$sig_fwd" \
    "$IPT -t mangle -A $CHAIN_FWD -m cgroup --path $path -m comment --comment 'sshmanager:user=${user};uid=${uid};mode=cgroup' -j ACCEPT"

  sig_fwd2=" -m connmark --mark ${mark} -m comment --comment sshmanager:user=${user};uid=${uid};mode=connmark;mark=${hex} "
  ensure_rule mangle "$CHAIN_FWD" "$sig_fwd2" \
    "$IPT -t mangle -A $CHAIN_FWD -m connmark --mark $mark -m comment --comment 'sshmanager:user=${user};uid=${uid};mode=connmark;mark=${hex}' -j ACCEPT"

  # ---- MARK chain: set + save
  sig_mark1=" --uid-owner ${uid} -j MARK --set-mark ${mark}"
  ensure_rule mangle "$CHAIN_MARK" "$sig_mark1" \
    "$IPT -t mangle -A $CHAIN_MARK -m owner --uid-owner $uid -j MARK --set-mark $mark"

  sig_mark2=" --uid-owner ${uid} -j CONNMARK --save-mark"
  ensure_rule mangle "$CHAIN_MARK" "$sig_mark2" \
    "$IPT -t mangle -A $CHAIN_MARK -m owner --uid-owner $uid -j CONNMARK --save-mark"
done

echo "[OK] iptables rules installed: OUTPUT(owner+cgroup), FORWARD(cgroup+connmark), MARK chain."

########
chmod +x /root/fix-iptables.sh


##########


systemctl daemon-reload || true
systemctl start fix-iptables.service 2>/dev/null || bash /root/fix-iptables.sh


chmod +x /root/fix-iptables.sh
systemctl enable --now fix-iptables.service
