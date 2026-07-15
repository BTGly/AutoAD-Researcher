"""Bounded, exact-target repository mapping and evidence collection."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from autoad_researcher.active_repository_context import ActiveRepositoryContext
from autoad_researcher.repository_intelligence.evidence import (
    FileEvidenceRequest,
    append_evidence,
    create_file_evidence,
)
from autoad_researcher.repository_intelligence.ids import IdentifierPattern, validate_relative_path
from autoad_researcher.repository_intelligence.analysis import AnalysisObservation
from autoad_researcher.repository_intelligence.models import RepositorySource
from autoad_researcher.tools import (
    FilesystemReadRequest,
    PermissionEngine,
    ToolContext,
    default_repository_permission_engine,
    filesystem_read,
)


TargetSourceField = Literal[
    "baseline_entrypoint",
    "baseline_config",
    "user_target_module_hints",
    "job_payload",
]


class RepositoryAnalysisTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    value: str = Field(min_length=1)
    source_field: TargetSourceField


class RepositoryMapEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    size_bytes: int = Field(ge=0)

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return validate_relative_path(value)


class BoundedRepositoryMap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    source_id: str = Field(pattern=IdentifierPattern)
    repository_commit: str | None
    file_limit: int = Field(gt=0)
    files: list[RepositoryMapEntry]
    omitted_file_count: int = Field(ge=0)
    truncated: bool


class RepositoryTargetMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    match_kind: Literal["exact_path", "exact_basename", "exact_content"]
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    repository_commit: str | None
    file_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    snippet_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_id: str = Field(pattern=IdentifierPattern)

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return validate_relative_path(value)


class RepositoryTargetResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: RepositoryAnalysisTarget
    status: Literal["found", "ambiguous", "not_evidenced"]
    conclusion: str = Field(min_length=1)
    matches: list[RepositoryTargetMatch] = Field(default_factory=list)


class CompatibilityAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["supported", "unsupported_as_stated", "uncertain"]
    conclusion: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)


class TargetedRepositoryAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    source_id: str = Field(pattern=IdentifierPattern)
    repository_commit: str | None
    repository_map_path: str
    targets: list[RepositoryAnalysisTarget]
    resolutions: list[RepositoryTargetResolution]
    compatibility: CompatibilityAssessment
    unresolved_facts: list[str] = Field(default_factory=list)
    files_read: int = Field(ge=0)
    bytes_read: int = Field(ge=0)
    read_file_limit: int = Field(gt=0)
    read_byte_limit: int = Field(gt=0)

    @field_validator("repository_map_path")
    @classmethod
    def _validate_map_path(cls, value: str) -> str:
        return validate_relative_path(value)


def targets_from_contract(contract: object | None, *, job_targets: list[str] | None = None) -> list[RepositoryAnalysisTarget]:
    """Project exact target values without parsing or normalizing identifiers."""

    targets: list[RepositoryAnalysisTarget] = []
    if contract is not None:
        for field_name in ("baseline_entrypoint", "baseline_config"):
            value = getattr(contract, field_name, None)
            if isinstance(value, str) and value.strip():
                targets.append(RepositoryAnalysisTarget(value=value.strip(), source_field=field_name))
        values = getattr(contract, "user_target_module_hints", None)
        if isinstance(values, list):
            targets.extend(
                RepositoryAnalysisTarget(value=value.strip(), source_field="user_target_module_hints")
                for value in values
                if isinstance(value, str) and value.strip()
            )
    targets.extend(
        RepositoryAnalysisTarget(value=value.strip(), source_field="job_payload")
        for value in job_targets or []
        if isinstance(value, str) and value.strip()
    )
    deduped: list[RepositoryAnalysisTarget] = []
    seen: set[tuple[str, str]] = set()
    for target in targets:
        key = (target.source_field, target.value)
        if key not in seen:
            deduped.append(target)
            seen.add(key)
    return deduped


def run_targeted_repository_analysis(
    *,
    source: RepositorySource,
    repository_root: Path,
    output_dir: Path,
    targets: list[RepositoryAnalysisTarget],
    evidence_index_path: Path,
    permission_engine: PermissionEngine | None = None,
    map_file_limit: int = 2000,
    read_file_limit: int = 128,
    read_byte_limit: int = 8 * 1024 * 1024,
    per_file_byte_limit: int = 128 * 1024,
) -> TargetedRepositoryAnalysis:
    """Map and search a repository using exact, case-sensitive target values."""

    repository_root = repository_root.resolve()
    entries, omitted = _bounded_repository_map(repository_root, file_limit=map_file_limit)
    repository_map = BoundedRepositoryMap(
        schema_version=1,
        source_id=source.source_id,
        repository_commit=source.resolved_commit,
        file_limit=map_file_limit,
        files=entries,
        omitted_file_count=omitted,
        truncated=omitted > 0,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    map_path = output_dir / "repository_map.json"
    _write_json_atomic(map_path, repository_map)

    engine = permission_engine or default_repository_permission_engine()
    context = ActiveRepositoryContext(
        source_id=source.source_id,
        repository_root=repository_root,
        resolved_commit=source.resolved_commit,
        tree_sha=source.tree_sha,
    )
    resolutions: list[RepositoryTargetResolution] = []
    files_read = 0
    bytes_read = 0
    evidence_counter = 0
    read_cache: dict[str, str] = {}

    exact_path_candidates = _exact_path_candidates(entries, targets)
    remaining_paths = [entry.path for entry in entries if entry.path not in exact_path_candidates]
    candidate_paths = [*exact_path_candidates, *remaining_paths] if targets else []

    for path in candidate_paths:
        if files_read >= read_file_limit or bytes_read >= read_byte_limit:
            break
        entry = next(item for item in entries if item.path == path)
        max_bytes = min(per_file_byte_limit, read_byte_limit - bytes_read)
        if max_bytes <= 0:
            break
        result = filesystem_read(
            FilesystemReadRequest(
                tool_call_id=f"tool_target_read_{files_read + 1:03d}",
                workspace_root=repository_root,
                workspace_label=source.local_path_label,
                path=path,
                max_bytes=max_bytes,
                stage="analysis",
                permission_profile="repository_analysis",
                active_source_id=source.source_id,
                tool_context=ToolContext(active_repository=context),
            ),
            permission_engine=engine,
        )
        if result.status != "success" or result.text is None:
            continue
        files_read += 1
        bytes_read += min(entry.size_bytes, max_bytes)
        read_cache[path] = result.text

    for target_index, target in enumerate(targets, 1):
        matches: list[RepositoryTargetMatch] = []
        for path, text in read_cache.items():
            path_kind = _exact_path_match_kind(path, target.value)
            lines = text.splitlines()
            if path_kind is not None and lines:
                evidence_counter += 1
                matches.append(_record_match(
                    context=context,
                    evidence_index_path=evidence_index_path,
                    evidence_id=f"ev_target_{target_index:03d}_{evidence_counter:03d}",
                    tool_call_id=f"tool_target_match_{target_index:03d}_{evidence_counter:03d}",
                    path=path,
                    line_number=1,
                    end_line=len(lines),
                    match_kind=path_kind,
                ))
            for line_number, line in enumerate(lines, 1):
                if target.value not in line:
                    continue
                evidence_counter += 1
                matches.append(_record_match(
                    context=context,
                    evidence_index_path=evidence_index_path,
                    evidence_id=f"ev_target_{target_index:03d}_{evidence_counter:03d}",
                    tool_call_id=f"tool_target_match_{target_index:03d}_{evidence_counter:03d}",
                    path=path,
                    line_number=line_number,
                    end_line=line_number,
                    match_kind="exact_content",
                ))
                if len(matches) >= 20:
                    break
            if len(matches) >= 20:
                break
        unique_matches = _dedupe_matches(matches)
        status: Literal["found", "ambiguous", "not_evidenced"]
        if not unique_matches:
            status = "not_evidenced"
        elif len({match.path for match in unique_matches}) == 1:
            status = "found"
        else:
            status = "ambiguous"
        conclusion = {
            "found": "Exact target evidence resolves to one repository file.",
            "ambiguous": "Exact target evidence resolves to multiple repository files.",
            "not_evidenced": "The bounded repository read found no exact evidence for this target.",
        }[status]
        resolutions.append(RepositoryTargetResolution(
            target=target,
            status=status,
            conclusion=conclusion,
            matches=unique_matches,
        ))

    evidence_ids = [match.evidence_id for resolution in resolutions for match in resolution.matches]
    unresolved: list[str] = []
    if not targets:
        unresolved.append("No exact repository target identifiers are present in the persisted Draft or Job payload.")
    unresolved.extend(
        f"Target from {resolution.target.source_field} is not evidenced: {resolution.target.value}"
        for resolution in resolutions
        if resolution.status == "not_evidenced"
    )
    unresolved.extend(
        f"Target from {resolution.target.source_field} resolves to multiple files: {resolution.target.value}"
        for resolution in resolutions
        if resolution.status == "ambiguous"
    )
    if files_read >= read_file_limit or bytes_read >= read_byte_limit:
        unresolved.append("The bounded repository read budget was exhausted before every mapped file could be inspected.")

    analysis = TargetedRepositoryAnalysis(
        schema_version=1,
        source_id=source.source_id,
        repository_commit=source.resolved_commit,
        repository_map_path="repository_map.json",
        targets=targets,
        resolutions=resolutions,
        compatibility=CompatibilityAssessment(
            status="uncertain",
            conclusion="Repository target evidence alone is insufficient for a semantic compatibility decision.",
            evidence_ids=evidence_ids,
        ),
        unresolved_facts=unresolved,
        files_read=files_read,
        bytes_read=bytes_read,
        read_file_limit=read_file_limit,
        read_byte_limit=read_byte_limit,
    )
    _write_json_atomic(output_dir / "targeted_repository_analysis.json", analysis)
    return analysis


def targeted_analysis_observations(
    analysis: TargetedRepositoryAnalysis,
    *,
    created_at: str,
) -> list[AnalysisObservation]:
    """Project evidenced target resolutions into the formal artifact synthesizer."""

    observations: list[AnalysisObservation] = []
    counter = 0
    for resolution in analysis.resolutions:
        category = {
            "baseline_entrypoint": "entrypoint_target",
            "baseline_config": "config_target",
            "user_target_module_hints": "module_target",
            "job_payload": "module_target",
        }[resolution.target.source_field]
        evidence_by_path: dict[str, list[str]] = {}
        for match in resolution.matches:
            evidence_by_path.setdefault(match.path, []).append(match.evidence_id)
        for path, evidence_ids in evidence_by_path.items():
            counter += 1
            observations.append(AnalysisObservation(
                observation_id=f"obs_target_{counter:03d}",
                category=category,
                summary=f"Exact target evidence recorded for {path}",
                status="confirmed" if resolution.status == "found" else "candidate",
                evidence_ids=list(dict.fromkeys(evidence_ids)),
                path=path,
                target_source_field=resolution.target.source_field,
                created_at=created_at,
            ))
    return observations


def _bounded_repository_map(root: Path, *, file_limit: int) -> tuple[list[RepositoryMapEntry], int]:
    entries: list[RepositoryMapEntry] = []
    omitted = 0
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
            if len(entries) >= file_limit:
                omitted += 1
                continue
            entries.append(RepositoryMapEntry(
                path=path.relative_to(root).as_posix(),
                size_bytes=path.stat().st_size,
            ))
    return entries, omitted


def _exact_path_candidates(
    entries: list[RepositoryMapEntry],
    targets: list[RepositoryAnalysisTarget],
) -> list[str]:
    candidates: list[str] = []
    for entry in entries:
        if any(_exact_path_match_kind(entry.path, target.value) is not None for target in targets):
            candidates.append(entry.path)
    return candidates


def _exact_path_match_kind(path: str, target: str) -> Literal["exact_path", "exact_basename"] | None:
    if path == target:
        return "exact_path"
    if PurePosixPath(path).name == target:
        return "exact_basename"
    return None


def _record_match(
    *,
    context: ActiveRepositoryContext,
    evidence_index_path: Path,
    evidence_id: str,
    tool_call_id: str,
    path: str,
    line_number: int,
    end_line: int,
    match_kind: Literal["exact_path", "exact_basename", "exact_content"],
) -> RepositoryTargetMatch:
    evidence = create_file_evidence(
        context=context,
        request=FileEvidenceRequest(
            evidence_id=evidence_id,
            path=path,
            start_line=line_number,
            end_line=end_line,
            tool_call_id=tool_call_id,
        ),
    )
    append_evidence(evidence_index_path, evidence)
    return RepositoryTargetMatch(
        path=path,
        match_kind=match_kind,
        start_line=line_number,
        end_line=end_line,
        repository_commit=evidence.repository_commit,
        file_sha256=evidence.file_sha256,
        snippet_sha256=evidence.snippet_sha256,
        evidence_id=evidence.evidence_id,
    )


def _dedupe_matches(matches: list[RepositoryTargetMatch]) -> list[RepositoryTargetMatch]:
    result: list[RepositoryTargetMatch] = []
    seen: set[tuple[str, int, str]] = set()
    for match in matches:
        key = (match.path, match.start_line, match.match_kind)
        if key not in seen:
            result.append(match)
            seen.add(key)
    return result


def _write_json_atomic(path: Path, value: BaseModel) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        data = json.dumps(value.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2, sort_keys=True)
        with tmp.open("wb") as handle:
            handle.write(data.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
