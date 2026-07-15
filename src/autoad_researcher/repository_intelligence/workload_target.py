"""Bounded resolution and reading of an explicit benchmark workload target."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RepositoryWorkloadTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: int = Field(ge=0)
    problem_id: int = Field(ge=0)


class RepositoryWorkloadAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    target: RepositoryWorkloadTarget
    status: Literal["found", "ambiguous", "not_evidenced"]
    resolved_path: str | None = None
    candidate_paths: list[str] = Field(default_factory=list)
    file_sha256: str | None = None
    content_sha256: str | None = None
    content_preview: str = ""
    bytes_read: int = Field(ge=0)
    mapped_file_count: int = Field(ge=0)
    map_truncated: bool = False
    conclusion: str


def analyze_repository_workload_target(
    *,
    repository_root: Path,
    target: RepositoryWorkloadTarget,
    output_path: Path,
    map_file_limit: int = 5000,
    read_byte_limit: int = 256 * 1024,
) -> RepositoryWorkloadAnalysis:
    """Resolve one exact level/problem pair and read its unique task file."""

    root = repository_root.resolve()
    paths, truncated = _bounded_file_map(root, map_file_limit)
    level_part = f"level{target.level}"
    problem_prefix = f"{target.problem_id}_"
    candidates = [
        path
        for path in paths
        if level_part in PurePosixPath(path).parts
        and PurePosixPath(path).name.startswith(problem_prefix)
    ]

    if len(candidates) != 1:
        status: Literal["ambiguous", "not_evidenced"] = (
            "ambiguous" if candidates else "not_evidenced"
        )
        analysis = RepositoryWorkloadAnalysis(
            target=target,
            status=status,
            candidate_paths=candidates,
            bytes_read=0,
            mapped_file_count=len(paths),
            map_truncated=truncated,
            conclusion=(
                "Multiple exact level/problem task files were found; no file was selected."
                if candidates
                else "No exact level/problem task file was evidenced by the bounded repository map."
            ),
        )
        _write_json_atomic(output_path, analysis)
        return analysis

    relative_path = candidates[0]
    path = root / relative_path
    raw = path.read_bytes()[:read_byte_limit]
    text = raw.decode("utf-8", errors="replace")
    analysis = RepositoryWorkloadAnalysis(
        target=target,
        status="found",
        resolved_path=relative_path,
        candidate_paths=candidates,
        file_sha256=_sha256_file(path),
        content_sha256=hashlib.sha256(raw).hexdigest(),
        content_preview=text[:4000],
        bytes_read=len(raw),
        mapped_file_count=len(paths),
        map_truncated=truncated,
        conclusion="The unique exact level/problem task file was read from the acquired repository.",
    )
    _write_json_atomic(output_path, analysis)
    return analysis


def _bounded_file_map(root: Path, limit: int) -> tuple[list[str], bool]:
    paths: list[str] = []
    truncated = False
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name != ".git" and not (directory_path / name).is_symlink()
        )
        for filename in sorted(filenames):
            path = directory_path / filename
            if path.is_symlink() or not path.is_file():
                continue
            if len(paths) >= limit:
                truncated = True
                continue
            paths.append(path.relative_to(root).as_posix())
    return paths, truncated


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_atomic(path: Path, value: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    data = json.dumps(
        value.model_dump(mode="json", exclude_none=True),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    try:
        with tmp.open("wb") as handle:
            handle.write(data.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
