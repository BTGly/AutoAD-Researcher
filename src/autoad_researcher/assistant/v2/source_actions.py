"""Deterministic source actions for explicit user-provided material."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from autoad_researcher.repository_intelligence.discovery import parse_github_repository_url
from autoad_researcher.source_normalizer import (
    extract_source_candidates,
    extract_first_url,
    normalize_repository_reference,
)


SourceActionType = Literal[
    "answer_only",
    "register_webpage",
    "register_github_repo",
]


class SourceAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: SourceActionType
    target: str = ""
    source_url: str | None = None
    source_kind: Literal["webpage", "github_repo", "paper_pdf"] | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    requires_confirmation: bool = False
    rationale: str = ""

    @field_validator("source_url")
    @classmethod
    def _clean_source_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = extract_first_url(value) or value.strip()
        return cleaned or None

    @model_validator(mode="after")
    def _validate_action_payload(self):
        if self.action_type in {"register_webpage", "register_github_repo"} and not self.source_url:
            raise ValueError(f"source_url is required for {self.action_type}")
        return self


class SourceActionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    actions: list[SourceAction] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""


def plan_explicit_source_actions(
    *,
    user_input: str,
    attachments: list[str] | None,
) -> SourceActionPlan | None:
    """Map every explicit conversation URL to its existing source action.

    File attachments are registered through the upload route before this
    planner runs. They must not suppress URL intake from the same conversation
    turn, and this function never creates an attachment-specific action.
    """

    del attachments
    candidates = extract_source_candidates(user_input.strip())
    if not candidates:
        return None

    actions: list[SourceAction] = []
    registered_urls: set[str] = set()
    for candidate in candidates:
        url = candidate.normalized_ref
        github_locator = parse_github_repository_url(url, strict=False)
        explicit_repo = (
            candidate.source_kind == "github_repo" or github_locator is not None
        )
        if github_locator is not None:
            url = github_locator.canonical_url
        if explicit_repo:
            repository = normalize_repository_reference(url)
            if repository is not None:
                url = repository.normalized_ref
        if url in registered_urls:
            continue
        registered_urls.add(url)
        action_type: SourceActionType = (
            "register_github_repo" if explicit_repo else "register_webpage"
        )
        source_kind: Literal["webpage", "github_repo", "paper_pdf"] = (
            "github_repo" if explicit_repo else "webpage"
        )
        actions.append(
            SourceAction(
                action_type=action_type,
                target=url,
                source_url=url,
                source_kind=source_kind,
                confidence=1.0,
                rationale="Explicit URL supplied by user.",
            )
        )

    if not actions:
        return None
    return SourceActionPlan(
        actions=actions,
        confidence=1.0,
        reason="Explicit URL signal(s).",
    )
