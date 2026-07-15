"""Bounded, read-only repository structure profiling for V2 evidence."""

from __future__ import annotations

import ast
import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.repository_intelligence.ids import IdentifierPattern, validate_relative_path


_IGNORED_DIRECTORIES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "vendor",
}
_ENTRYPOINT_STEMS = {
    "app",
    "cli",
    "eval",
    "evaluate",
    "infer",
    "inference",
    "launch",
    "main",
    "predict",
    "run",
    "test",
    "train",
}
_CONFIG_FILENAMES = {
    "environment.yml",
    "environment.yaml",
    "package.json",
    "pipfile",
    "pipfile.lock",
    "poetry.lock",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "setup.cfg",
    "setup.py",
    "tox.ini",
    "uv.lock",
}


class RepositoryPathProfile(BaseModel):
    """One bounded repository-relative path observation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    path: str
    kind: Literal["file", "directory"]

    def model_post_init(self, __context: object) -> None:
        del __context
        validate_relative_path(self.path)


class RepositoryStructureProfile(BaseModel):
    """Structure facts and unresolved candidates from an attested repository tree."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    source_id: str = Field(pattern=IdentifierPattern)
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    scanned_file_count: int = Field(ge=0)
    scan_truncated: bool
    top_level_entries: list[RepositoryPathProfile] = Field(default_factory=list)
    entrypoint_candidates: list[str] = Field(default_factory=list)
    configuration_candidates: list[str] = Field(default_factory=list)
    declared_entrypoints: dict[str, str] = Field(default_factory=dict)


def build_repository_structure_profile(
    *,
    repository_root: Path,
    source_id: str,
    source_fingerprint: str,
    max_files: int = 2000,
    max_candidates: int = 100,
) -> RepositoryStructureProfile:
    """Inspect names and explicit package metadata without executing repository code."""
    if max_files < 1:
        raise ValueError("max_files must be positive")
    if max_candidates < 1:
        raise ValueError("max_candidates must be positive")
    root = repository_root.resolve()
    top_level_entries = [
        RepositoryPathProfile(
            path=path.name,
            kind="directory" if path.is_dir() else "file",
        )
        for path in sorted(root.iterdir(), key=lambda item: item.name.lower())
        if not path.is_symlink() and path.name not in _IGNORED_DIRECTORIES
    ][:max_candidates]

    files: list[str] = []
    scan_truncated = False
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in _IGNORED_DIRECTORIES
            and not (directory_path / name).is_symlink()
        )
        for filename in sorted(filenames):
            path = directory_path / filename
            if path.is_symlink():
                continue
            files.append(path.relative_to(root).as_posix())
            if len(files) >= max_files:
                scan_truncated = True
                break
        if scan_truncated:
            break

    entrypoint_candidates = sorted(
        path for path in files if _is_entrypoint_candidate(root, path)
    )[:max_candidates]
    configuration_candidates = sorted(
        path
        for path in files
        if _is_configuration_candidate(path)
    )[:max_candidates]
    declared_entrypoints = _read_declared_python_entrypoints(root / "pyproject.toml")

    return RepositoryStructureProfile(
        schema_version=1,
        source_id=source_id,
        source_fingerprint=source_fingerprint,
        scanned_file_count=len(files),
        scan_truncated=scan_truncated,
        top_level_entries=top_level_entries,
        entrypoint_candidates=entrypoint_candidates,
        configuration_candidates=configuration_candidates,
        declared_entrypoints=declared_entrypoints,
    )


def _is_configuration_candidate(relative_path: str) -> bool:
    path = Path(relative_path)
    lowered_parts = [part.lower() for part in path.parts]
    if path.name.lower() in _CONFIG_FILENAMES:
        return True
    if path.name.lower().startswith("requirements") and path.suffix.lower() == ".txt":
        return True
    return (
        any(part in {"config", "configs", "conf"} for part in lowered_parts[:-1])
        and path.suffix.lower() in {".cfg", ".ini", ".json", ".toml", ".yaml", ".yml"}
    )


def _is_entrypoint_candidate(repository_root: Path, relative_path: str) -> bool:
    path = Path(relative_path)
    suffix = path.suffix.lower()
    if suffix == ".sh":
        return True
    if suffix != ".py":
        return False
    if path.stem.lower() in _ENTRYPOINT_STEMS:
        return True
    return _declares_python_main(repository_root / path)


def _declares_python_main(path: Path, *, max_bytes: int = 524288) -> bool:
    try:
        if path.stat().st_size > max_bytes:
            return False
        module = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
    except (OSError, UnicodeDecodeError, SyntaxError):
        return False
    return any(
        isinstance(node, ast.If) and _is_python_main_guard(node.test)
        for node in module.body
    )


def _is_python_main_guard(expression: ast.expr) -> bool:
    if not isinstance(expression, ast.Compare):
        return False
    if len(expression.ops) != 1 or not isinstance(expression.ops[0], ast.Eq):
        return False
    if len(expression.comparators) != 1:
        return False
    left = expression.left
    right = expression.comparators[0]
    return (
        isinstance(left, ast.Name)
        and left.id == "__name__"
        and isinstance(right, ast.Constant)
        and right.value == "__main__"
    ) or (
        isinstance(right, ast.Name)
        and right.id == "__name__"
        and isinstance(left, ast.Constant)
        and left.value == "__main__"
    )


def _read_declared_python_entrypoints(path: Path) -> dict[str, str]:
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}
    project = payload.get("project")
    if not isinstance(project, dict):
        return {}
    scripts = project.get("scripts")
    if not isinstance(scripts, dict):
        return {}
    return {
        str(name): str(target)
        for name, target in sorted(scripts.items())
        if isinstance(name, str) and isinstance(target, str)
    }
