# Internal Benchmark Case

## Status

engineering_ready_dataset_user_provided — config, ADR, environment locking,
weight fingerprinting, controlled attempt orchestration, and offline validation
contracts are in place. Real dual-run MVTec evidence requires the user-provided
licensed dataset and is tracked separately from the 3.0 engineering seal.

## Case

| Field | Value |
|---|---|
| case_id | internal_patchcore_mvtec_bottle_v1 |
| baseline | PatchCore |
| implementation | amazon-science/patchcore-inspection |
| commit | fcaa92f124fb1ad74a7acf56726decd4b27cbcad |
| dataset | MVTec AD / bottle |
| license | Apache-2.0 (repo), CC BY-NC-SA 4.0 (dataset) |
| dataset acquisition | user_provided; no automatic download, redistribution, or committed dataset files |

## Config

```yaml
configs/benchmarks/internal_patchcore_mvtec_bottle_v1.yaml
```

## Scope

This benchmark is strictly internal-only. It must never be used as a default
for user-facing tasks. The config explicitly declares:

```yaml
scope: internal_benchmark_only
must_not_be_used_as_user_default: true
```

## Execution environment

AutoAD Core and the benchmark baseline use separate Python environments.
Benchmark dependencies (PyTorch, torchvision, CUDA) are not added to the
main `pyproject.toml`.

## Prerequisites

MVTec AD bottle data must be obtained by the user from the official MVTec page
after accepting its license terms. AutoAD records the dataset manifest once the
local dataset is present, but it must not download, vendor, or redistribute the
dataset.

```bash
export AUTOAD_INTERNAL_BENCHMARK_DATASET_ROOT=/path/to/mvtec-ad
```

## Key parameters

| Category | Value |
|---|---|
| entrypoint | `bin/run_patchcore.py` |
| backbone | wideresnet50 |
| layers | layer2, layer3 |
| resize / imagesize | 256 / 224 |
| embedding dim | 1024 |
| coreset ratio | 0.1 |
| results path | outputs |
| GPU | 0 |

## Required metrics

| Metric | Tolerance |
|---|---|
| instance_auroc | 0.005 |
| full_pixel_auroc | 0.005 |
| anomaly_pixel_auroc | 0.005 |

## Protected paths

```
bin/run_patchcore.py
src/patchcore/metrics.py
src/patchcore/utils.py
src/patchcore/datasets/mvtec.py
```

## Expected results.csv

```
outputs/autoad_internal_benchmark/internal_patchcore_mvtec_bottle_v1/results.csv
```

## Validate

```bash
uv run python scripts/benchmark/validate_case.py \
  configs/benchmarks/internal_patchcore_mvtec_bottle_v1.yaml
```

## ADR

See `docs/adr/0001-internal-benchmark-selection.md` for the full decision record.
