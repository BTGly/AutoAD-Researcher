---
name: paper-parse
description: Parse an attested paper PDF using a locked MinerU profile and produce structured canonical output.
required_tools:
  - document_parse
permission_profile: paper_parse
max_context_tokens: 6000
deferred: true
triggers:
  - paper_source_attested
---

# Paper Parse Skill

## Purpose

Parse a user-provided paper PDF with a **version-locked, profile-locked MinerU parser** and produce canonical structured output that enables evidence-backed paper analysis.

## Preconditions

- Paper source has been attested (SHA256, size, page count, MIME type verified)
- PDF is inside the workspace root
- MinerU parser profile is locked (version, backend, model revision, weight SHA)
- Sufficient disk space in `runs/<run_id>/paper/parse/`

## Allowed Tools

- `document_parse` (only the approved MinerU profile)

## Forbidden Actions

- Do not execute embedded PDF JavaScript or attachments
- Do not resolve external resources inside PDF
- Do not follow PDF links automatically
- Do not use MarkItDown as a silent fallback
- Do not produce a paper summary on parse failure

## Recommended Workflow

1. Read the source attestation to confirm PDF identity
2. Call `document_parse` with the locked MinerU profile
3. Inspect the parse quality report for empty/scanned/garbled/OCR pages
4. Verify `canonical_output.sha256` matches the parser manifest
5. Record the parser manifest for evidence reproducibility

## Evidence Requirements

- Parser manifest must be written to `parse/parser_manifest.json`
- Parse quality report must be written to `parse/parse_quality_report.json`
- Canonical output SHA must be written to `parse/canonical_output.sha256`
- All output artifacts must be atomic writes

## Output Contract

- `parse/pages.jsonl` — page-level text content
- `parse/blocks.jsonl` — block-level text content
- `parse/sections.json` — section tree with page ranges
- `parse/figures.json` — figure metadata and captions
- `parse/tables.json` — table metadata and content
- `parse/references.json` — reference entries
- `parse/parse_quality_report.json` — quality assessment
- `parse/parser_manifest.json` — locked parser identity

## Stop Conditions

- Parse status is `failed` → stop, no paper_summary generated
- Parse status is `partial_success` → only evidence-backed claims from reliable pages
- Parser profile hash mismatch → stop
- Runtime or device profile mismatch → stop

## Failure Handling

- `PARSER_FAILED` → return failed result, no summary
- `PARSER_PARTIAL_LOW_CONFIDENCE` → mark low-confidence pages
- `PARSER_TIMEOUT` → stop, preserve partial output if any
- `PARSER_OUTPUT_INVALID` → stop with structured error
