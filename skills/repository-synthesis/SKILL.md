---
name: repository-synthesis
description: Synthesize repository intelligence artifacts from existing evidence only.
required_tools: []
permission_profile: repository_synthesis
max_context_tokens: 12000
deferred: true
triggers:
  - analysis_synthesis_ready
---

## Purpose

Turn recorded repository evidence and observations into strict Repository
Intelligence artifacts.

## Preconditions

Analysis has produced enough evidence or a forced partial synthesis decision.

## Allowed Tools

Use existing Evidence Workspace and structured state. Do not expand repository
reading during synthesis.

## Forbidden Actions

Do not call web, GitHub, Git, filesystem, or process tools to discover new
facts. Do not claim installation, execution, or test success.

## Recommended Workflow

Group claims by artifact, preserve uncertainty, and downgrade unsupported
claims. Keep evaluation contracts as draft and path policies as proposals.

## Evidence Requirements

All critical confirmed claims must reference evidence IDs already present in the
Evidence Index.

## Output Contract

Produce `repository_summary.json`, `entrypoints.json`,
`dependency_evidence.json`, `modifiable_paths.json`,
`evaluation_contract_draft.json`, `environment_context.json`, and
`uncertainties.json`.

## Stop Conditions

Stop after all seven formal artifacts are generated or after returning a
partial result with explicit blockers.

## Failure Handling

If evidence is missing, request targeted repair or return partial success. Do
not invent evidence.

## Examples

If the evaluator path is conflicting, write it as conflicting and list the
evidence IDs on both sides.
