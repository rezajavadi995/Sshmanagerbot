cat > /root/fix-iptables.sh << 'EOF'

#!/usr/bin/env bash
# /root/fix-iptables.sh
# Final: counting IN+OUT correctly using connmark; dual-stack (v4/v6)

set -euo pipefail

# Pick binaries with -w support if available
_pick() {
  local name="$1"
  if command -v "${name}-legacy" >/dev/null 2>&1; then
    if "${name}-legacy" -w -L >/dev/null 2>&1; then echo "${name}-legacy -w"; else echo "${name}-legacy"; fi
  else
    if "${name}" -w -L >/dev/null 2>&1; then echo "${name} -w"; else echo "${name}"; fi
  fi
}
IPT=$(_pick iptables)
IP6=$(_pick ip6tables)

# Chains
TABLE=mangle
CHAINS=(SSH_USERS_OUT SSH_USERS_IN SSH_USERS_FWD)

# Limits dir: هر فایل json اسم کاربر است (سازگار با پروژه فعلی)
LIMITS_DIR="/etc/sshmanager/limits"

# derive a stable 32-bit connmark from UID (0x100000 + (uid & 0xFFFFF))
mark_of_uid() {
  local uid="$1"
  printf "0x%x" $(( (1<<20) | (uid & 0xFFFFF) ))
}

ensure_chain_set() {
  local bin="$1"
  # create chains if missing
  for c in "${CHAINS[@]}"; do
    if ! eval $bin -t "$TABLE" -nL "$c" >/dev/null 2>&1; then
      eval $bin -t "$TABLE" -N "$c"
    fi
    # همیشه خالی‌شان کن تا قوانین خراب قدیمی نماند
    eval $bin -t "$TABLE" -F "$c" || true
  done
  # hook chains
  for hook in OUTPUT INPUT FORWARD; do
    if ! eval $bin -t "$TABLE" -C "$hook" -j SSH_USERS_${hook/PUT/OUT} >/dev/null 2>&1; then
      # برای OUTPUT زنجیره OUT، برای INPUT زنجیره IN، برای FORWARD زنجیره FWD
      local tgt="SSH_USERS_${hook/PUT/OUT}"
      eval $bin -t "$TABLE" -I "$hook" 1 -j "$tgt" || true
    fi
  done
}

add_user_rules() {
  local bin="$1" user="$2" uid="$3" mark="$4"

  # OUT: شناسه‌دار کردن کانکشن و شمارش خروجی با owner
  eval $bin -t "$TABLE" -A SSH_USERS_OUT -m owner --uid-owner "$uid" \
       -m comment --comment "sshmanager:user=${user};uid=${uid};mode=owner-mark" \
       -j CONNMARK --set-mark "$mark"
  eval $bin -t "$TABLE" -A SSH_USERS_OUT -m owner --uid-owner "$uid" \
       -m comment --comment "sshmanager:user=${user};uid=${uid};mode=owner-count" \
       -j ACCEPT

  # IN: شمارش تمام ورودی‌های همان conntrack mark
  eval $bin -t "$TABLE" -A SSH_USERS_IN -m connmark --mark "$mark" \
       -m comment --comment "sshmanager:user=${user};uid=${uid};mode=connmark-in" \
       -j ACCEPT

  # FWD: اگر ترافیک توسط کرنل forward می‌شود (tun/tap/…)
  eval $bin -t "$TABLE" -A SSH_USERS_FWD -m connmark --mark "$mark" \
       -m comment --comment "sshmanager:user=${user};uid=${uid};mode=connmark-fwd" \
       -j ACCEPT
}

build_for_stack() {
  local bin="$1"
  ensure_chain_set "$bin"

  # برای هر کاربر حجمی یک rule بساز
  if [ -d "$LIMITS_DIR" ]; then
    shopt -s nullglob
    for f in "$LIMITS_DIR"/*.json; do
      user="$(basename "$f" .json)"
      # فقط UIDهای غیرسیستمی
      if id -u "$user" >/dev/null 2>&1; then
        uid="$(id -u "$user")"
        [ "$uid" -lt 1000 ] && continue
        # اگر کاربر limited نیست، از روی فایل رد نشوید—ولی در پروژه حاضر همه‌ی فایل‌های این مسیر حجمی‌اند
        mark="$(mark_of_uid "$uid")"
        add_user_rules "$bin" "$user" "$uid" "$mark"
      fi
    done
  fi
}

main() {
  build_for_stack "$IPT"
  build_for_stack "$IP6"
  echo "OK: rules (v4/v6) are rebuilt."
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
