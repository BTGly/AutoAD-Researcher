"""Normalize user-provided source references.

LLMs decide intent; this module handles concrete external identifiers.  It
does not repair misspelled repository names.  A syntactically valid but wrong
repository URL must fail later during git validation/acquisition.
"""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field


SourceRefKind = Literal["github_repo", "webpage"]
ValidationStatus = Literal["syntactically_valid", "invalid"]

# Match the ASCII URI grammar so adjacent prose in another script is not
# absorbed into the source reference. Unicode URL text should be percent-encoded.
_RAW_URL_RE = re.compile(
    r"https?://[A-Za-z0-9][A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]*",
    re.IGNORECASE,
)
_REPO_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_.-]+")


class SourceCandidate(BaseModel):
    """A concrete source reference extracted from user text."""

    model_config = ConfigDict(extra="forbid")

    raw_ref: str
    normalized_ref: str
    source_kind: SourceRefKind
    validation_status: ValidationStatus = "syntactically_valid"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    provider: str | None = None
    owner: str | None = None
    repo: str | None = None
    warnings: list[str] = Field(default_factory=list)


def extract_source_candidates(text: str) -> list[SourceCandidate]:
    """Extract source candidates from free-form user text."""

    candidates: list[SourceCandidate] = []
    for match in _RAW_URL_RE.finditer(str(text or "")):
        candidate = normalize_source_reference(match.group(0))
        if candidate is not None:
            candidates.append(candidate)
    return candidates


def extract_first_source_candidate(text: str) -> SourceCandidate | None:
    candidates = extract_source_candidates(text)
    return candidates[0] if candidates else None


def extract_first_url(text: str) -> str | None:
    candidate = extract_first_source_candidate(text)
    return candidate.normalized_ref if candidate is not None else None


def normalize_source_reference(raw_ref: str) -> SourceCandidate | None:
    """Normalize one explicit source reference without semantic correction."""

    raw = str(raw_ref or "").strip()
    if not raw:
        return None
    parsed = urlsplit(_strip_wrapping_delimiters(raw))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None

    explicit_repo = _has_explicit_git_suffix(parsed)
    if explicit_repo:
        repo = _normalize_repository_path(parsed)
        if repo is not None:
            return repo.model_copy(update={"raw_ref": raw})

    normalized = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, parsed.fragment))
    return SourceCandidate(
        raw_ref=raw,
        normalized_ref=normalized,
        source_kind="webpage",
        provider=parsed.hostname.lower() if parsed.hostname else None,
    )


def is_repository_url(url: str) -> bool:
    """Return True only for explicit git-remote-shaped URLs.

    Host allowlists do not scale.  A generic HTTP(S) URL becomes a repository
    only when it is explicitly git-shaped (`.git`) or when the LLM/user intent
    routes it through a repository action.
    """

    parsed = urlsplit(_strip_wrapping_delimiters(str(url or "")))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    return _has_explicit_git_suffix(parsed)


def normalize_repository_reference(raw_ref: str) -> SourceCandidate | None:
    """Normalize a URL that has already been routed as a repository candidate."""

    raw = str(raw_ref or "").strip()
    if not raw:
        return None
    parsed = urlsplit(_strip_wrapping_delimiters(raw))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    repo = _normalize_repository_path(parsed)
    if repo is not None:
        return repo.model_copy(update={"raw_ref": raw})
    candidate = normalize_source_reference(raw)
    if candidate is None:
        return None
    return candidate.model_copy(update={"source_kind": "github_repo"})


def source_kind_for_url(url: str) -> SourceRefKind:
    candidate = normalize_source_reference(url)
    if candidate is not None:
        return candidate.source_kind
    return "webpage"


def _normalize_repository_path(parsed) -> SourceCandidate | None:
    hostname = (parsed.hostname or "").lower()
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 2:
        return None

    owner = _repo_segment_prefix(path_parts[0])
    repo = _repo_segment_prefix(path_parts[1])
    warnings: list[str] = []
    if owner != path_parts[0] or repo != path_parts[1] or len(path_parts) > 2:
        warnings.append("ignored_trailing_non_repo_path")
    if not owner or not repo:
        return None
    if repo.endswith(".git"):
        repo = repo[:-4]

    scheme = parsed.scheme or "https"
    normalized = f"{scheme}://{parsed.netloc.lower()}/{owner}/{repo}"
    return SourceCandidate(
        raw_ref=urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, parsed.fragment)),
        normalized_ref=normalized,
        source_kind="github_repo",
        provider=hostname,
        owner=owner,
        repo=repo,
        warnings=warnings,
    )


def _has_explicit_git_suffix(parsed) -> bool:
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < 2:
        return False
    return _repo_segment_prefix(path_parts[1]).lower().endswith(".git")


def _repo_segment_prefix(value: str) -> str:
    match = _REPO_SEGMENT_RE.match(value)
    return match.group(0) if match else ""


def _strip_wrapping_delimiters(value: str) -> str:
    return value.strip().strip("<>()[]{}\"'")
