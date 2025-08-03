✅ ۲. بکاپ شبانه /etc/sshmanager/limits و کانفیگ‌ها

📁 پوشه مقصد بکاپ:

/root/backups/YYYY-MM-DD/
(تاریخ روز به صورت فولدر)

🔧 مراحل:

1. اسکریپت بکاپ:

cat > /usr/local/bin/sshmanager_backup.sh << 'EOF'
#!/bin/bash
DATE=$(date +%F)
DEST="/root/backups/$DATE"
mkdir -p "$DEST"

cp -r /etc/sshmanager "$DEST/"
cp /root/sshmanager.py "$DEST/sshmanager.py"

echo "✅ بکاپ انجام شد: $DEST"
EOF

chmod +x /usr/local/bin/sshmanager_backup.sh
########################################

2. ساخت systemd timer:

cat > /etc/systemd/system/sshmanager-backup.timer << 'EOF'
[Unit]
Description=Daily Backup of SSHManager

[Timer]
OnCalendar=*-*-* 02:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF


#######################################

3سرویس اجرا کننده: 

cat > /etc/systemd/system/sshmanager-backup.service << 'EOF'
[Unit]
Description=Backup SSHManager Data

[Service]
ExecStart=/usr/local/bin/sshmanager_backup.sh
EOF

#######################################

4 فعال سازی

systemctl daemon-reexec
systemctl daemon-reload
systemctl enable --now sshmanager-backup.timer


#######################################


> ✅ از این به بعد، هر شب ساعت ۲ نصف شب بکاپ می‌گیره
