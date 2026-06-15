#!/usr/bin/env bash
set -euo pipefail

if command -v uv >/dev/null 2>&1; then
  UV_BIN="uv"
elif [ -x "$HOME/.local/bin/uv" ]; then
  UV_BIN="$HOME/.local/bin/uv"
else
  echo "[verify] uv is required. Install it with: python -m pip install --user uv"
  exit 1
fi

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

echo "[verify] checking Python syntax..."
"$UV_BIN" run python -m compileall -q spikes/deepagents_harness src tests

echo "[verify] checking DeepAgents spike schema import..."
PYTHONPATH=spikes/deepagents_harness "$UV_BIN" run python - <<'PY'
from schema import ExperimentPlan, PatchPlan

ExperimentPlan.model_validate(
    {
        "experiment_goal": "smoke",
        "baseline": "PatchCore",
        "dataset": "MVTec AD",
        "categories": ["bottle"],
        "metrics": ["image-level AUROC"],
        "control_group": "baseline",
        "experiment_group": "experiment",
        "resource_budget": "single GPU",
        "risks": ["implementation risk"],
        "extra_field": {"kept": True},
    }
)
PatchPlan.model_validate(
    {
        "target_repo": "example",
        "files_to_inspect": ["README.md"],
        "files_to_modify": ["README.md"],
        "planned_changes": ["add note"],
        "expected_risks": ["none"],
        "requires_approval": True,
        "extra_field": {"kept": True},
    }
)
print("[verify] schema import ok.")
PY

echo "[verify] checking fixture JSON..."
"$UV_BIN" run python - <<'PY'
import json
from pathlib import Path

json.loads(Path("spikes/deepagents_harness/runs/run_demo/paper_summary.json").read_text())
print("[verify] fixture json ok.")
PY

echo "[verify] checking git status..."
git rev-parse --is-inside-work-tree >/dev/null

echo "[verify] done."
