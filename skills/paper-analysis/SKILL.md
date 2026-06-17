---
name: paper-analysis
description: Analyze an attested paper using parser-backed paper tools and evidence-backed claims.
required_tools:
  - paper_list_sections
  - paper_read
  - paper_search
permission_profile: paper_analysis
max_context_tokens: 12000
deferred: true
triggers:
  - paper_parse_success
---

# Paper Analysis Skill

## Purpose

Guide the agent through systematic, evidence-backed analysis of a parsed paper. The agent reads sections incrementally, extracts structured claims, identifies candidates (baselines, datasets, metrics, repos), and produces analysis progress records.

## Preconditions

- Paper parse has completed (success or partial_success)
- Canonical paper store is accessible at `runs/<run_id>/paper/parse/`
- Paper tools are loaded and permission-checked
- Analysis budget is allocated

## Allowed Tools

- `paper_list_sections` — navigate section tree
- `paper_read` — read content by section/page/block/table/figure/reference
- `paper_search` — search within canonical parsed text
- `filesystem_read` — read analysis artifacts within paper workspace

## Forbidden Actions

- Do not call `document_parse` (already completed in parse stage)
- Do not execute code from the paper
- Do not make network calls unless explicitly allowed by web context
- Do not write to the repository
- Do not mark `paper_mentioned` candidates as `selected`

## Recommended Workflow

1. Inspect `parse_quality_report.json` to understand parse quality
2. Read title and abstract
3. Read introduction section
4. Read method section
5. Read experiment setup
6. Read main results tables
7. Read ablation and appendix when needed
8. Extract candidate baselines, datasets, metrics, and repos
9. Produce structured claims with evidence refs
10. Output an `AnalysisControlSignal` each cycle

## Evidence Requirements

- Every `confirmed` claim must have at least one `PaperEvidenceRef`
- `inferred` claims must include a `rationale_summary`
- Evidence must come from `paper_text`, `paper_table`, `paper_figure`, or `paper_reference`
- Web evidence cannot be the sole support for paper body claims
- All evidence is appended to `evidence_index.jsonl`

## Output Contract

- `analysis_progress.json` — current analysis cycle and coverage
- `analysis_control_signals.jsonl` — append-only control signal per cycle
- `analysis_observations.jsonl` — append-only short evidence-backed work facts
- `evidence_index.jsonl` — append-only evidence records

## Candidate Semantics

- `compared_baseline` ≠ selected baseline
- `reported_dataset` ≠ selected dataset
- `reported_metric` ≠ selected metric
- `paper-derived idea` ≠ confirmed idea
- `repository URL in paper` ≠ verified repository source
- All candidates are marked `paper_mentioned` only

## Coverage Requirements

Before synthesis, the following must be checked:
- research_problem
- proposed_method
- core_components
- data_assumptions
- training_objective
- experiment_setup
- baseline_candidates
- dataset_candidates
- metric_candidates
- transfer_points

## Stop Conditions

- No progress for `max_no_progress_cycles` cycles → forced synthesis
- Budget exhausted → stop with partial results
- Parse quality report indicates all pages are unusable → stop
