"""NarrowRepositoryRead — read-only agent for pre-applying context gathering.

Used by PatchMaterializer to capture filesystem state before generating
payloads (for before_sha256 and diff computation). Read-only by contract;
raises PermissionError on any write attempt.

Integrates with NarrowRepositoryReadRequest for allowed_paths, max_files,
and max_bytes enforcement.
"""

import os
from pathlib import Path
from typing import Optional

from autoad_researcher.schemas.patch_planning import NarrowRepositoryReadRequest


class NarrowRepositoryReader:
    """Read-only repository reader constrained by a NarrowRepositoryReadRequest.

    Enforces:
      - allowed_paths: only read files under these relative path prefixes.
      - max_files: limit number of files read.
      - max_bytes: limit total bytes read.
      - Path traversal prevention (via _resolve_safe).
    """

    def __init__(self, request: NarrowRepositoryReadRequest, root: Path) -> None:
        self._request = request
        self._root = root.resolve()
        self._allowed_prefixes = [p.rstrip("/") + "/" if not p.endswith("/") else p
                                   for p in request.allowed_paths]
        self._total_bytes = 0
        self._total_files = 0

    def _path_allowed(self, relative_path: str) -> bool:
        """Check if relative_path is under one of the allowed_paths prefixes."""
        if not self._allowed_prefixes:
            return True
        for prefix in self._allowed_prefixes:
            if relative_path == prefix.rstrip("/") or relative_path.startswith(prefix):
                return True
        return False

    def _check_limits(self, additional_bytes: int) -> None:
        if self._total_files >= self._request.max_files:
            raise PermissionError(
                f"max_files limit ({self._request.max_files}) reached"
            )
        if self._total_bytes + additional_bytes > self._request.max_bytes:
            raise PermissionError(
                f"max_bytes limit ({self._request.max_bytes}) exceeded: "
                f"{self._total_bytes} + {additional_bytes} > {self._request.max_bytes}"
            )

    def _resolve_safe(self, relative_path: str) -> Path:
        """Resolve relative_path under root with traversal protection."""
        if ".." in relative_path.split("/") or ".." in relative_path.split("\\"):
            raise PermissionError(f"path traversal denied: {relative_path}")
        resolved = (self._root / relative_path).resolve()
        try:
            if not resolved.is_relative_to(self._root):
                raise PermissionError(
                    f"resolved path {resolved} is outside root {self._root}"
                )
        except AttributeError:
            if not str(resolved).startswith(str(self._root) + os.sep) and resolved != self._root:
                raise PermissionError(
                    f"resolved path {resolved} is outside root {self._root}"
                )
        return resolved

    def read_file(self, relative_path: str) -> bytes:
        """Read a single file, enforcing allowed_paths and limits."""
        if not self._path_allowed(relative_path):
            raise PermissionError(
                f"path {relative_path} not in allowed_paths: {self._request.allowed_paths}"
            )
        resolved = self._resolve_safe(relative_path)
        data = resolved.read_bytes()
        self._check_limits(len(data))
        self._total_files += 1
        self._total_bytes += len(data)
        return data

    def read_source_files(self, extensions: Optional[set[str]] = None) -> list[tuple[str, bytes]]:
        """Read all source files under allowed paths matching extensions.

        Respects max_files and max_bytes limits.
        Returns list of (relative_path, content) tuples.
        """
        if extensions is None:
            extensions = {".py"}
        result: list[tuple[str, bytes]] = []
        for p in sorted(self._root.rglob("*")):
            if not (p.is_file() and not p.is_symlink() and p.suffix in extensions):
                continue
            rel = str(p.relative_to(self._root))
            if not self._path_allowed(rel):
                continue
            try:
                data = p.read_bytes()
            except PermissionError:
                continue
            try:
                self._check_limits(len(data))
            except PermissionError:
                break
            self._total_files += 1
            self._total_bytes += len(data)
            result.append((rel, data))
        return result

    def list_files(self, glob_pattern: str = "**/*") -> list[str]:
        """List files matching pattern within allowed_paths, respecting max_files."""
        resolved_root = self._root
        files: list[str] = []
        for p in sorted(resolved_root.glob(glob_pattern)):
            if not (p.is_file() and not p.is_symlink()):
                continue
            rel = str(p.relative_to(resolved_root))
            if not self._path_allowed(rel):
                continue
            if self._total_files >= self._request.max_files:
                break
            self._total_files += 1
            files.append(rel)
        return files


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
