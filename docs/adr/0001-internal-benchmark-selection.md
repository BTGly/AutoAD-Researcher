# ADR-0001: Internal anomaly detection benchmark selection

Status: Accepted

## Context

AutoAD-Researcher needs a stable, locked internal benchmark for
developing and regression-testing Repository Reader, Runner, Metrics Parser,
and Validity Supervisor. This benchmark must not influence user-facing defaults.

## Product boundary

This benchmark is `internal_benchmark_only`. It must not be read by InputIntake,
IntentClarifier, IdeaGenerator, ExperimentPlanner, or the real-user CLI as a
default baseline, dataset, or metric configuration.

## Candidates

| Candidate | URL | License |
|---|---|---|
| A: amazon-science/patchcore-inspection | https://github.com/amazon-science/patchcore-inspection | Apache-2.0 |
| B: open-edge-platform/anomalib v2.5.0 | https://github.com/open-edge-platform/anomalib | Apache-2.0 |

## Hard gates

| Gate | A | B |
|---|---|---|
| Lockable to full commit SHA | Pass | Pass |
| License clear | Pass | Pass |
| Entrypoint locatable | Pass (bin/run_patchcore.py) | Pass (CLI) |
| Evaluation code locatable | Pass (metrics.py) | Pass |
| Data path configurable | Pass | Pass |
| Offline run possible | Pass | Pass |
| Independent environment | Pass | Pass |
| Does not require AutoAD dep changes | Pass | Pass |

## Comparison

| Factor | A | B |
|---|---|---|
| Codebase size | Small (~15 source files) | Large (framework) |
| Dependency lockfile | No (requirements.txt min versions) | Yes (uv.lock) |
| Python version | 3.8-era | Modern (3.10+) |
| Result output | results.csv (structured) | Logger/Engine managed |
| Team running experience | Needs verification | Needs verification |
| Auto-download of backbone weights | Yes (ImageNet pretrained) | Configurable |

## Decision

Select **amazon-science/patchcore-inspection** as the first internal-only
anomaly detection benchmark, locked to main branch commit:

```
fcaa92f124fb1ad74a7acf56726decd4b27cbcad
```

Rationale:

1. Official PatchCore paper implementation;
2. Clear training, evaluation, and result output paths;
3. Small codebase suitable for first Repository Reader fixture;
4. results.csv provides stable, structured evaluation output;
5. Apache-2.0 license is clear;
6. Full commit SHA prevents main branch movement from affecting reproducibility.

## Fixed case

- Baseline: PatchCore
- Implementation: amazon-science/patchcore-inspection
- Dataset: MVTec AD, category bottle
- Required metric: instance_auroc
- Seed: 0
- Backbone: wideresnet50

## Protected evaluation contract

```
bin/run_patchcore.py
src/patchcore/metrics.py
src/patchcore/utils.py
src/patchcore/datasets/mvtec.py
```

These files must not be modified by any experiment patch or code agent.

## Risks

| Risk | Mitigation (Step 3.0C) |
|---|---|
| Python 3.8-era upstream environment | Independent Python environment |
| No upstream lockfile | Generate AutoAD-owned lockfile |
| requirements.txt uses min version constraints | Lock exact versions |
| Backbone weight auto-download | Pre-download weights, record SHA256 |
| MVTec AD non-commercial license | Dataset never committed to Git |
| ~11 GB GPU memory typical | Documented; not a CI requirement |

## Rejected alternatives

**Anomalib v2.5.0** was rejected as the first benchmark because its generic
Engine, Lightning, CLI, logger, and result directory structure add more
variables to the first vertical slice than the official PatchCore repo.
Anomalib remains a strong candidate for a second cross-repo structure
regression case.

## Re-evaluation triggers

- Upstream release with breaking changes to the evaluation contract
- Discovery of a reproducibility defect in the locked commit
- Need for a second baseline requiring a different repository structure
