#!/bin/bash
cd /home/brenesamerica/POS

# Add database files if they have changes
git add pos_test.db pos_prod.db roast_tracker.db 2>/dev/null

# Check if there are changes to commit
if git diff --cached --quiet; then
    exit 0
fi

# Commit and push
git commit -m "Auto-backup databases $(date '+%Y-%m-%d %H:%M')"
git push
