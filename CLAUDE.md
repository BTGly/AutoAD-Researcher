# CLAUDE.md — AutoAD-Researcher

This file provides guidance to Claude Code when working on this project.

---

## ⚠️ MANDATORY WORKFLOW — READ FIRST

### Every operation must follow this cycle. NO EXCEPTIONS.

```
1. MAKE changes (code, tests, config)
2. LOG to notes/development-log.md — BEFORE verify, BEFORE commit
3. RUN bash scripts/verify.sh
4. IF pass → bash scripts/verify_and_push.sh "message"
5. IF fail → fix, back to step 2
6. NEVER start next step before current step is pushed
```

**The log is NOT optional.** If you haven't updated the log, you haven't finished the step. Every commit message should correspond to a log entry.

### Logging rules

- **File**: `notes/development-log.md`
- **When**: BEFORE running verify_and_push.sh — not after, not "I'll do it next time"
- **Format**: Append at the **bottom** under `## YYYY-MM-DD` heading. Use `### Session N:` for each step.
- **Every entry**: what, why, result, leftovers
- **Self-check before every push**: "Did I write the log entry for this change?" If no — stop and write it first.
- **Goal**: anyone can read the log and understand every decision without looking at git diff

### Push rules

- **GitHub**: `https://github.com/BTGly/AutoAD-Researcher.git` (main branch)
- **Token**: stored in local `.env` only; never commit raw tokens or write them into scripts
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
