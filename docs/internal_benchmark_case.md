# Internal Benchmark Case: PatchCore / MVTec AD / bottle

> **Status: locked_not_executed**

## Scope

```yaml
scope: internal_benchmark_only
must_not_be_used_as_user_default: true
```

This case is for internal AutoAD development only. It must not be used as a
default baseline, dataset, or metric configuration for real user tasks.

## Case ID

```
internal_patchcore_mvtec_bottle_v1
```

## Implementation

| Field | Value |
|---|---|
| Baseline | PatchCore |
| Implementation | amazon-science/patchcore-inspection |
| Repo URL | https://github.com/amazon-science/patchcore-inspection |
| Ref | main |
| Commit SHA | fcaa92f124fb1ad74a7acf56726decd4b27cbcad |
| License | Apache-2.0 |
| Entrypoint | bin/run_patchcore.py |

## Dataset

| Field | Value |
|---|---|
| Name | MVTec AD |
| Category | bottle |
| License | CC BY-NC-SA 4.0 (non-commercial) |
| Root env var | AUTOAD_INTERNAL_BENCHMARK_DATASET_ROOT |
| Required paths | bottle/train/good, bottle/test, bottle/ground_truth |

Place data at:

```text
${AUTOAD_INTERNAL_BENCHMARK_DATASET_ROOT}/bottle/
```

## Fixed Parameters

```yaml
seed: 0
backbone: wideresnet50
layers: [layer2, layer3]
resize: 256
imagesize: 224
pretrain_embed_dimension: 1024
target_embed_dimension: 1024
coreset_sampling_ratio: 0.1
sampler: approx_greedy_coreset
```

## Metrics

| Name | Required | Direction | Tolerance |
|---|---|---|---|
| instance_auroc | yes | maximize | 0.005 |
| full_pixel_auroc | no | maximize | 0.005 |
| anomaly_pixel_auroc | no | maximize | 0.005 |

## Evaluation Contract (Protected)

```
bin/run_patchcore.py
src/patchcore/metrics.py
src/patchcore/utils.py
src/patchcore/datasets/mvtec.py
```

These files must not be modified by experiment patches or code agents.

## Expected Result Output

```
outputs/autoad_internal_benchmark/internal_patchcore_mvtec_bottle_v1/results.csv
```

## Reproducibility

- Attempts: 2
- Same commit required: yes
- Same config required: yes
- Same dataset manifest required: yes
- Same evaluation contract required: yes
- Same environment required: yes

## Current State

**locked_not_executed** — configuration validated, schema passes, but no
real experiment has been executed. Real running, environment lock, weight
fingerprinting, and dual-run reproduction belong to Step 3.0C/3.0D.

## Next Steps

```bash
# Step 3.0C — when ready
uv run python scripts/benchmark/validate_case.py \
  configs/benchmarks/internal_patchcore_mvtec_bottle_v1.yaml
```
