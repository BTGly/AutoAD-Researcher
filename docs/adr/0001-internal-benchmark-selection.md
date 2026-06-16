# ADR 0001: Internal Benchmark Selection

## Status

Accepted. PatchCore is locked as the first internal-only anomaly detection benchmark.
Actual execution, environment locking, weight fingerprinting, and dual-run results
belong to Step 3.0C/3.0D.

## Decision

Select `amazon-science/patchcore-inspection` as the first internal-only anomaly
detection benchmark, locked to main branch commit:

```
fcaa92f124fb1ad74a7acf56726decd4b27cbcad
```

### Selection criteria

1. Official PatchCore paper implementation.
2. Training, evaluation, and result-writing paths are clearly separated.
3. Codebase is small, suitable as a first Repository Reader fixture.
4. `results.csv` provides stable, structured evaluation output.
5. Apache-2.0 license.
6. Full commit SHA prevents main-branch drift from affecting reproducibility.

### Alternatives considered

**Anomalib v2.5.0** (`open-edge-platform/anomalib`, commit
`faedd0734af81233192e6108c8962ab61e6d2de8`)

Anomalib has modern Python support, `uv.lock`, a unified CLI, and formal releases.
However, it is a large general-purpose anomaly detection framework where execution
and output directories are co-managed by the Engine, Lightning, and the logger.
For the first minimal, auditable internal benchmark, the official PatchCore
implementation introduces fewer variables.

Anomalib is retained as a second cross-repository structure regression case.

## Risks

| Risk | Mitigation |
|---|---|
| Python 3.8-era upstream environment | Dedicated Python environment (Step 3.0C) |
| No upstream lockfile; requirements use minimum-version constraints | AutoAD-generated lockfile (Step 3.0C) |
| First load of ImageNet-pretrained backbone may require network | Pre-download weights, record SHA256 (Step 3.0C) |
| MVTec AD data is CC BY-NC-SA 4.0 (non-commercial) | Dataset never enters Git; documented in README |
| Upstream `main` may move | Full commit SHA pinned; config validated on every load |

## Consequences

- Repository Reader development can use this repo as a stable, small fixture.
- Runner and Validity Supervisor can target a known `results.csv` format.
- Internal-only benchmark never leaks into user-facing defaults.
