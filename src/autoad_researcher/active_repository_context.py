"""Shared active repository context model."""

from pathlib import Path
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field

GitCommitPattern = r"^[0-9a-f]{40}$"
IdentifierPattern = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
Sha256Pattern = r"^[0-9a-f]{64}$"


class ActiveRepositoryContext(BaseModel):
    """Runtime context injected after repository attestation."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(pattern=IdentifierPattern)
    repository_root: Path
    resolved_commit: str | None = Field(default=None, pattern=GitCommitPattern)
    tree_sha: str = Field(pattern=Sha256Pattern)


def validate_relative_path(value: str) -> str:
    """Validate a run-relative or repository-relative POSIX path."""
    if "\\" in value:
        raise ValueError(f"backslash forbidden in path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise ValueError(f"absolute path forbidden: {value!r}")
    if value in {"", "."}:
        raise ValueError("path must not be empty or '.'")
    if any(part == ".." for part in path.parts):
        raise ValueError(f"parent traversal forbidden: {value!r}")
    return value
