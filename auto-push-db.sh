#!/bin/bash
# Auto-push database files to git every 5 minutes

cd /home/brenesamerica/POS

# Database files to track
DB_FILES="pos_test.db pos_prod.db roast_tracker_test.db roast_tracker_prod.db"

# Check if there are changes to any database files
CHANGES=0
for db in $DB_FILES; do
    if [ -f "$db" ] && ! git diff --quiet "$db" 2>/dev/null; then
        CHANGES=1
        break
    fi
done

# Also check for untracked database files
for db in $DB_FILES; do
    if [ -f "$db" ] && ! git ls-files --error-unmatch "$db" >/dev/null 2>&1; then
        CHANGES=1
        break
    fi
done

if [ $CHANGES -eq 0 ]; then
    echo "$(date): No database changes to commit"
    exit 0
fi

# Add only the database files that exist
for db in $DB_FILES; do
    if [ -f "$db" ]; then
        git add "$db"
    fi
done

# Commit with timestamp
git commit -m "Auto-backup: Database update $(date '+%Y-%m-%d %H:%M:%S')"

# Push to remote
if git push origin main; then
    echo "$(date): Successfully pushed database backup to git"
else
    echo "$(date): Failed to push to git"
    exit 1
fi
