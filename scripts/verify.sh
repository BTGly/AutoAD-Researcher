#!/usr/bin/env bash
set -euo pipefail

echo "[verify] checking project structure..."

test -d scripts
test -f scripts/verify.sh
test -f scripts/verify_and_push.sh
test -f .gitignore

echo "[verify] checking git status..."
git rev-parse --is-inside-work-tree >/dev/null

echo "[verify] done."
