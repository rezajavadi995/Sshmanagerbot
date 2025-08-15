
cat > /root/fix-iptables.sh << 'EOF'
# /root/fix-iptables.sh
#!/usr/bin/env bash
set -euo pipefail

echo "[+] Fixing iptables for per-UID bidirectional accounting (connmark)..."

CHAIN_MARK="SSH_MARK"
CHAIN_UIDS="SSH_UIDS"
LIMITS_DIR="/etc/sshmanager/limits"

# انتخاب backend سازگار
pick_backend() {
  local ipt_candidates=("iptables" "iptables-legacy" "iptables-nft")
  for b in "${ipt_candidates[@]}"; do
    if command -v "$b" >/dev/null 2>&1; then
      if "$b" -L >/dev/null 2>&1; then
        IPT="$b"
        break
      fi
    fi
  done
  : "${IPT:=iptables}"

  if [[ "$IPT" == "iptables-legacy" ]]; then
    SAVE="iptables-legacy-save"
  elif [[ "$IPT" == "iptables-nft" ]]; then
    SAVE="iptables-nft-save"
  else
    SAVE="iptables-save"
  fi

  if $IPT -w -L >/dev/null 2>&1; then
    IPTW="$IPT -w"
  else
    IPTW="$IPT"
  fi
}
pick_backend

# ساخت زنجیره‌ها در صورت نبود
$IPTW -N "$CHAIN_MARK" 2>/dev/null || true
$IPTW -N "$CHAIN_UIDS" 2>/dev/null || true

# اتصال idempotent: OUTPUT → SSH_MARK (برای set connmark) و OUTPUT/INPUT → SSH_UIDS (برای شمارش)
$IPTW -C OUTPUT -j "$CHAIN_MARK" 2>/dev/null || $IPTW -I OUTPUT 1 -j "$CHAIN_MARK"
# بعد از درج قبلی، این یکی را در پوزیشن 2 می‌گذاریم تا ترتیب پایدار بماند
$IPTW -C OUTPUT -j "$CHAIN_UIDS" 2>/dev/null || $IPTW -I OUTPUT 2 -j "$CHAIN_UIDS"
$IPTW -C INPUT  -j "$CHAIN_UIDS" 2>/dev/null || $IPTW -I INPUT  1 -j "$CHAIN_UIDS"

# پاکسازی امن رول‌های درون‌زنجیره (جریان فایروال را تغییر نمی‌دهیم)
$IPTW -F "$CHAIN_MARK"
$IPTW -F "$CHAIN_UIDS"

# گردآوری UID ها از سیستم و لیست JSON ها
declare -A WANT_UIDS
while IFS=: read -r user _ uid _; do
  [[ -n "$user" && "$uid" =~ ^[0-9]+$ ]] || continue
  # کاربرهای واقعی
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

# برای هر UID:
# 1) در OUTPUT: با owner+NEW، connmark اتصال را برابر UID می‌گذاریم (ست‌شدن کافیست، نیازی به ACCEPT نیست)
# 2) در هر دو مسیر: بر اساس connmark==UID فقط شمارش می‌کنیم و برمی‌گردیم (RETURN) تا جریان فایروال تغییر نکند
for uid in $(printf "%s\n" "${!WANT_UIDS[@]}" | sort -n); do
  # ست connmark روی اولین بسته اتصال (NEW)
  $IPTW -A "$CHAIN_MARK" -m owner --uid-owner "$uid" -m conntrack --ctstate NEW -j CONNMARK --set-mark "$uid"
  # رول‌های حسابداری (هدف: RETURN برای عدم تغییر مسیر)
  $IPTW -A "$CHAIN_UIDS" -m connmark --mark "$uid" -j RETURN
done

echo "[i] OUTPUT head:"
$IPTW -L OUTPUT -v -n --line-numbers | sed -n '1,6p'
echo "[i] INPUT head:"
$IPTW -L INPUT  -v -n --line-numbers | sed -n '1,6p'
echo "[i] $CHAIN_MARK:"
$IPTW -L "$CHAIN_MARK" -v -n -x | sed -n '1,12p'
echo "[i] $CHAIN_UIDS:"
$IPTW -L "$CHAIN_UIDS" -v -n -x | sed -n '1,12p'
echo "[✓] iptables fixed (bidirectional per-UID accounting via connmark)."



EOF

chmod +x /root/fix-iptables.sh
systemctl enable --now fix-iptables.service
