#!/bin/bash
# Auto-push database files to git every 5 minutes

cd /home/brenesamerica/POS

# Check if there are changes to commit
if git diff --quiet pos_test.db pos_prod.db 2>/dev/null; then
    echo "$(date): No database changes to commit"
    exit 0
fi

# Add only the database files
git add pos_test.db pos_prod.db

# Commit with timestamp
git commit -m "Auto-backup: Database update $(date '+%Y-%m-%d %H:%M:%S')

🤖 Generated with [Claude Code](https://claude.com/claude-code)

Co-Authored-By: Claude <noreply@anthropic.com>"

# Push to remote
if git push origin main; then
    echo "$(date): Successfully pushed database backup to git"
else
    echo "$(date): Failed to push to git"
    exit 1
fi
