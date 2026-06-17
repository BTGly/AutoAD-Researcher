#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT"

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

echo "[verify] checking AutoAD schemas..."
"$UV_BIN" run python - <<'PY'
from autoad_researcher.schemas import ExperimentPlan, PatchPlan

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
print("[verify] schemas ok.")
PY

echo "[verify] checking core imports..."
"$UV_BIN" run python - <<'PY'
from autoad_researcher.clarifiers import (
    IntentClarifierBackend,
    RuleBasedIntentClarifierBackend,
)
from autoad_researcher.core import (
    ArtifactStore,
    EventStore,
    IdeaGenerator,
    IdeaSourceRouter,
    InputIntake,
    IntentClarifier,
    PipelineController,
    PipelineResult,
    StageResult,
)
from autoad_researcher.ideation import (
    DirectIdeaBackend,
    IdeaGenerationBackend,
)
from autoad_researcher.core import PaperReader, RepositoryReader
from autoad_researcher.harness.simple_pipeline import SimplePipelineHarness
from autoad_researcher.readers import (
    StaticPaperReaderBackend,
    StaticRepositoryReaderBackend,
)
from autoad_researcher.schemas import (
    ClarificationContext,
    ClarificationQuestion,
    ClarifiedTask,
    EvidenceReference,
    IdeaCandidate,
    IdeaContext,
    IdeaGenerationResult,
    IdeaRouteDecision,
    InputTask,
    KnownFact,
    PaperSummary,
    RepositorySummary,
    SourceEntry,
    SourceManifest,
)

store = ArtifactStore(runs_root="runs")
events = EventStore(runs_root="runs")
stage = StageResult(
    run_id="run_demo",
    stage="experiment_planning",
    status="success",
    artifacts=["experiment_plan.json"],
)
pipeline = PipelineResult(
    run_id="run_demo",
    status="success",
    stages=[stage],
)
controller = PipelineController(
    harness=SimplePipelineHarness(runs_root="runs"),
    runs_root="runs",
)
print("[verify] core import ok.")
PY

echo "[verify] checking fixture JSON..."
"$UV_BIN" run python - <<'PY'
import json
from pathlib import Path

json.loads(Path("spikes/deepagents_harness/runs/run_demo/paper_summary.json").read_text())
print("[verify] fixture json ok.")
PY

echo "[verify] checking forbidden spike schema imports..."
"$UV_BIN" run python - <<'PY'
import sys
from pathlib import Path

root = Path(".")

for base in ["src", "tests", "scripts"]:
    for pyfile in sorted((root / base).rglob("*.py")):
        text = pyfile.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            s = line.strip()
            if s.startswith("from schema import") or s.startswith("import schema"):
                print(
                    f"Forbidden bare schema import in {pyfile}:{lineno}: {s}",
                    file=sys.stderr,
                )
                sys.exit(1)
print("[verify] no forbidden spike schema imports.")
PY

echo "[verify] checking CLI entrypoints..."
"$UV_BIN" run autoad --help >/dev/null
"$UV_BIN" run python -m autoad_researcher --help >/dev/null
echo "[verify] CLI entrypoints ok."

echo "[verify] checking benchmark preflight imports..."
"$UV_BIN" run python - <<'''PY'''
from autoad_researcher.benchmarks.repository import collect_repository_state
from autoad_researcher.benchmarks.dataset import build_dataset_manifest
from autoad_researcher.benchmarks.environment import collect_environment_snapshot
from autoad_researcher.benchmarks.preflight import run_preflight
print("[verify] benchmark preflight imports ok.")
PY

echo "[verify] checking benchmark config..."
test -f src/autoad_researcher/schemas/benchmark.py
test -f src/autoad_researcher/benchmarks/config.py
test -f scripts/benchmark/validate_case.py
test -f configs/benchmarks/internal_patchcore_mvtec_bottle_v1.yaml
test -f docs/adr/0001-internal-benchmark-selection.md
test -f docs/internal_benchmark_case.md
"$UV_BIN" run python scripts/benchmark/validate_case.py \
  configs/benchmarks/internal_patchcore_mvtec_bottle_v1.yaml >/dev/null
echo "[verify] benchmark config ok."

echo "[verify] checking benchmark preflight files..."
test -f src/autoad_researcher/benchmarks/repository.py
test -f src/autoad_researcher/benchmarks/dataset.py
test -f src/autoad_researcher/benchmarks/environment.py
test -f src/autoad_researcher/benchmarks/preflight.py
test -f scripts/benchmark/preflight.py
echo "[verify] checking benchmark environment lock..."
test -f src/autoad_researcher/benchmarks/environment_lock.py
test -f configs/benchmarks/environments/patchcore_linux_gpu/environment.yaml
test -f configs/benchmarks/environments/patchcore_linux_gpu/requirements.in
test -f configs/benchmarks/environments/patchcore_linux_gpu/requirements.lock.txt
"$UV_BIN" run python -c "
import yaml
from pathlib import Path
from autoad_researcher.benchmarks.environment_lock import BenchmarkEnvironmentSpec, validate_lockfile, compute_lockfile_sha256, parse_lockfile_pins
base = Path('configs/benchmarks/environments/patchcore_linux_gpu')
data = yaml.safe_load(open(base / 'environment.yaml'))
spec = BenchmarkEnvironmentSpec.model_validate(data)
lf = base / spec.lockfile_path
errors = validate_lockfile(lf)
assert not errors, f'lockfile invalid: {errors}'
actual_sha = compute_lockfile_sha256(lf)
assert actual_sha == spec.lockfile_sha256, f'SHA mismatch: {actual_sha[:16]} != {spec.lockfile_sha256[:16]}'
pins = parse_lockfile_pins(lf)
expected = {
    'torch': '==2.5.1+cu124',
    'torchvision': '==0.20.1+cu124',
    'timm': '==1.0.27',
    'faiss-cpu': '==1.14.3',
}
for name, version in expected.items():
    assert pins.get(name) == version, f'{name}: expected {version}, got {pins.get(name)}'
print('[verify] benchmark environment lock ok.')
"
echo "[verify] benchmark preflight files ok."

echo "[verify] checking environment plan fixtures..."
test -f src/autoad_researcher/environments/models.py
test -f src/autoad_researcher/environments/policy.py
test -f src/autoad_researcher/environments/io.py
test -f src/autoad_researcher/environments/builder.py
test -f src/autoad_researcher/environments/executor.py
test -f src/autoad_researcher/environments/result.py
test -f src/autoad_researcher/environments/adapters/base.py
test -f src/autoad_researcher/environments/adapters/uv_venv.py
test -f src/autoad_researcher/environments/adapters/pip_venv.py
test -f src/autoad_researcher/environments/adapters/existing_python.py
test -f src/autoad_researcher/environments/adapters/conda.py
test -f fixtures/environment_plans/python_cpu_uv.yaml
test -f fixtures/environment_plans/python_cuda_uv.yaml
test -f fixtures/environment_plans/existing_python.yaml
"$UV_BIN" run python - <<'PY'
from pathlib import Path

from autoad_researcher.environments import (
    load_environment_plan,
    validate_environment_plan_policy,
)

for path in sorted(Path("fixtures/environment_plans").glob("*.yaml")):
    plan = load_environment_plan(path)
    report = validate_environment_plan_policy(plan)
    assert report.status == "passed", path

print("[verify] environment plan fixtures ok.")
PY

echo "[verify] running pytest..."
"$UV_BIN" run --extra dev pytest -q

echo "[verify] checking development log..."
test -f notes/development-log.md
"$UV_BIN" run python - <<'PY'
from pathlib import Path
import re
import sys

index_path = Path("notes/development-log.md")
index_text = index_path.read_text(encoding="utf-8")
daily_links = re.findall(r"\((\d{4}-\d{2}-\d{2}\.md)\)", index_text)

if not daily_links:
    print("[verify] no daily log links found in notes/development-log.md", file=sys.stderr)
    sys.exit(1)

missing = [link for link in daily_links if not (Path("notes") / link).is_file()]
if missing:
    print(f"[verify] daily log file(s) missing from notes/: {missing}", file=sys.stderr)
    sys.exit(1)

print("[verify] development log index ok.")
PY

echo "[verify] checking git status..."
git rev-parse --is-inside-work-tree >/dev/null

echo "[verify] done."
