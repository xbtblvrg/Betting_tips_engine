#!/usr/bin/env bash
set -euo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

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

# 4. Push to GitHub if local is ahead of remote
CURRENT_BRANCH=$(git branch --show-current)
LOCAL_HASH=$(git rev-parse HEAD)
REMOTE_HASH=$(git rev-parse "origin/$CURRENT_BRANCH" 2>/dev/null || echo "")

if [ "$LOCAL_HASH" = "$REMOTE_HASH" ]; then
    echo "Branch '$CURRENT_BRANCH' is up to date with origin. Nothing to push."
else
    echo "Pushing branch '$CURRENT_BRANCH' to origin..."
    git push -u origin "$CURRENT_BRANCH"
fi

echo "=== Sync to GitHub completed successfully! ==="
