#!/usr/bin/env bash
set -euo pipefail

STAGED_ONLY=false
if [ "${1:-}" = "--staged-only" ]; then
  STAGED_ONLY=true
  shift
fi

if [ $# -lt 1 ]; then
  echo "Usage: scripts/verify_and_push.sh [--staged-only] \"commit message\""
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
if [ "$STAGED_ONLY" = true ] && git diff --cached --quiet; then
  echo "[gate] staged-only mode requires explicitly staged changes."
  exit 1
fi
if [ "$STAGED_ONLY" = false ] && git diff --quiet && git diff --cached --quiet; then
  echo "[gate] no changes to commit."
  exit 0
fi

if [ "$STAGED_ONLY" = true ]; then
  echo "[gate] using explicitly staged changes only."
else
  echo "[gate] staging changes..."
  git add .
fi

echo "[gate] committing..."
git commit -m "$COMMIT_MESSAGE"

echo "[gate] pushing..."

# Load GitHub token from .env (in .gitignore, never committed)
if [ -f .env ]; then
  set -a; source .env; set +a
fi

CURRENT_BRANCH="$(git branch --show-current)"

if [ -n "${GITHUB_TOKEN:-}" ] && [ -n "${GITHUB_USER:-}" ] && [ -n "${GITHUB_REPO:-}" ]; then
  # Push directly with token URL — never modifies remote.origin.url, zero leak risk.
  git push "https://${GITHUB_USER}:${GITHUB_TOKEN}@github.com/${GITHUB_USER}/${GITHUB_REPO}.git" "$CURRENT_BRANCH"
else
  echo "[gate] GITHUB_TOKEN not set — trying default push (may prompt for auth)"
  git push origin "$CURRENT_BRANCH"
fi

echo "[gate] pushed successfully to origin/$CURRENT_BRANCH."
