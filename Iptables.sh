cat > /root/fix-iptables.sh << 'EOF'
# /root/fix-iptables.sh
#!/usr/bin/env bash
set -euo pipefail

# ---------- utils ----------
choose_ipt() {
  if command -v iptables-legacy >/dev/null 2>&1; then echo iptables-legacy; else echo iptables; fi
}
IPTBIN="$(choose_ipt)"
IPT=("$IPTBIN")
# add -w if supported
if "$IPTBIN" -w -L >/dev/null 2>&1; then IPT+=(-w); fi

modprobe xt_connmark >/dev/null 2>&1 || true
modprobe xt_MARK >/dev/null 2>&1 || true
modprobe xt_owner >/dev/null 2>&1 || true
modprobe xt_comment >/dev/null 2>&1 || true

# safe check/append/insert
rule_exists() { "${IPT[@]}" -t "$1" -C "$2" "${@:3}" >/dev/null 2>&1; }
ensure_append() { local t="$1" c="$2"; shift 2; rule_exists "$t" "$c" "$@" || "${IPT[@]}" -t "$t" -A "$c" "$@"; }
ensure_insert_at() { local t="$1" c="$2" idx="$3"; shift 3; rule_exists "$t" "$c" "$@" || "${IPT[@]}" -t "$t" -I "$c" "$idx" "$@"; }
ensure_jump_at() { local t="$1" from="$2" to="$3" idx="$4"; rule_exists "$t" "$from" -j "$to" || "${IPT[@]}" -t "$t" -I "$from" "$idx" -j "$to"; }
ensure_new_chain() { "${IPT[@]}" -t mangle -N "$1" 2>/dev/null || true; }

# ---------- chains ----------
ensure_new_chain SSH_MARK
ensure_new_chain SSH_USERS_OUT
ensure_new_chain SSH_USERS_IN
ensure_new_chain SSH_USERS_FWD

# ---------- global jumps & connmark plumbing ----------
# PREROUTING: restore mark early for incoming traffic
ensure_insert_at mangle PREROUTING 1 -j CONNMARK --restore-mark --nfmask 0xffffffff --ctmask 0xffffffff

# OUTPUT: first restore, then mark, then count by owner
ensure_insert_at mangle OUTPUT 1 -j CONNMARK --restore-mark --nfmask 0xffffffff --ctmask 0xffffffff
ensure_jump_at   mangle OUTPUT SSH_MARK     1
ensure_jump_at   mangle OUTPUT SSH_USERS_OUT 2

# INPUT/FORWARD: count by connmark
ensure_jump_at   mangle INPUT   SSH_USERS_IN   1
ensure_jump_at   mangle FORWARD SSH_USERS_FWD  1

# ---------- per-user rules ----------
# users = همه‌ی فایل‌های /etc/sshmanager/limits/*.json
USERS_DIR="/etc/sshmanager/limits"
if [[ -d "$USERS_DIR" ]]; then
  for jf in "$USERS_DIR"/*.json; do
    [[ -e "$jf" ]] || continue
    user="$(basename "$jf" .json)"
    uid="$(id -u "$user" 2>/dev/null || true)"
    [[ -n "$uid" ]] || continue

    # یک connmark یکتا بر اساس uid (ساده و پایدار)
    # (اگر خودت mark سفارشی می‌خواهی، اینجا جای آن است.)
    mark=$(( 0x10000 + uid ))           # مثلاً 0x1xxxx
    # ---- OUTPUT (owner) ----
    ensure_append mangle SSH_USERS_OUT -m owner --uid-owner "$uid" -m comment --comment "sshmanager:user=$user;uid=$uid;mode=owner" -j ACCEPT

    # ---- SSH_MARK: روی ترافیک این uid اول MARK بعد SAVE ----
    ensure_append mangle SSH_MARK -m owner --uid-owner "$uid" -j MARK --set-mark "$mark"
    ensure_append mangle SSH_MARK -m owner --uid-owner "$uid" -j CONNMARK --save-mark --nfmask 0xffffffff --ctmask 0xffffffff

    # ---- INPUT/FORWARD (connmark) ----
    ensure_append mangle SSH_USERS_IN  -m connmark --mark "$mark" -m comment --comment "sshmanager:user=$user;uid=$uid;mode=connmark;dir=in"   -j ACCEPT
    ensure_append mangle SSH_USERS_FWD -m connmark --mark "$mark" -m comment --comment "sshmanager:user=$user;uid=$uid;mode=connmark;dir=fwd"  -j ACCEPT
  done
fi

exit 0
EOF


chmod +x /root/fix-iptables.sh




systemctl daemon-reload || true
systemctl start fix-iptables.service 2>/dev/null || bash /root/fix-iptables.sh


chmod +x /root/fix-iptables.sh
systemctl enable --now fix-iptables.service
