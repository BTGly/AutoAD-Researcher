---
name: repository-acquisition
description: Acquire a resolved repository source at a fixed commit without executing repository code.
required_tools:
  - github_read
  - filesystem_stat
  - process
permission_profile: repository_acquisition
max_context_tokens: 10000
deferred: true
triggers:
  - repository_resolved
---

## Purpose

Acquire the selected repository source into the workspace and attest its fixed
identity before analysis.

## Preconditions

A repository resolution exists with a selected candidate or local source.

## Allowed Tools

Use `github_read`, `filesystem_stat`, and argv-based `process` under the
repository acquisition permission profile.

## Forbidden Actions

Do not execute repository code, install dependencies, fetch submodules, pull Git
LFS content, execute hooks, or use shell-string commands.

## Recommended Workflow

Choose `shallow_ref` for branch or tag refs and `partial_exact` for exact
40-character commits. Never combine shallow depth and partial clone filters in
the same acquisition profile. Checkout detached HEAD and attest remote, commit,
tree, and dirty state.

## Evidence Requirements

Record tool calls, resolved commit, tree SHA, detached state, dirty state, and
source fingerprint.

## Output Contract

Produce `repository_source.json` and repository identity evidence.

## Stop Conditions

Stop after source attestation succeeds, after policy blocks acquisition, or
after a structured acquisition failure.

## Failure Handling

Do not fall back to unsafe Git commands. Return a blocked or failed acquisition
state with the exact failing precondition.

## Examples

For `main`, use a shallow ref profile. For a fixed 40-character commit, use a
partial exact profile if supported.
