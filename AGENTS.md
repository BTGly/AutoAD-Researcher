# AGENTS.md — AutoAD-Researcher

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

- Daily logs live in `notes/YYYY-MM-DD.md`; `notes/development-log.md` is only an index.
- New daily files must be added to the index in the same change.
- Each entry includes: goal, operations, result, leftovers.

## Verification Rules

- Always use `bash scripts/verify.sh`; do not substitute partial tests.
- The gate checks: structure, schemas, core imports, CLI, benchmark config/preflight/
  environment lock, pytest (1931 tests), log index integrity.
- Dev dependencies via `uv sync --extra dev` (or use the gate which handles this).
- GitHub Actions Node.js warnings are not failures.

## Development Boundaries

- Never guess identifiers, schema fields, config keys, paths, or JSON/YAML shapes.
- Baseline, dataset, metrics must be user-provided or user-confirmed (not internal
  benchmark defaults).
- Do not commit `.env`, API keys, raw tokens, LLM call logs, or large runtime artifacts.
- Run artifacts live under `runs/{run_id}/`; use `ArtifactStore`/`EventStore` helpers
  from `autoad_researcher.core`.

## Architecture

Two major subsystems coexist:

**V2 (primary surface)** — FastAPI + React SPA + WebSocket + embedded worker.
Entry: `ResearchOrchestratorV2.handle()` in `assistant/v2/orchestrator.py`.
- Backend: `uv run uvicorn autoad_researcher.server.main:app --host 0.0.0.0 --port 8000`
- Frontend dev: `cd frontend && npm run dev` (proxies `/api` to `127.0.0.1:8000`)
- Frontend build: `cd frontend && npm run build` → served from FastAPI's `frontend/dist/`
- V2 routes: `server/routes/{chat,runs,sources,evidence,jobs,intent_summary,artifacts,ws,experiment_config,report_route}.py`
- V2 assistant modules: `assistant/v2/{orchestrator,research_dialogue_agent,research_intent_summary,source_actions,context_builder,...}.py`

**V1 (CLI pipeline)** — deterministic planning pipeline via `autoad` CLI.
- Entry: `autoad_researcher.cli:main`  → configures `cli.py` 12 subcommands
- Subcommands: `smoke`, `repository-intelligence`, `paper-intelligence`, `research-context`,
  `transfer-design`, `experiment-plan`, `patch-plan`, `patch-apply`, `runner-execute`,
  `results-analysis`, `final-report`, `stage3-acceptance`
- Smoke: `uv run autoad smoke --run-id run_demo`

**State**: SQLite + JSONL under `runs/{run_id}/`. Streamlit is legacy/removed surface.

## Tech Stack

- **Python** ≥3.11,<3.14, package manager: `uv` only (no pip/poetry)
- **Pydantic v2** for schemas, **FastAPI** + **uvicorn** for server
- **React 19** + **TypeScript 6** + **Vite 8** + **Tailwind 4** + **oxlint** (no ESLint)
- **PyYAML**, **httpx**, **deepagents**, **MarkItDown**

## Key Commands

```bash
# Server (production mode with built frontend)
uv run uvicorn autoad_researcher.server.main:app --host 0.0.0.0 --port 8000

# Frontend dev (separate terminal, needs server running)
cd frontend && npm run dev     # http://localhost:5173

# Frontend build
cd frontend && npm run build   # output → frontend/dist/

# Tests (dev extra required)
uv run --extra dev pytest                              # all 1931 tests
uv run --extra dev pytest -k test_intent_contract      # single file

# CLI smoke
uv run autoad smoke --run-id run_demo
uv run autoad smoke --run-id run_demo --json

# Docker
bash scripts/docker-up.sh        # or docker compose -f docker/docker-compose.yml up --build
```

## Key Docs & Configs

| File | Purpose |
|------|---------|
| `docs/AutoAD_任务参数决策与来源协议.md` | Baseline/dataset/metrics must be user-confirmed |
| `docs/internal_benchmark_case.md` | Internal benchmark: PatchCore + MVTec bottle |
| `configs/benchmarks/internal_patchcore_mvtec_bottle_v1.yaml` | Benchmark config |
| `assistant/v2/research_intent_summary.py` | Compact research dialogue summary model + atomic persistence |
| `assistant/v2/intent_contract.py` | Read-only compatibility for legacy intent-contract artifacts |
| `src/autoad_researcher/schemas/` | Pydantic v2 schemas |
| `scripts/verify.sh` | Full verification gate |
| `scripts/verify_and_push.sh` | Verify → add → commit → push (reads `.env` for token) |

## Frontend Layout

```
frontend/src/
├── App.tsx              — Root: page routing, WebSocket, state
├── components/
│   ├── LeftSidebar.tsx  — 48px VS Code-style nav (Chat/Settings/Report)
│   ├── Sidebar.tsx      — Right panel (Sources/Jobs/Evidence/Draft tabs)
│   ├── SettingsPage.tsx — Experiment config form (LLM/Budget/Search)
│   ├── ReportPage.tsx   — Read-only markdown report viewer
│   ├── Messages.tsx     — User/Assistant/Welcome message components
│   └── ...              — ChatInput, ConfigModal, Toast, etc.
├── hooks/               — useConfig, useWebSocket, useAutoScroll
└── lib/                 — api.ts (REST calls), types.ts (shared types)
```

Before new feature work, inspect `notes/` latest entries and `git log --oneline -3`.
