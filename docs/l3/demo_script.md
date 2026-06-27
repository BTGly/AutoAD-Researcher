# L3 Demo Script — AutoAD-Researcher

Target duration: 5–8 minutes.

---

## Slide 1: Problem (30s)

**Research reproducibility is expensive.**
- Each anomaly detection experiment setup: dataset config, CUDA env, code changes
- No standardised pipeline: results hard to audit, hard to reproduce, hard to compare
- Existing AutoML tools skip the scientific evidence chain

AutoAD-Researcher: a pipeline that reads papers + code, generates hypotheses, runs experiments, and produces a verifiable evidence chain — all the way from paper to final report.

---

## Slide 2: System Overview (60s)

```
Paper → Code Understanding → Variant Ideas → Patch Generation →
GPU Benchmark → Results Analysis → Scientific Report
```

Key contributions:
- **Code-level patch materialization**: reads source → generates real diffs (not stubs)
- **Evidence chain with SHA sealing**: every artifact hash-linked
- **GPU execution verification**: probes `.venv-gpu` when main env CUDA unavailable
- **Conservative scientific reporting**: says "no improvement" when no improvement observed

---

## Slide 3: Demo Setup (30s)

**Inputs:**
- Paper: PatchCore (Roth et al., WACV 2022)
- Repository: `patchcore-inspection` (commit `aa64649`)
- Dataset: MVTec AD, bottle category
- GPU: NVIDIA RTX 4090 (CUDA 12.4)
- LLM: DeepSeek V4 Flash (API)

**Demo command:**
```bash
export AUTOAD_L3_REAL_EXECUTION_ALLOWED=1
uv run autoad stage3-acceptance \
  --run-id run_l3_demo --mode l3-preflight \
  --provider-base-url "https://api.deepseek.com" --json
```

---

## Slide 4: Pipeline Run (90s)

| Stage | Status | What happens |
|---|---|---|
| 3.6 patch-plan | ✅ | LLM plans coreset size clamp change |
| 3.7 patch-apply | ✅ | 1040-byte real diff applied to repo |
| 3.8 runner-execute | ✅ | 3 GPU attempts, `gpu_verified` |
| 3.9 results-analysis | ✅ | Baseline vs variant: `practically_equivalent` |
| 3.10 final-report | ✅ | Conservative `mixed_or_inconclusive` |

Key moment: the dirty-repo intake validation checks that the git diff SHA
matches the expected patch SHA — blocking any unexpected modifications.

---

## Slide 5: Results (60s)

```json
{"completed": 3, "failed": 0, "blocked": 0}
```

```json
{
  "execution_mode": "gpu_verified",
  "gpu_device_name": "NVIDIA GeForce RTX 4090",
  "noop_patch": false,
  "scientific_claim": "mixed_or_inconclusive"
}
```

- **Real diff**: 1040 bytes, coreset size clamp injection
- **GPU**: verified via `.venv-gpu` subprocess probe
- **Scientific claim**: no improvement observed → `mixed_or_inconclusive`
- **Safety**: preflight mode defers real execution by default

---

## Slide 6: Engineering Takeaways (60s)

What makes this different from a script that runs `python train.py`:

| AutoAD-Researcher | Typical script |
|---|---|
| SHA-sealed evidence chain | No provenance tracking |
| Real code diff generation | Manual code changes |
| Dirty-repo validation | No guard |
| GPU telemetry collection | No resource tracking |
| Conservative scientific claim | Often overclaims |

P0 items resolved:
- [x] GPU evidence: `.venv-gpu` fallback
- [x] No-op patch: real code transformation
- [x] ResourceUsageReport: wall time + GPU memory
- [x] Dirty-repo validation: SHA-must-match
- [x] Unit tests: 1414 passing

---

## Slide 7: Limitations & Future Work (30s)

- **Scientific improvement not demonstrated**: coreset clamp didn't change MVTec bottle metrics (expected — single-category demo)
- **LLM dependency**: patch-planning calls external API
- **Single benchmark case**: MVTec bottle only

Future: multi-category, multi-repo, automated paper discovery.

---

## Slide 8: Conclusion (30s)

**AutoAD-Researcher achieves:**
- End-to-end pipeline from paper to scientific report
- Real code generation with evidence chain
- Conservative reporting: no improvement → says no improvement

**Demo repo:** https://github.com/BTGly/AutoAD-Researcher
**Commit:** `f60fe45`
