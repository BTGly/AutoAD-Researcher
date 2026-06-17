# AGENTS.md — AutoAD-Researcher Codex Memory

This file is the local project memory for Codex-style agents working in this
repository. It complements `CLAUDE.md`; when rules overlap, follow the stricter
rule.

## Mandatory Workflow

Every meaningful change must follow this exact cycle:

1. Read the relevant source, tests, config, docs, and logs before editing.
2. Make the smallest scoped change that solves the current task.
3. Update the daily log under `notes/YYYY-MM-DD.md` before verification.
4. Run `bash scripts/verify.sh`.
5. If verification passes, run `bash scripts/verify_and_push.sh "<message>"`.
6. Confirm `git status --short --branch`, latest `git log --oneline -3`, and
   GitHub Actions verify status.

Do not start the next development step until the current one is pushed.

## Logging Rules

- Use the local project date from `date +%Y-%m-%d`.
- Daily logs live in `notes/YYYY-MM-DD.md`.
- `notes/development-log.md` is only an index.
- If a new daily file is created, add it to the index in the same change.
- Each log entry must include: goal, operations, result, and leftovers.

## Verification Rules

- Always use `bash scripts/verify.sh`; do not substitute a partial local test.
- The gate currently checks project structure, schemas, core imports, CLI,
  benchmark config/preflight/environment lock, pytest, and log index integrity.
- Dev dependencies are installed through the `dev` extra; use the gate instead
  of guessing uv flags.
- GitHub Actions Node.js warnings are not failure causes unless the job itself
  fails.

## Development Boundaries

- Never guess identifiers, schema fields, config keys, paths, or JSON/YAML
  shapes. Read the exact file that defines them.
- Real user task parameters such as baseline, dataset, metrics, category,
  compute budget, and evaluation protocol must be user-provided or
  user-confirmed. Internal benchmark values are not product defaults.
- Keep internal benchmark code explicitly internal-only.
- Do not commit `.env`, API keys, raw tokens, LLM call logs, real token usage, or
  large runtime artifacts.
- Do not write token-bearing URLs into Git remotes.
- Preserve run artifact boundaries under `runs/{run_id}/`; use existing
  `ArtifactStore`, `EventStore`, and `core/run_id.py` helpers.

## Current Orientation

- Main planning document: `docs/AutoAD_真实纵向闭环开发计划.md`.
- Decision-source protocol: `docs/AutoAD_任务参数决策与来源协议.md`.
- Internal benchmark case: `docs/internal_benchmark_case.md` and
  `configs/benchmarks/internal_patchcore_mvtec_bottle_v1.yaml`.
- Before new feature work, inspect the latest entries in `notes/` and the latest
  commits on `main`.

