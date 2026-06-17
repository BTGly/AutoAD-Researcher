---
name: paper-repair
description: Repair paper evidence artifacts when the validator detects missing evidence, wrong page indices, or incorrect candidate roles.
required_tools:
  - paper_read
  - paper_search
permission_profile: paper_repair
max_context_tokens: 8000
deferred: true
triggers:
  - paper_validation_failed
---

# Paper Repair Skill

## Purpose

Respond to targeted validation failures by re-reading specific paper sections, correcting evidence references, and regenerating affected artifacts. Repair is bounded and cannot expand permissions.

## Preconditions

- Paper validation has identified specific repairable issues
- Repair budget has remaining capacity (repairs_remaining > 0)
- Original analysis artifacts exist and are accessible

## Allowed Tools

- `paper_read` — re-read specific sections/blocks to confirm or correct evidence
- `paper_search` — search for missing evidence
- Read access to paper artifacts workspace

## Forbidden Actions

- Do not expand permission profiles
- Do not call `document_parse`
- Do not consume analysis budget (use repair reserve only)
- Do not modify repository files
- Do not execute code
- Do not make network calls

## Recommended Workflow

1. Read the validation report to identify repairable issues
2. For missing evidence: call `paper_read` or `paper_search` to find missing evidence
3. For wrong page indices: re-read and correct physical_page_index
4. For wrong candidate roles: re-read context and correct mention_role
5. Append new evidence to evidence_index (never overwrite)
6. Regenerate affected artifacts
7. Re-run validator

## Evidence Requirements

- New evidence is appended to evidence_index.jsonl (never overwrites existing)
- Corrected claims reference valid evidence_ids
- Repair decisions are recorded in repair_attempts.jsonl

## Stop Conditions

- `repairs_remaining` reaches 0 → stop, report partial fix
- `repair_llm_calls_remaining` reaches 0 → stop
- Global LLM cap reached → stop
- All issues fixed → transition to synthesis
- Non-repairable issues remain → stop with report

## Budget Boundaries

- Repair cannot consume analysis budget
- Repair cannot relax permission policy
- Repair tool calls are separate from analysis tool calls
