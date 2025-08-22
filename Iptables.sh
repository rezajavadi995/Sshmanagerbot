cat > /root/fix-iptables.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

IPT=$(command -v iptables-legacy >/dev/null 2>&1 && echo "iptables-legacy" || echo "iptables")
IP6=$(command -v ip6tables-legacy >/dev/null 2>&1 && echo "ip6tables-legacy" || echo "ip6tables")

TABLE=mangle
LIMITS_DIR="/etc/sshmanager/limits"

CHAINS_OUT="SSH_USERS_OUT"
CHAINS_IN="SSH_USERS_IN"
CHAINS_FWD="SSH_USERS_FWD"

mark_of_uid() {
  local uid="$1"
  printf "0x%x" $(( (1<<20) | (uid & 0xFFFFF) ))
}

ensure_chains() {
  local bin="$1"
  for c in $CHAINS_OUT $CHAINS_IN $CHAINS_FWD; do
    $bin -t $TABLE -N "$c" 2>/dev/null || true
    $bin -t $TABLE -F "$c" || true
  done

  $bin -t $TABLE -C OUTPUT  -j $CHAINS_OUT 2>/dev/null || $bin -t $TABLE -A OUTPUT  -j $CHAINS_OUT
  $bin -t $TABLE -C INPUT   -j $CHAINS_IN  2>/dev/null || $bin -t $TABLE -A INPUT   -j $CHAINS_IN
  $bin -t $TABLE -C FORWARD -j $CHAINS_FWD 2>/dev/null || $bin -t $TABLE -A FORWARD -j $CHAINS_FWD
}

add_user_rules() {
  local bin="$1" user="$2" uid="$3" mark="$4"

  # OUT
  $bin -t $TABLE -A $CHAINS_OUT -m owner --uid-owner "$uid" \
       -m comment --comment "sshmanager:user=$user;uid=$uid;mode=owner-mark" \
       -j CONNMARK --set-mark "$mark"

  $bin -t $TABLE -A $CHAINS_OUT -m owner --uid-owner "$uid" \
       -m comment --comment "sshmanager:user=$user;uid=$uid;mode=owner-count" \
       -j ACCEPT

  # IN
  $bin -t $TABLE -A $CHAINS_IN -m connmark --mark "$mark" \
       -m comment --comment "sshmanager:user=$user;uid=$uid;mode=connmark-in" \
       -j ACCEPT

  # FWD
  $bin -t $TABLE -A $CHAINS_FWD -m connmark --mark "$mark" \
       -m comment --comment "sshmanager:user=$user;uid=$uid;mode=connmark-fwd" \
       -j ACCEPT
}

build_for_stack() {
  local bin="$1"
  ensure_chains "$bin"

  shopt -s nullglob
  for f in "$LIMITS_DIR"/*.json; do
    user="$(basename "$f" .json)"
    if id -u "$user" >/dev/null 2>&1; then
      uid="$(id -u "$user")"
      [ "$uid" -lt 1000 ] && continue
      mark="$(mark_of_uid "$uid")"
      add_user_rules "$bin" "$user" "$uid" "$mark"
    fi
  done
}

main() {
  build_for_stack "$IPT"
  build_for_stack "$IP6"
  echo "[OK] iptables rules updated (v4 & v6)."
}

main

EOF



chmod +x /root/fix-iptables.sh



systemctl stop fix-iptables.service
systemctl daemon-reload
systemctl enable --now fix-iptables.service
systemctl start fix-iptables.service

chmod +x /root/fix-iptables.sh
systemctl enable --now fix-iptables.service
