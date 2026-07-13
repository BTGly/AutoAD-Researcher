"""Run-relative path validation for control-plane artifacts."""

from __future__ import annotations

from pathlib import Path, PurePosixPath


def resolve_control_plane_path(
    run_dir: Path,
    relative_path: str,
    *,
    require_exists: bool = False,
) -> Path:
    if not isinstance(relative_path, str) or not relative_path or relative_path == ".":
        raise ValueError("control-plane path must be a non-empty run-relative string")
    if "\x00" in relative_path or "\\" in relative_path:
        raise ValueError(f"unsafe control-plane path characters: {relative_path!r}")
    pure = PurePosixPath(relative_path)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError(f"control-plane path must be run-relative: {relative_path!r}")

    root = run_dir.resolve(strict=True)
    candidate = run_dir.joinpath(*pure.parts)
    current = run_dir
    for part in pure.parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError(f"symlink forbidden in control-plane path: {relative_path!r}")
    resolved = candidate.resolve(strict=require_exists)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"control-plane path escapes run directory: {relative_path!r}") from exc
    return resolved
