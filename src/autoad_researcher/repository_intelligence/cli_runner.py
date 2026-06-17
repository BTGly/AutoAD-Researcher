"""CLI orchestration for Repository Intelligence R13."""

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.benchmarks.hashing import canonical_sha256
from autoad_researcher.repository_intelligence.acquisition import RepositoryAcquisitionRequest, RepositoryAcquisitionRunner
from autoad_researcher.repository_intelligence.analysis import RepositoryAnalysisAgent
from autoad_researcher.repository_intelligence.artifacts import synthesize_repository_artifacts
from autoad_researcher.repository_intelligence.clarification_handoff import build_clarification_question_candidates
from autoad_researcher.repository_intelligence.handoff import build_environment_plan_handoff
from autoad_researcher.repository_intelligence.models import RepositoryIntelligenceRequest
from autoad_researcher.repository_intelligence.validate import RepositoryValidationReport, validate_repository_intelligence_run


class RepositoryIntelligenceCliSummary(BaseModel):
    """Machine-readable CLI summary."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    run_id: str
    status: Literal["success", "failed", "blocked"]
    run_dir: str
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    validation_status: str | None = None
    artifacts: list[str] = Field(default_factory=list)
    message: str


def run_local_repository_intelligence(
    *,
    run_id: str,
    runs_root: Path,
    local_path: Path,
    resume: bool,
) -> RepositoryIntelligenceCliSummary:
    """Run the offline local-path Repository Intelligence flow."""
    run_dir = runs_root / run_id
    result_path = run_dir / "repository_intelligence_result.json"
    request = RepositoryIntelligenceRequest(
        schema_version=1,
        request_id=f"req_{run_id}",
        run_id=run_id,
        user_goal="Analyze local repository",
        local_path="local_source",
        discovery_allowed=False,
        user_confirmation_policy="when_ambiguous",
        budget_profile="small",
    )
    request_fingerprint = canonical_sha256(request)
    fingerprint_path = run_dir / "resume_fingerprint.json"

    if run_dir.exists() and not resume:
        return RepositoryIntelligenceCliSummary(
            schema_version=1,
            run_id=run_id,
            status="blocked",
            run_dir=run_dir.as_posix(),
            request_fingerprint=request_fingerprint,
            message="run directory already exists; pass --resume to reuse completed result",
        )
    if resume and result_path.is_file():
        existing = RepositoryIntelligenceCliSummary.model_validate_json(result_path.read_text(encoding="utf-8"))
        if existing.request_fingerprint != request_fingerprint:
            return RepositoryIntelligenceCliSummary(
                schema_version=1,
                run_id=run_id,
                status="blocked",
                run_dir=run_dir.as_posix(),
                request_fingerprint=request_fingerprint,
                message="resume fingerprint mismatch",
            )
        return existing

    run_dir.mkdir(parents=True, exist_ok=True)
    _write_json_atomic(run_dir / "request.json", request)
    _write_json_atomic(fingerprint_path, {"request_fingerprint": request_fingerprint})

    acquisition = RepositoryAcquisitionRunner().acquire(
        RepositoryAcquisitionRequest(
            schema_version=1,
            source_id="source_001",
            workspace_root=run_dir / "workspace",
            local_path=local_path,
            acquisition_profile="local",
        ),
        run_dir=run_dir,
    )
    if acquisition.status != "success" or acquisition.source is None:
        summary = RepositoryIntelligenceCliSummary(
            schema_version=1,
            run_id=run_id,
            status="failed",
            run_dir=run_dir.as_posix(),
            request_fingerprint=request_fingerprint,
            message=acquisition.error_message or "repository acquisition failed",
        )
        _write_json_replace(result_path, summary)
        return summary

    analysis = RepositoryAnalysisAgent().run_cycle(
        request=request,
        source=acquisition.source,
        repository_root=local_path,
        run_dir=run_dir,
        iteration=1,
        created_at="2026-06-17T00:00:00Z",
    )
    synthesized = synthesize_repository_artifacts(
        output_dir=run_dir,
        observations=analysis.observations,
        progress=analysis.progress,
    )
    validation = validate_repository_intelligence_run(
        source=acquisition.source,
        repository_root=local_path,
        run_dir=run_dir,
        artifacts=synthesized.paths,
    )
    _write_json_atomic(run_dir / "evidence_validation.json", validation)
    build_environment_plan_handoff(
        run_id=run_id,
        source=acquisition.source,
        artifact_dir=run_dir,
        output_path=run_dir / "environment_plan_candidate.json",
    )
    build_clarification_question_candidates(
        artifact_dir=run_dir,
        output_path=run_dir / "clarification_question_candidates.json",
    )
    status = "success" if validation.status == "passed" else "failed"
    summary = RepositoryIntelligenceCliSummary(
        schema_version=1,
        run_id=run_id,
        status=status,
        run_dir=run_dir.as_posix(),
        request_fingerprint=request_fingerprint,
        validation_status=validation.status,
        artifacts=sorted(synthesized.paths.path_set()),
        message="repository intelligence completed" if status == "success" else "repository intelligence validation failed",
    )
    _write_json_replace(result_path, summary)
    return summary


def _write_json_atomic(path: Path, value: BaseModel | dict) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite: {path}")
    _write_json_replace(path, value)


def _write_json_replace(path: Path, value: BaseModel | dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        payload = value.model_dump(mode="json", exclude_none=True) if isinstance(value, BaseModel) else value
        data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        with tmp.open("wb") as f:
            f.write(data.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
