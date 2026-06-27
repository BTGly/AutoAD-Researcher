# L3 Demo Runbook — AutoAD-Researcher

Reproducibility guide for the L3 rehearsal (commit `f60fe45`).

## Prerequisites

| Requirement | Value |
|---|---|
| GPU | NVIDIA RTX 4090 (CUDA 12.4 driver) |
| Python | 3.14 (main venv) + 3.12 (`.venv-gpu` for CUDA) |
| Dataset | MVTec AD bottle (see below) |
| Provider | DeepSeek API (Anthropic-format proxy) |
| Disk | ~5 GB for run artifacts |

## Environment Setup

```bash
# 1. Verify repo is clean
cd workspace/repos/patchcore-inspection
git status --short  # must be clean

# 2. Set provider credentials (do not echo or commit this value)
read -s -p "DeepSeek API key: " DEEPSEEK_API_KEY
export DEEPSEEK_API_KEY

# 3. Ensure dataset root
export AUTOAD_INTERNAL_BENCHMARK_DATASET_ROOT=/root/autodl-tmp/mvtec
ls $AUTOAD_INTERNAL_BENCHMARK_DATASET_ROOT/bottle/  # must exist
```

### Data Preparation

Download MVTec AD bottle category:

```bash
mkdir -p /root/autodl-tmp/mvtec/bottle/
# Download from official source or use:
# https://www.mydrive.ch/shares/38536/3830184030e49fe74747669442f0f282/download/420938113-1629952094
# Extract bottle/ into /root/autodl-tmp/mvtec/
```

Expected structure:
```
/root/autodl-tmp/mvtec/bottle/
├── ground_truth/
├── test/
└── train/
```

## Running the Full L3 Pipeline

### Preflight Check (No API calls)

```bash
export RUN_ID=run_l3_demo
uv run autoad stage3-acceptance \
  --run-id "$RUN_ID" \
  --mode l3-preflight \
  --provider-base-url "https://api.deepseek.com" \
  --json
```

Expected: `status: blocked, failure_reason: blocked_l3_real_run_deferred_preflight_only`

### Real Execution

Override the preflight deferral:

```bash
export AUTOAD_L3_REAL_EXECUTION_ALLOWED=1
export RUN_ID=run_l3_demo

# Full orchestrated run (all stages):
uv run autoad stage3-acceptance \
  --run-id "$RUN_ID" \
  --mode l3-preflight \
  --provider-base-url "https://api.deepseek.com" \
  --json
```

Or run individual stages (useful for debugging):

```bash
# Stage 3.6
uv run autoad patch-plan --run-id "$RUN_ID" --json

# Stage 3.7
uv run autoad patch-apply --run-id "$RUN_ID" --json

# Stage 3.8 (GPU benchmark, takes ~60s per attempt)
uv run autoad runner-execute --run-id "$RUN_ID" --json

# Stage 3.9
uv run autoad results-analysis --run-id "$RUN_ID" --json

# Stage 3.10
uv run autoad final-report --run-id "$RUN_ID" --json
```

### Reset Between Runs

```bash
# Clean all downstream stages
rm -rf runs/$RUN_ID/patch_planner \
       runs/$RUN_ID/patch_applicator \
       runs/$RUN_ID/runner_execute \
       runs/$RUN_ID/results_analysis \
       runs/$RUN_ID/final_report

# Restore repo to clean state
cd workspace/repos/patchcore-inspection
git reset --hard HEAD && git clean -fd
```

## Acceptance Verification

```bash
export RUN_ID=run_l3_demo

# 1. Execution manifest (must be 3/0/0)
jq '{completed: .completed_unit_count, failed: .failed_unit_count, blocked: .blocked_unit_count}' \
  runs/$RUN_ID/runner_execute/execution_manifest.json

# Expected: {"completed": 3, "failed": 0, "blocked": 0}

# 2. GPU evidence
cat runs/$RUN_ID/runner_execute/gpu_execution_evidence.json

# Expected: device_name=NVIDIA GeForce RTX 4090, gpu_used=true

# 3. Final facts
jq '{
  execution_mode,
  l3_gpu_claim,
  gpu_device_name,
  scientific_claim,
  noop_patch
}' runs/$RUN_ID/final_report/final_report_facts.json

# Expected:
# execution_mode=gpu_verified
# l3_gpu_claim=completed
# gpu_device_name=NVIDIA GeForce RTX 4090
# noop_patch=false
# scientific_claim=mixed_or_inconclusive
```

## Troubleshooting

### Intake Blocks with "dirty diff SHA does not match"

Cause: repo has unexpected modifications beyond the expected patch.

Fix: reset repo and re-run from patch-apply:
```bash
cd workspace/repos/patchcore-inspection
git reset --hard HEAD && git clean -fd
uv run autoad patch-apply --run-id "$RUN_ID" --json
```

### Intake Blocks with "protected file changes"

Cause: repo has modifications to protected paths (bin/, configs/, tests/).

Fix: identify and revert the protected file changes, then re-run.

### Patch-Apply Fails with Fingerprint Mismatch

Cause: repo fingerprint changed between patch-plan and patch-apply.

Fix: re-run both stages:
```bash
rm -rf runs/$RUN_ID/patch_planner runs/$RUN_ID/patch_applicator
uv run autoad patch-plan --run-id "$RUN_ID" --json
uv run autoad patch-apply --run-id "$RUN_ID" --json
```

### GPU Evidence Shows cpu_fallback

Cause: main venv CUDA incompatible with driver (torch 2.12.1+cu130 vs CUDA 12.4).

AutoAD automatically probes `.venv-gpu` (torch 2.5.1+cu124) as fallback.
Ensure `.venv-gpu/bin/python3` exists and has CUDA-capable torch installed.
