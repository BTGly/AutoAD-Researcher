# L3 Comparison Report — run_l3_bottle_001

**Generated:** 2026-06-26
**Commit:** f60fe45

## Experiment

| Field | Value |
|---|---|
| Variant | coreset size clamp (patch-core-20260625-var-a) |
| Dataset | MVTec AD — bottle |
| Metric | instance_auroc (maximize) |
| GPU | NVIDIA RTX 4090 (CUDA 12.4) |

## Metric Comparison

| Seed | Baseline | Variant | Raw Δ | Improvement Δ | Relative Δ |
|---|---|---|---|---|---|
| 0 | 1.0000 | 1.0000 | 0.0000 | 0.0000 | 0.00% |

**No improvement observed.** The coreset size clamp did not affect the
instance-level AUROC on the bottle category (already at ceiling).

## Resource Comparison

| Resource | Baseline | Variant | Δ |
|---|---|---|---|
| Wall time | 16.3 s | 31.0 s | +14.8 s |
| Peak GPU memory | 12 MB | 12 MB | 0 MB |
| GPU hours | 0.0045 | 0.0086 | +0.0041 |

The variant required approximately 2× the wall time (smoke test + full run),
but GPU memory remained flat.

## Scientific Conclusion

- **Claim:** mixed_or_inconclusive
- **Per variant:** idea_run_l3_bottle_001_var_A → practically_equivalent
- **No-op patch:** false (1040-byte real diff applied)
- **GPU execution:** gpu_verified

## Artifact Chain

```
patch_planner (9eb3af84)
  └─→ patch_applicator (7b68e306)
        └─→ runner_execute (f78cb706)
              └─→ results_analysis (625dbae2)
                    └─→ final_report (bd6e952c)
```
