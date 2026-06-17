---
name: repository-discovery
description: Discover and verify candidate repositories before source acquisition.
required_tools:
  - web_search
  - web_fetch
  - github_read
  - filesystem_read
  - filesystem_stat
permission_profile: repository_discovery
max_context_tokens: 10000
deferred: true
triggers:
  - source_missing
---

## Purpose

Find candidate public repositories only when the request does not already
provide an explicit repository URL or local repository path.

## Preconditions

The request has no confirmed source and discovery is allowed by policy.

## Allowed Tools

Use `web_search`, `web_fetch`, `github_read`, `filesystem_read`, and
`filesystem_stat` only within the discovery permission profile.

## Forbidden Actions

Do not run process commands, clone repositories, modify files, fetch private
resources, or treat search snippets as code-behavior evidence.

## Recommended Workflow

Check user-provided sources first. If none exists, search for official project
pages, author organization links, paper repository links, and GitHub metadata.
Keep the candidate set small and preserve ambiguity.

## Evidence Requirements

Search results are association leads. GitHub metadata can support candidate
identity. Web pages can support paper-to-repository association.

## Output Contract

Produce repository candidates and a repository resolution artifact. Use
`needs_user_confirmation` when candidates are materially ambiguous.

## Stop Conditions

Stop when one candidate is sufficiently supported, when user confirmation is
required, or when the budget is exhausted.

## Failure Handling

Return `not_found` or `blocked` with evidence-backed reasons. Do not invent a
repository.

## Examples

An explicit GitHub URL skips this Skill. A paper title without a repository URL
may use search and GitHub metadata to produce candidates.
