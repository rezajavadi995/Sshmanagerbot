cat > /usr/local/bin/lock_user.sh << 'EOF'
#!/usr/bin/env bash
# Wrapper to call the real locker
# Usage: lock_user.sh <username> [reason]
set -euo pipefail
USER="${1:?username required}"
REASON="${2:-quota}"
exec /usr/bin/python3 /root/sshmanager/lock_user.py "$USER" "$REASON"
EOF