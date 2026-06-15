#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: scripts/verify_and_push.sh \"commit message\""
  exit 1
fi

COMMIT_MESSAGE="$1"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

echo "[gate] running verification..."
bash "$SCRIPT_DIR/verify.sh"

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

# Load GitHub token from .env (in .gitignore, never committed)
if [ -f .env ]; then
  set -a; source .env; set +a
fi

CURRENT_BRANCH="$(git branch --show-current)"

if [ -n "${GITHUB_TOKEN:-}" ] && [ -n "${GITHUB_USER:-}" ] && [ -n "${GITHUB_REPO:-}" ]; then
  # Use token-embedded URL to avoid interactive auth prompt
  PUSH_URL="https://${GITHUB_USER}:${GITHUB_TOKEN}@github.com/${GITHUB_USER}/${GITHUB_REPO}.git"
  git remote set-url origin "$PUSH_URL"
  git push origin "$CURRENT_BRANCH"
  # Clean token from remote URL immediately
  git remote set-url origin "https://github.com/${GITHUB_USER}/${GITHUB_REPO}.git"
else
  echo "[gate] GITHUB_TOKEN not set — trying default push (may prompt for auth)"
  git push origin "$CURRENT_BRANCH"
fi

echo "[gate] pushed successfully to origin/$CURRENT_BRANCH."
