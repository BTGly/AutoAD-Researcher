---
name: repository-analysis
description: Analyze an attested repository with read-only tools and evidence-backed claims.
required_tools:
  - filesystem_list
  - filesystem_read
  - filesystem_search
  - filesystem_stat
  - process
permission_profile: repository_analysis
max_context_tokens: 12000
deferred: true
triggers:
  - source_attested
---

## Purpose

Understand an attested repository through static, read-only evidence.

## Preconditions

An active repository context exists with source identity evidence and a fixed
commit or local attestation.

## Allowed Tools

Use `filesystem_list`, `filesystem_read`, `filesystem_search`,
`filesystem_stat`, and read-only Git commands through argv-based `process`.

## Forbidden Actions

Do not install dependencies, import repository modules, run tests, execute
notebooks, execute scripts, modify files, or call remote GitHub metadata during
analysis.

## Recommended Workflow

Read repository metadata, dependency declarations, CLI entrypoints, scripts,
configuration files, tests, evaluation code, dataset code, and README claims.
Prefer direct file evidence over indirect documentation.

## Evidence Requirements

Every confirmed code behavior claim must cite repository file evidence with
commit, path, line range, file SHA, snippet SHA, and tool call ID.

## Output Contract

Produce evidence-backed observations and an analysis control signal. Keep
confirmed, inferred, conflicting, and unknown facts separate.

## Stop Conditions

Stop when coverage is sufficient for synthesis, when no progress repeats beyond
budget, or when a blocker requires user input or missing Tool Foundation.

## Failure Handling

Return `blocked` with unresolved blockers when mandatory tools, active
repository context, or evidence recording is unavailable.

## Examples

A README training command can identify a candidate entrypoint, but the entrypoint
is confirmed only after reading the referenced file.
