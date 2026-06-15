# CLAUDE.md — AutoAD-Researcher

This file provides guidance to Claude Code when working on this project.

---

## ⚠️ MANDATORY WORKFLOW — READ FIRST

### Every operation must follow this cycle:

```
make changes
  → log to notes/development-log.md (what, why, result)
  → bash scripts/verify.sh
  → if pass: bash scripts/verify_and_push.sh "descriptive commit message"
  → if fail: fix, re-verify, then push
  → NEVER proceed to next step without pushing current step
```

### Logging rules

- **File**: `notes/development-log.md`
- **Format**: Append new entries at the **bottom** under the current date heading
- **Every entry must include**: what you did, why, the result, and any leftover issues
- **Do NOT** proceed without logging — the verify.sh gate checks this file exists
- **Goal**: every version is traceable; you can rewind to any commit and know exactly what happened

### Push rules

- **GitHub**: `https://github.com/BTGly/AutoAD-Researcher.git` (main branch)
- **Token**: stored in `scripts/verify_and_push.sh` — do NOT commit raw tokens
- **Every commit must pass `verify.sh` before push**
- **Push after every meaningful change** — not batched, not deferred
- **Force push only when remote has conflicting auto-generated files** (new repo setup)

### After every push

Confirm:
1. `git log --oneline -3` shows the commit
2. GitHub Actions verify workflow passes
3. `notes/development-log.md` entry is accurate

---

## Project Overview

AutoAD-Researcher is a **semi-automated research agent for visual anomaly detection**. It closes the loop from paper ingestion → experiment execution → results analysis.

**Core loop:**
```
Paper / method idea
→ Intent clarification
→ Paper parsing (MinerU + MarkItDown)
→ Transferability judgment
→ Experiment plan generation
→ Code patch proposal
→ Human confirmation ⚠️
→ Experiment execution (smoke test)
→ Log/metric analysis
→ Comparison report + next-step suggestions
```

## Tech Stack

- **Python 3.10+** with `uv` package manager
- **Pydantic v2** for structured output / schema validation
- **SQLite + JSONL** for persistent state
- **MinerU** (primary) + **MarkItDown** (auxiliary) for paper parsing
- **Anomalib** + **MVTec AD** for anomaly detection experiments
- **YAML** for configuration
- **Gradio / Streamlit** for UI (MVP)

## Commands

```bash
# Install dependencies
uv sync

# Install with dev dependencies
uv sync --dev

# Run (once implemented)
uv run autoad-researcher

# Run tests
uv run pytest

# Run a single test file
uv run pytest tests/test_intent.py

# Type checking (when mypy/pyright added)
# uv run mypy src/
```

## Architecture

```
src/autoad_researcher/
├── main.py           # Entry point
├── intent/           # Intent Clarifier — prevents executing on incomplete specs
├── paper/            # Paper Reader — structured extraction via MinerU/MarkItDown
├── transfer/         # Transferability Judge — 6-dimension evaluation
├── experiment/       # Experiment Planner — produces structured experiment plan
├── code_agent/       # Code Agent — generates reviewable patches
├── runner/           # Runner Agent — sandboxed experiment execution
├── analysis/         # Log Analyzer + Report Generator
├── supervisor/       # Scientific Validity Supervisor — prevents false conclusions
├── gateway/          # Model Gateway — light/strong model routing + cache
├── storage/          # State persistence — SQLite + JSONL
└── pipeline/         # Orchestrator — wires modules into the full loop
```

## Key Design Constraints

1. **LLMs are components, not the system** — predictable pipelines, not black-box agents
2. **State must live on disk** (SQLite/JSONL), not in conversation context
3. **Experiments must be reproducible and traceable** — save config, patch, command, logs, metrics for every run
4. **Code changes are patches** — human reviews before applying
5. **5 confirmation gates** — task goal, experiment plan, code patch, execute command, final report

## MVP Scope

- Visual anomaly detection only
- MVTec AD, 1-2 categories (bottle, cable)
- Baseline: PatchCore / PaDiM / FastFlow
- Single-GPU smoke tests
- Human-in-the-loop at all 5 gates

## Key Documents

- [技术路线草案](docs/AutoAD_Researcher_技术路线草案.md) — Full system design (13 modules, MVP, tech stack)
- [参考资料汇总](docs/AutoAD_参考资料汇总.md) — Reference papers, repos, tools
- [configs/default.yaml](configs/default.yaml) — Default configuration
