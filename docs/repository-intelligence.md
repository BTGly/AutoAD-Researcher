# Repository Intelligence

Repository Intelligence is Step 3.1 of AutoAD-Researcher. Its job is to turn a
candidate research repository into evidence-backed, structured repository
facts that downstream EnvironmentPlan and Intent Clarifier stages can consume.

It is agent-native in structure, but the current implementation keeps CI
deterministic: tests use local Git fixtures, recorded/fake providers, scripted
analysis behavior, and no real LLM calls.

## Scope

Repository Intelligence can start from:

- an explicit repository URL
- a local repository path
- paper or method signals for discovery, when provider fixtures are supplied

It produces:

- repository source identity and state attestation
- file and repository identity evidence refs
- seven formal repository artifacts
- deterministic validation report
- environment-planning handoff candidate
- clarification question candidates

It does not:

- execute repository code
- install dependencies
- modify the target repository
- infer final environment decisions
- treat WebSearch snippets or project pages as code facts
- rely on live WebSearch, live GitHub, real LLM, GPU, or external datasets in CI

## Stage Flow

```text
request
→ discovery / resolution
→ acquisition / attestation
→ read-only analysis
→ artifact synthesis
→ evidence validation
→ bounded repair when applicable
→ EnvironmentPlan handoff
→ Intent Clarifier handoff
→ CLI summary / resume
```

The current CLI supports the offline local-path flow:

```bash
uv run autoad repository-intelligence \
  --run-id run_repo \
  --runs-root runs \
  --local-path /path/to/repository \
  --json
```

Resume is explicit:

```bash
uv run autoad repository-intelligence \
  --run-id run_repo \
  --runs-root runs \
  --local-path /path/to/repository \
  --resume \
  --json
```

Existing run directories are not overwritten unless `--resume` matches the
stored request fingerprint.

## Formal Artifacts

Each successful run writes these formal artifacts:

| Artifact | Purpose |
|---|---|
| `repository_summary.json` | Evidence-backed repository purpose and limits |
| `entrypoints.json` | Candidate train/inference/evaluation entrypoints |
| `dependency_evidence.json` | Dependency declarations and environment signals |
| `modifiable_paths.json` | Proposed path policy; not authorization to edit |
| `evaluation_contract_draft.json` | Draft evaluator and metric evidence |
| `environment_context.json` | Environment candidate context; no final decision |
| `uncertainties.json` | Unknown/conflicting facts for clarification |

Additional handoff and audit files include:

| File | Purpose |
|---|---|
| `repository_source.json` | Acquired/local source identity |
| `repository_attestation.json` | Git/tree/dirty/remote attestation |
| `evidence_index.jsonl` | Append-only evidence refs |
| `analysis_progress.json` | Current coverage and budget state |
| `analysis_observations.jsonl` | Brief evidence-backed observations |
| `analysis_control_signals.jsonl` | Agent transition requests |
| `analysis_transition_decisions.jsonl` | Harness transition decisions |
| `evidence_validation.json` | Deterministic validator output |
| `environment_plan_candidate.json` | Candidate EnvironmentPlan handoff |
| `clarification_question_candidates.json` | Clarifier handoff |
| `repository_intelligence_result.json` | CLI summary and resume target |

## Evidence Rules

Confirmed artifact claims must reference evidence IDs present in
`evidence_index.jsonl`.

File evidence records include:

- source ID
- repository commit when available
- repository-relative path
- file SHA256
- line range
- snippet SHA256
- tool call ID

Repository identity evidence records bind source metadata to attestation SHA.
Web evidence is association-only unless later resolved into repository/file
evidence. Credential-bearing URLs, `.env` paths, private key paths, parent
traversal, and symlink escapes are rejected by deterministic guards.

## Tool And Permission Boundary

Repository Intelligence uses mature tools behind explicit policy:

- `filesystem_*` tools are workspace-scoped and require active repository
  context during analysis.
- `process` is argv-based and always runs with `shell=False`.
- Repository acquisition and analysis profiles additionally restrict process
  argv to stage-specific Git allowlists.
- `web_search`, `web_fetch`, and `github_read` are deferred and loaded only for
  allowed stages.

Acquisition Git commands and analysis Git commands are intentionally different.
Analysis cannot clone, fetch, checkout, configure Git, update submodules, pull
LFS, push, commit, tag, or run shell scripts.

## Validation And Repair

The validator is deterministic. It checks evidence existence, file hashes,
line ranges, snippet hashes, repository identity consistency, safe WebEvidence
URLs, and artifact claim evidence coverage.

Repair is bounded by explicit repair and global budgets. It may downgrade
unsupported artifact claims to `unknown`, but it does not invent evidence and
does not modify repository source files.

## CI Integration Fixtures

CI validates the flow with local temporary Git repositories that mimic public
research repositories:

- PatchCore-like anomaly detection repository
- FastFlow-like anomaly detection repository
- non-anomaly image classification repository

These fixtures exercise local acquisition, attestation, analysis, artifact
synthesis, validation, environment handoff, clarification handoff, and resume
behavior without network access.

## Current Boundary

The repository contains provider-injected discovery/resolution and controlled
acquisition primitives, but the sealed CI path remains offline and fixture
based. Real WebSearch, real GitHub discovery, public clone integration, and
real LLM analysis should be run only in explicit manual integration workflows
with user-approved credentials, budget, and audit retention.
