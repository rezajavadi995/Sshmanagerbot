# /usr/local/bin/sshmanager_backup.sh
cat > /usr/local/bin/sshmanager_backup.sh << 'EOF'
#!/bin/bash
set -euo pipefail
DATE=$(date +%F)
DEST="/root/backups/$DATE"
mkdir -p "$DEST"
cp -r /etc/sshmanager "$DEST/"
[ -f /root/sshmanager.py ] && cp /root/sshmanager.py "$DEST/sshmanager.py" || true
echo "✅ بکاپ انجام شد: $DEST"
EOF

chmod +x /usr/local/bin/sshmanager_backup.sh

# /etc/systemd/system/sshmanager-backup.service
cat > /etc/systemd/system/sshmanager-backup.service << 'EOF'
[Unit]
Description=Backup SSHManager Data
After=network.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/sshmanager_backup.sh
EOF

# /etc/systemd/system/sshmanager-backup.timer
cat > /etc/systemd/system/sshmanager-backup.timer << 'EOF'
[Unit]
Description=Daily Backup of SSHManager

[Timer]
OnCalendar=*-*-* 02:00:00
Persistent=true
Unit=sshmanager-backup.service

[Install]
WantedBy=timers.target
EOF
