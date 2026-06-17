"""Evidence reference contracts for Repository Intelligence."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from autoad_researcher.repository_intelligence.ids import (
    GitCommitPattern,
    IdentifierPattern,
    Sha256Pattern,
    validate_relative_path,
)


class FileEvidenceRef(BaseModel):
    """Evidence anchored to lines in an attested repository file."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_kind: Literal["repository_file"]
    evidence_id: str = Field(pattern=IdentifierPattern)
    source_id: str = Field(pattern=IdentifierPattern)
    repository_commit: str | None = Field(default=None, pattern=GitCommitPattern)
    path: str
    file_sha256: str = Field(pattern=Sha256Pattern)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    snippet_sha256: str = Field(pattern=Sha256Pattern)
    tool_call_id: str = Field(pattern=IdentifierPattern)
    trust_level: Literal["code_fact"]

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return validate_relative_path(value)


class RepositoryIdentityEvidenceRef(BaseModel):
    """Evidence describing the fixed repository identity and tree state."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_kind: Literal["repository_identity"]
    evidence_id: str = Field(pattern=IdentifierPattern)
    source_id: str = Field(pattern=IdentifierPattern)
    canonical_remote_url: str | None = None
    resolved_commit: str | None = Field(default=None, pattern=GitCommitPattern)
    tree_sha: str = Field(pattern=Sha256Pattern)
    detached_head: bool | None
    dirty: bool
    attestation_sha256: str = Field(pattern=Sha256Pattern)
    tool_call_ids: list[str] = Field(min_length=1)
    trust_level: Literal["repository_identity"]


class WebEvidenceRef(BaseModel):
    """Evidence from web, search, or GitHub metadata tools."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_kind: Literal["web_page", "search_result", "github_metadata"]
    evidence_id: str = Field(pattern=IdentifierPattern)
    url: str = Field(min_length=1)
    content_sha256: str = Field(pattern=Sha256Pattern)
    tool_call_id: str = Field(pattern=IdentifierPattern)
    trust_level: Literal["association_lead", "repository_identity"]


class UserInputEvidenceRef(BaseModel):
    """Evidence that records user-provided assertions or preferences."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    source_kind: Literal["user_input"]
    evidence_id: str = Field(pattern=IdentifierPattern)
    input_sha256: str = Field(pattern=Sha256Pattern)
    trust_level: Literal["user_assertion"]


EvidenceRef = Annotated[
    FileEvidenceRef | RepositoryIdentityEvidenceRef | WebEvidenceRef | UserInputEvidenceRef,
    Field(discriminator="source_kind"),
]


class EvidenceIndexRecord(BaseModel):
    """One append-only Evidence Index record."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    evidence: EvidenceRef
