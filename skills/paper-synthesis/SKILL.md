---
name: paper-synthesis
description: Synthesize analysis observations into structured paper artifacts with evidence-backed claims.
required_tools:
  - paper_read
  - paper_search
permission_profile: paper_synthesis
max_context_tokens: 15000
deferred: true
triggers:
  - paper_analysis_complete
---

# Paper Synthesis Skill

## Purpose

Convert analysis observations and evidence into seven structured paper artifacts. Combine paper evidence, claims, and candidates into machine-readable JSON that downstream stages can consume.

## Preconditions

- Paper analysis has produced sufficient evidence coverage
- Analysis control signal is `synthesis_ready`
- Evidence index contains all referenced evidence

## Allowed Tools

- `paper_read` — for targeted re-reading to confirm claims
- `paper_search` — for targeted search to fill evidence gaps
- Read access to paper artifacts workspace

## Forbidden Actions

- Do not call `document_parse`
- Do not add new claims without evidence
- Do not change `paper_mentioned` to `selected`
- Do not generate a confirmed Idea
- Do not generate transfer feasibility conclusions
- Do not generate an ExperimentPlan
- Do not turn Reader evidence gaps into user questions
- Do not make network calls

## Recommended Workflow

1. Review all analysis observations and evidence index
2. Construct `paper_summary.json` from confirmed claims
3. Decompose method into `method_components.json`
4. Extract candidates into `paper_candidates.json`
5. Record uncertainties into `paper_uncertainties.json`
6. Extract idea source candidates into `paper_idea_sources.json`
7. Record repository link candidates
8. Write `paper_reader_result.json`

## Evidence Requirements

- Every confirmed claim references evidence_ids from the evidence index
- Inferred claims have rationale_summary
- Conflicting claims have at least two evidence_ids
- Unknown items are recorded, not fabricated

## Output Contract

Artifacts written to `runs/<run_id>/paper/artifacts/`:
- `paper_summary.json` — structured paper understanding
- `method_components.json` — decomposed method components
- `paper_candidates.json` — baseline/dataset/metric/repo/asset candidates
- `paper_uncertainties.json` — known unknowns
- `paper_idea_sources.json` — paper-derived idea candidates
- `repository_link_candidates.json` — repository URLs found in paper
- `paper_reader_result.json` — final capability result

## Synthesis Forbidden Items

- paper_mentioned candidate → selected parameter
- user assertion → overwrite of paper fact
- inferred → confirmed without additional evidence
- Reader evidence gap → user question
