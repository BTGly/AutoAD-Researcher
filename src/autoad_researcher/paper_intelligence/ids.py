"""Identifiers and validators for Paper Intelligence contracts."""

from pathlib import PurePosixPath

IdentifierPattern = r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$"
Sha256Pattern = r"^[0-9a-f]{64}$"


def validate_workspace_path(value: str) -> str:
    """Validate a workspace-relative POSIX path (no traversal, no backslash)."""
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
