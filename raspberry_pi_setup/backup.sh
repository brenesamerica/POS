#!/bin/bash
# Café Tiko POS - Database Backup Script
# Add to crontab for automatic daily backups:
#   crontab -e
#   0 2 * * * /home/pi/pos/raspberry_pi_setup/backup.sh

APP_DIR="/home/pi/pos"
BACKUP_DIR="/home/pi/pos_backups"
DATE=$(date +%Y%m%d_%H%M%S)

# Create backup directory if it doesn't exist
mkdir -p $BACKUP_DIR

# Backup the database
if [ -f "$APP_DIR/pos_prod.db" ]; then
    cp "$APP_DIR/pos_prod.db" "$BACKUP_DIR/pos_prod_$DATE.db"
    echo "Backup created: $BACKUP_DIR/pos_prod_$DATE.db"
fi

if [ -f "$APP_DIR/pos_test.db" ]; then
    cp "$APP_DIR/pos_test.db" "$BACKUP_DIR/pos_test_$DATE.db"
    echo "Backup created: $BACKUP_DIR/pos_test_$DATE.db"
fi

# Keep only last 30 days of backups
find $BACKUP_DIR -name "*.db" -mtime +30 -delete

echo "Backup complete. Keeping last 30 days."
