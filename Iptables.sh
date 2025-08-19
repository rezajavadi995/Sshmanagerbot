cat > /root/fix-iptables.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail

echo "[+] Fixing iptables for SSH per-UID accounting..."

CHAIN="SSH_USERS"
LIMITS_DIR="/etc/sshmanager/limits"

# انتخاب iptables (legacy → nft → default)
if command -v iptables-legacy >/dev/null 2>&1; then
  IPT="iptables-legacy"
elif command -v iptables-nft >/dev/null 2>&1; then
  IPT="iptables-nft"
else
  IPT="iptables"
fi

# بررسی پشتیبانی -w
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

# تست پشتیبانی cgroup match
HAS_CGROUP=0
modprobe xt_cgroup 2>/dev/null || true
if $IPT_W -m cgroup -h >/dev/null 2>&1; then
  HAS_CGROUP=1
fi

# ساخت chain اگر نبود
$IPT_W -N "$CHAIN" 2>/dev/null || true

# تضمین پرش OUTPUT در خط 1
if ! $IPT_W -C OUTPUT -j "$CHAIN" 2>/dev/null; then
  $IPT_W -I OUTPUT 1 -j "$CHAIN"
fi

# اگر cgroup پشتیبانی می‌شود، FORWARD هم به chain وصل شود
if [[ $HAS_CGROUP -eq 1 ]]; then
  if ! $IPT_W -C FORWARD -j "$CHAIN" 2>/dev/null; then
    $IPT_W -I FORWARD 1 -j "$CHAIN"
  fi
fi

# پاکسازی رول‌های قبلی (ضد duplication)
$IPT_W -F "$CHAIN"

# جمع‌آوری UIDهای هدف: همه‌ی کاربران واقعی + کاربران موجود در limits
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

# برای هر UID، rule های OUTPUT/owner و (در صورت امکان) FORWARD/cgroup اضافه کن
# هر Rule comment دارد تا parsing دقیق در اسکریپت پایتون ساده باشد
for uid in $(printf "%s\n" "${!WANT_UIDS[@]}" | sort -n); do
  user="${WANT_UIDS[$uid]}"

  # OUTPUT / owner (همیشه)
  $IPT_W -A "$CHAIN" -m owner --uid-owner "$uid" \
    -m comment --comment "sshmanager:user=${user};uid=${uid};mode=owner" \
    -j ACCEPT

  # FORWARD / cgroup (اگر ساپورت هست)
  if [[ $HAS_CGROUP -eq 1 ]]; then
    CGP="user.slice/user-${uid}.slice"
    # بعضی توزیع‌ها session-*.scope هم دارند؛ ولی user-UID.slice ریشه‌ی امن‌تری است
    # اگر نیاز شد می‌توانیم بعداً Matchهای دقیق‌تری اضافه کنیم.
    if $IPT_W -A "$CHAIN" -m cgroup --path "$CGP" \
         -m comment --comment "sshmanager:user=${user};uid=${uid};mode=cgroup;path=${CGP}" \
         -j ACCEPT 2>/dev/null; then
      echo "[+] cgroup rule added for $user (uid=$uid) path=$CGP"
    else
      echo "[!] cgroup add failed for $user (uid=$uid) — keeping OUTPUT/owner only."
    fi
  fi
done

echo "[✓] iptables fixed. (HAS_CGROUP=$HAS_CGROUP)"

EOF

##########
install -m 755 /root/fix-iptables.sh /usr/local/bin/fix-iptables.sh

systemctl daemon-reload || true
systemctl start fix-iptables.service 2>/dev/null || bash /root/fix-iptables.sh


chmod +x /root/fix-iptables.sh
systemctl enable --now fix-iptables.service
