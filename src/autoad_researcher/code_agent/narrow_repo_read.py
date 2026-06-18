"""NarrowRepositoryRead — read-only agent for pre-applying context gathering.

Used by PatchMaterializer to capture filesystem state before generating
payloads (for before_sha256 and diff computation). Read-only by contract;
raises PermissionError on any write attempt.
"""

import os
from pathlib import Path
from typing import Optional


def read_file_safe(root: Path, relative_path: str) -> bytes:
    """Read a file relative to root, preventing path traversal.

    Raises:
        FileNotFoundError: file does not exist.
        PermissionError: relative_path attempts directory traversal (contains '..').
    """
    resolved = (root / relative_path).resolve()
    actual_root = root.resolve()

    if ".." in relative_path.split("/") or ".." in relative_path.split("\\"):
        raise PermissionError(
            f"path traversal denied: {relative_path}"
        )
    try:
        if not resolved.is_relative_to(actual_root):
            raise PermissionError(
                f"resolved path {resolved} is outside root {actual_root}"
            )
    except AttributeError:
        if not str(resolved).startswith(str(actual_root) + os.sep) and resolved != actual_root:
            raise PermissionError(
                f"resolved path {resolved} is outside root {actual_root}"
            )

    return resolved.read_bytes()


def list_files(root: Path, glob_pattern: str = "**/*") -> list[str]:
    """List files matching a pattern, returning relative paths.

    Only regular files (not dirs/symlinks) are included.
    """
    resolved_root = root.resolve()
    files: list[str] = []
    for p in sorted(resolved_root.glob(glob_pattern)):
        if p.is_file() and not p.is_symlink():
            files.append(str(p.relative_to(resolved_root)))
    return files


def iter_source_files(
    root: Path,
    extensions: Optional[set[str]] = None,
) -> list[tuple[str, bytes]]:
    """Read all source files matching given extensions.

    Args:
        root: Repository root directory.
        extensions: Set of extensions to include (e.g. {".py", ".rs", ".ts"}).
                    Defaults to {".py"}.

    Returns:
        List of (relative_path, content_bytes) tuples.
    """
    if extensions is None:
        extensions = {".py"}

    resolved = root.resolve()
    result: list[tuple[str, bytes]] = []
    for p in sorted(resolved.rglob("*")):
        if p.is_file() and not p.is_symlink() and p.suffix in extensions:
            rel = str(p.relative_to(resolved))
            result.append((rel, p.read_bytes()))
    return result
