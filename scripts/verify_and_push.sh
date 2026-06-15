#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: scripts/verify_and_push.sh \"commit message\""
  exit 1
fi

COMMIT_MESSAGE="$1"

echo "[gate] running verification..."
bash scripts/verify.sh

echo "[gate] verification passed."

echo "[gate] checking git changes..."
if git diff --quiet && git diff --cached --quiet; then
  echo "[gate] no changes to commit."
  exit 0
fi

echo "[gate] staging changes..."
git add .

echo "[gate] committing..."
git commit -m "$COMMIT_MESSAGE"

echo "[gate] pushing..."
CURRENT_BRANCH="$(git branch --show-current)"
git push origin "$CURRENT_BRANCH"

echo "[gate] pushed successfully to origin/$CURRENT_BRANCH."
