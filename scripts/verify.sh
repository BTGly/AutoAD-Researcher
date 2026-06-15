#!/usr/bin/env bash
set -euo pipefail

echo "[verify] checking project structure..."

test -d scripts
test -f scripts/verify.sh
test -f scripts/verify_and_push.sh
test -f .github/workflows/verify.yml
test -f .gitignore

echo "[verify] checking DeepAgents spike files..."

test -f spikes/deepagents_harness/README.md
test -f spikes/deepagents_harness/run_spike.py
test -f spikes/deepagents_harness/schema.py
test -f spikes/deepagents_harness/task.md
test -f spikes/deepagents_harness/task_security_test.md
test -f spikes/deepagents_harness/runs/run_demo/input_task.yaml
test -f spikes/deepagents_harness/runs/run_demo/paper_summary.json

echo "[verify] DeepAgents spike files exist."

echo "[verify] checking git status..."
git rev-parse --is-inside-work-tree >/dev/null

echo "[verify] done."
