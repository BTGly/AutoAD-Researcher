"""Finite report-source inventory collected from existing control-plane stores."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

from autoad_researcher.reporting.snapshot import resolve_run_relative_file, sha256_file
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2


def collect_snapshot_sources(
    run_dir: Path,
    *,
    session_id: str,
    session: Any,
    session_ref: ArtifactReferenceV2,
) -> list[ArtifactReferenceV2]:
    """Collect only typed, finite control-plane artifacts for this Session."""

    refs = [session_ref]

    def add(locator: str, artifact_type: str, artifact_id: str, *, required: bool = False) -> None:
        raw_path = run_dir.joinpath(*PurePosixPath(locator).parts)
        if not raw_path.exists() and not raw_path.is_symlink():
            if required:
                raise ValueError("Session references a missing report source artifact")
            return
        resolved = resolve_run_relative_file(run_dir, locator)
        refs.append(
            ArtifactReferenceV2(
                artifact_id=artifact_id,
                artifact_type=artifact_type,
                locator=locator,
                sha256=sha256_file(resolved),
                size_bytes=resolved.stat().st_size,
            )
        )

    if session.evaluation_contract_ref:
        add(session.evaluation_contract_ref, "evaluation_contract", f"evaluation_contract:{session_id}", required=True)
    if session.environment_snapshot_ref:
        add(session.environment_snapshot_ref, "environment_snapshot", f"environment_snapshot:{session_id}", required=True)

    from autoad_researcher.experiment.attempt_store import ExperimentAttemptStore
    from autoad_researcher.experiment.promotion import CandidateRegistry

    add(f"experiments/ideas/{session_id}.json", "idea_tree", f"idea_tree:{session_id}")
    add(f"experiments/cognition/{session_id}/cost_summary.json", "cognitive_cost_summary", f"cognitive_cost_summary:{session_id}")
    add(f"experiments/stops/{session_id}/decision.json", "stop_decision", f"stop_decision:{session_id}")
    for attempt in ExperimentAttemptStore().list_for_session(run_dir, session_id=session_id):
        attempt_id = attempt.attempt_id
        add(f"experiments/attempts/{attempt_id}.json", "experiment_attempt", f"experiment_attempt:{attempt_id}", required=True)
        for filename, artifact_type in (
            ("outcome_card.json", "outcome_card"),
            ("scientific_assessment.json", "scientific_assessment"),
            ("assessment_reconciliation.json", "assessment_reconciliation"),
            ("scientific_evaluation_inputs.json", "scientific_evaluation_inputs"),
            ("metrics.json", "attempt_metrics"),
            ("failure_classification.json", "failure_classification"),
            ("execution_result.json", "execution_result"),
        ):
            add(f"attempts/{attempt_id}/{filename}", artifact_type, f"{artifact_type}:{attempt_id}")
        _add_registered_resource_reports(run_dir, attempt_id, add)
        _add_registered_execution_logs(run_dir, attempt_id, add)
    candidates = CandidateRegistry().list_candidates(run_dir, session_id=session_id)
    for candidate in candidates:
        add(
            f"experiments/champions/candidates/{candidate.candidate_id}.json",
            "candidate_snapshot",
            f"candidate_snapshot:{candidate.candidate_id}",
            required=True,
        )
    if candidates:
        add("experiments/champions/current_by_contract.json", "champion_pointers", "champion_pointers:current")
    return sorted(refs, key=lambda item: (item.locator, item.artifact_id))


def _add_registered_resource_reports(run_dir: Path, attempt_id: str, add) -> None:
    """Discover resource reports only through the execution output manifest."""

    from autoad_researcher.runner.models import ExperimentExecutionResult, OutputManifest
    from autoad_researcher.schemas.execution import ResourceUsageReport

    result_path = run_dir / "attempts" / attempt_id / "execution_result.json"
    if not result_path.is_file():
        return
    try:
        result = ExperimentExecutionResult.model_validate_json(result_path.read_text(encoding="utf-8"))
    except ValueError:
        return
    if not result.output_manifest_path:
        return
    manifest_path = run_dir / "attempts" / attempt_id / result.output_manifest_path
    if not manifest_path.is_file():
        return
    try:
        manifest = OutputManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    except ValueError:
        return
    for output in manifest.outputs:
        locator = f"attempts/{attempt_id}/{output.path}"
        path = run_dir.joinpath(*PurePosixPath(locator).parts)
        if not path.is_file():
            continue
        if sha256_file(path) != output.sha256:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload.pop("actual_gpu_hours", None)
            ResourceUsageReport.model_validate(payload)
        except ValueError:
            continue
        add(locator, "resource_usage_report", f"resource_usage_report:{attempt_id}:{output.path}", required=True)


def _add_registered_execution_logs(run_dir: Path, attempt_id: str, add) -> None:
    from autoad_researcher.runner.models import ExperimentExecutionResult

    path = run_dir / "attempts" / attempt_id / "execution_result.json"
    if not path.is_file():
        return
    try:
        result = ExperimentExecutionResult.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return
    for stream, locator in (("stdout", result.stdout_path), ("stderr", result.stderr_path)):
        add(f"attempts/{attempt_id}/{locator}", f"attempt_{stream}_log", f"attempt_{stream}_log:{attempt_id}")
