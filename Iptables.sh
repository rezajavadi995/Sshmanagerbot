cat > /root/fix-iptables.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

CHAIN_OUT="SSH_USERS_OUT"
CHAIN_FWD="SSH_USERS_FWD"
CHAIN_MARK="SSH_MARK"
LIMITS_DIR="/etc/sshmanager/limits"

# انتخاب iptables
if command -v iptables-legacy >/dev/null 2>&1; then
    IPT="iptables-legacy"
else
    IPT="iptables"
fi

# اطمینان از وجود chainها
for c in $CHAIN_OUT $CHAIN_FWD $CHAIN_MARK; do
    $IPT -t mangle -N $c 2>/dev/null || true
    $IPT -t mangle -F $c
done

# اتصال chainها به جریان شبکه
$IPT -t mangle -C OUTPUT -j $CHAIN_OUT 2>/dev/null || $IPT -t mangle -A OUTPUT -j $CHAIN_OUT
$IPT -t mangle -C FORWARD -j $CHAIN_FWD 2>/dev/null || $IPT -t mangle -A FORWARD -j $CHAIN_FWD

# اضافه کردن رول‌ها برای هر یوزر
for f in "$LIMITS_DIR"/*.json; do
    [ -e "$f" ] || continue
    user=$(jq -r '.username' "$f")
    uid=$(id -u "$user" 2>/dev/null || true)
    [ -n "$uid" ] || continue

    # تولید mark یکتا
    mark_hex=$(printf "0x%X" $((0x10000 + uid)))

    # --- رول‌های OUTPUT ---
    # مارک زدن
    $IPT -t mangle -A $CHAIN_OUT -m owner --uid-owner "$uid" \
        -m comment --comment "sshmanager:user=$user;uid=$uid;mode=owner-mark" \
        -j CONNMARK --set-mark "$mark_hex"

    # شمارش بایت‌ها
    $IPT -t mangle -A $CHAIN_OUT -m owner --uid-owner "$uid" \
        -m comment --comment "sshmanager:user=$user;uid=$uid;mode=owner-count" \
        -j ACCEPT

    # --- رول‌های FORWARD ---
    $IPT -t mangle -A $CHAIN_FWD -m connmark --mark "$mark_hex" \
        -m comment --comment "sshmanager:user=$user;uid=$uid;mode=connmark;mark=$mark_hex" \
        -j ACCEPT
done

echo "[OK] iptables updated: users=$(ls $LIMITS_DIR/*.json 2>/dev/null | wc -l)"EOF


chmod +x /root/fix-iptables.sh




systemctl daemon-reload || true
systemctl start fix-iptables.service 2>/dev/null || bash /root/fix-iptables.sh


chmod +x /root/fix-iptables.sh
systemctl enable --now fix-iptables.service
