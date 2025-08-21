cat > /root/fix-iptables.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

CHAIN_OUT="SSH_USERS_OUT"
CHAIN_FWD="SSH_USERS_FWD"
CHAIN_IN="SSH_USERS_IN"
LIMITS_DIR="/etc/sshmanager/limits"

# انتخاب iptables
if command -v iptables-legacy >/dev/null 2>&1; then
    IPT="iptables-legacy"
else
    IPT="iptables"
fi

# اطمینان از وجود و پاک‌سازی chainها
for c in "$CHAIN_OUT" "$CHAIN_FWD" "$CHAIN_IN"; do
    $IPT -t mangle -N "$c" 2>/dev/null || true
    $IPT -t mangle -F "$c"
done

# اتصال chainها به جریان شبکه (وجودی/تکراری نبودن)
$IPT -t mangle -C OUTPUT -j "$CHAIN_OUT" 2>/dev/null || $IPT -t mangle -A OUTPUT  -j "$CHAIN_OUT"
$IPT -t mangle -C FORWARD -j "$CHAIN_FWD" 2>/dev/null || $IPT -t mangle -A FORWARD -j "$CHAIN_FWD"
$IPT -t mangle -C INPUT  -j "$CHAIN_IN"  2>/dev/null || $IPT -t mangle -A INPUT   -j "$CHAIN_IN"

# اضافه کردن رول‌ها برای هر یوزر
shopt -s nullglob
for f in "$LIMITS_DIR"/*.json; do
    # نام کاربر از نام فایل (سازگار با ساختار پروژه)
    user="$(basename "$f" .json)"
    # گرفتن UID؛ اگر کاربر موجود نیست، رد شو
    uid="$($IPT -V >/dev/null 2>&1; id -u "$user" 2>/dev/null || true)"
    [[ -n "$uid" ]] || continue

    # تولید mark یکتا بر اساس UID (بدون تغییر منطق)
    mark_hex=$(printf "0x%X" $((0x10000 + uid)))

    # --- OUTPUT: مارک‌زدن اتصال + شمارش مالک ---
    $IPT -t mangle -A "$CHAIN_OUT" -m owner --uid-owner "$uid" \
        -m comment --comment "sshmanager:user=$user;uid=$uid;mode=owner-mark" \
        -j CONNMARK --set-mark "$mark_hex"

    $IPT -t mangle -A "$CHAIN_OUT" -m owner --uid-owner "$uid" \
        -m comment --comment "sshmanager:user=$user;uid=$uid;mode=owner-count" \
        -j ACCEPT

    # --- FORWARD: شمارش بر مبنای connmark (بدون تغییر منطق) ---
    $IPT -t mangle -A "$CHAIN_FWD" -m connmark --mark "$mark_hex" \
        -m comment --comment "sshmanager:user=$user;uid=$uid;mode=connmark-fwd;mark=$mark_hex" \
        -j ACCEPT

    # --- INPUT: شمارش بر مبنای connmark برای ترافیک ورودی همان اتصال ---
    $IPT -t mangle -A "$CHAIN_IN" -m connmark --mark "$mark_hex" \
        -m comment --comment "sshmanager:user=$user;uid=$uid;mode=connmark-in;mark=$mark_hex" \
        -j ACCEPT
done

echo "[OK] iptables updated: users=$(ls "$LIMITS_DIR"/*.json 2>/dev/null | wc -l)"

EOF



chmod +x /root/fix-iptables.sh



systemctl stop fix-iptables.service
systemctl daemon-reload
systemctl enable --now fix-iptables.service
systemctl start fix-iptables.service

chmod +x /root/fix-iptables.sh
systemctl enable --now fix-iptables.service
