# L3 Rehearsal Report — `f60fe45`

Date: 2026-06-26

## Run Parameters

| Field | Value |
|---|---|
| RUN_ID | `run_l3_bottle_001` |
| Mode | `l3-preflight` with `AUTOAD_L3_REAL_EXECUTION_ALLOWED=1` |
| Repo | `patchcore-inspection` @ `aa64649` |
| Dataset | MVTec AD bottle |
| GPU | NVIDIA RTX 4090 (CUDA 12.4) |
| Provider | DeepSeek V4 Flash (api.deepseek.com) |

## Pipeline Results

| Stage | Status | Artifact SHA |
|---|---|---|
| patch-plan | passed | `9eb3af84` |
| patch-apply | passed | `7b68e306` |
| runner-execute | passed | `f78cb706` |
| results-analysis | passed | `625dbae2` |
| final-report | passed | `bd6e952c` |

## Acceptance Criteria

### Execution Manifest
```json
{"completed": 3, "failed": 0, "blocked": 0}
```

### GPU Evidence
```json
{
  "device_name": "NVIDIA GeForce RTX 4090",
  "gpu_used": true,
  "source": "runner_execute_via_venv_gpu",
  "torch_cuda_available": true,
  "torch_cuda_version": "12.4"
}
```

### Final Facts
```json
{
  "execution_mode": "gpu_verified",
  "l3_gpu_claim": "completed",
  "gpu_device_name": "NVIDIA GeForce RTX 4090",
  "noop_patch": false,
  "scientific_claim": "mixed_or_inconclusive",
  "per_variant_conclusions": [
    {"variant_id": "idea_run_l3_bottle_001_var_A", "conclusion": "practically_equivalent"}
  ]
}
```

## Key Fixes Validated

1. **`_compute_dirty_diff_sha256` aligned with difflib** — was using `git diff` format
   which mismatched the applicator's `difflib.unified_diff` format. Fixed to use
   same format, now SHAs match the handoff manifest.
2. **`_run_intake` dirty-repo validation** — blocks unexpected diff SHA mismatch
   and protected-file changes while allowing expected patch dirtiness.
3. **GPU evidence probes `.venv-gpu`** — works around main-venv CUDA incompatibility
   (torch 2.12.1+cu130 vs driver 12.4).

## Artifact Locations

```
runs/run_l3_bottle_001/
├── patch_planner/
├── patch_applicator/
│   ├── patch_execution_result.json
│   └── patch_runner_handoff.json
├── runner_execute/
│   ├── execution_manifest.json
│   ├── gpu_execution_evidence.json
│   ├── runner_intake_report.json
│   └── experiment_execution_handoff.json
├── results_analysis/
│   ├── results_analysis_handoff.json
│   ├── reflection.json
│   └── results_analysis_report.md
└── final_report/
    ├── final_report_handoff.json
    ├── final_report.md
    ├── final_report_facts.json
    └── report_artifact_chain.json
```
