#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

echo "=== Betting Quant Engine — GitHub Backup & Sync ==="
echo "Timestamp: $(date -u +"%Y-%m-%dT%H:%M:%SZ")"

# 1. Ensure git remote origin is set
REMOTE_URL="git@github.com:xbtblvrg/Betting_tips_engine.git"
CURRENT_REMOTE=$(git remote get-url origin 2>/dev/null || echo "")

if [ "$CURRENT_REMOTE" != "$REMOTE_URL" ]; then
    echo "Setting remote origin to $REMOTE_URL"
    git remote remove origin 2>/dev/null || true
    git remote add origin "$REMOTE_URL"
fi

# 2. Stage all source, docs, config, tests, and root files (excluding ignored files in .gitignore)
echo "Staging files..."
git add src/ tests/ docs/ config/ scripts/ knowledge/cards/ package.json pyproject.toml .gitignore AGENTS.md GEMINI.md README.md COLD_START_RECOVERY.md 2>/dev/null || true

# 3. Check if there are changes to commit
if git diff --staged --quiet; then
    echo "No staged changes to commit."
else
    COMMIT_MSG="chore(backup): auto-sync system state $(date -u +"%Y-%m-%d %H:%M UTC")"
    if [ $# -gt 0 ]; then
        COMMIT_MSG="$*"
    fi
    echo "Committing changes: $COMMIT_MSG"
    git commit -m "$COMMIT_MSG"
fi

# 4. Push to GitHub
CURRENT_BRANCH=$(git branch --show-current)
echo "Pushing branch '$CURRENT_BRANCH' to origin..."
git push -u origin "$CURRENT_BRANCH"

echo "=== Sync to GitHub completed successfully! ==="
