
cat > /root/fix-iptables.sh << 'EOF'
# /root/fix-iptables.sh
#!/usr/bin/env bash
set -euo pipefail

echo "[+] Fixing iptables for SSH per-UID accounting..."


CHAIN="SSH_USERS"
LIMITS_DIR="/etc/sshmanager/limits"

# find iptables binary (legacy/nft) + -w flag if supported
IPT="iptables"
if command -v iptables-legacy >/dev/null 2>&1; then IPT="iptables-legacy"; fi
if command -v iptables-nft >/dev/null 2>&1; then IPT="iptables-nft"; fi
IPT_W="$IPT"
($IPT -w -L >/dev/null 2>&1) && IPT_W="$IPT -w"

# create chain if missing
$IPT_W -N "$CHAIN" 2>/dev/null || true

# ensure OUTPUT jumps to SSH_USERS at line 1 (move if not first)
if $IPT_W -C OUTPUT -j "$CHAIN" 2>/dev/null; then
  # already exists, but ensure it's first
  FIRST_TARGET=$($IPT_W -L OUTPUT --line-numbers -n | awk 'NR==3{print $3}')
  if [[ "${FIRST_TARGET:-}" != "$CHAIN" ]]; then
    # delete existing jump(s) then insert as #1
    while $IPT_W -C OUTPUT -j "$CHAIN" 2>/dev/null; do
      $IPT_W -D OUTPUT -j "$CHAIN" || true
    done
    $IPT_W -I OUTPUT 1 -j "$CHAIN"
  fi
else
  $IPT_W -I OUTPUT 1 -j "$CHAIN"
fi

# snapshot old rules (برای حفظ شمارنده‌ها بعداً بازنمی‌سازیم؛ flush می‌کنیم)
$IPT_W -F "$CHAIN"

# جمع‌آوری لیست UIDها:
# 1) همه‌ی یوزرهای واقعی (UID>=1000 و not nobody)
# 2) یوزرنیم‌های موجود در LIMITS_DIR (اگر فایل json دارند و در سیستم هم یوزر تعریف شده)
declare -A WANT_UIDS

# from passwd
while IFS=: read -r user _ uid _; do
  [[ -n "$user" && "$uid" =~ ^[0-9]+$ ]] || continue
  if (( uid >= 1000 )) && [[ "$user" != "nobody" ]]; then
    WANT_UIDS["$uid"]=1
  fi
done < <(getent passwd)

# from limits json
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

# افزودن رول‌های owner به ترتیب UID برای قابلیت دیباگ بهتر
for uid in $(printf "%s\n" "${!WANT_UIDS[@]}" | sort -n); do
  $IPT_W -A "$CHAIN" -m owner --uid-owner "$uid" -j ACCEPT
done

# گزارش کوتاه
echo "[i] OUTPUT head:"
$IPT_W -L OUTPUT -v -n --line-numbers | sed -n '1,5p'
echo "[i] ${CHAIN} head:"
$IPT_W -L "$CHAIN" -v -n -x | sed -n '1,12p'

echo "[✓] iptables fixed."

EOF

chmod +x /root/fix-iptables.sh
systemctl enable --now fix-iptables.service
