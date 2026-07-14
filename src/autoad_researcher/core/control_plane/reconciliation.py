"""Rebuild terminal audit events from authoritative control-plane state."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from autoad_researcher.assistant.v2.contract_confirmation_service import (
    mark_confirmation_audit_repaired,
    recover_contract_confirmation,
)
from autoad_researcher.core.control_plane.event_store import ControlPlaneEventStore
from autoad_researcher.core.control_plane.materialization_requests import MaterializationRequestStore
from autoad_researcher.core.control_plane.readiness import (
    load_experiment_readiness,
    load_experiment_session,
)
from autoad_researcher.core.control_plane.job_store import PipelineJobStore
from autoad_researcher.core.control_plane.io import atomic_write_json
from autoad_researcher.core.control_plane.unit_of_work import ControlPlaneUnitOfWork


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def reconcile_incomplete_terminal_attempts(run_dir: Path) -> int:
    """Recover every torn terminal attempt without requiring a live lease."""
    repaired = 0
    with ControlPlaneUnitOfWork(run_dir) as uow:
        jobs = uow.jobs._load_unlocked()
        for job in jobs:
            if job.status != "running":
                continue
            claim, attempt_dir = uow.jobs._load_active_claim_unlocked(job)
            if not (attempt_dir / "attempt_result.json").is_file():
                continue
            result = uow.jobs._load_attempt_result_unlocked(attempt_dir)
            uow.jobs._validate_attempt_result_identity_unlocked(result, claim, attempt_dir)
            recovered = uow.jobs.recover_from_terminal_attempt_unlocked(
                job_id=job.job_id,
                expected_attempt_count=job.attempt_count,
                expected_claim_token=claim.claim_token,
            )
            if job.job_type == "experiment_prepare":
                from autoad_researcher.core.control_plane.experiment_state import (
                    transition_session_if_present_unlocked,
                )

                session_status = {
                    "queued": "queued",
                    "completed": "materialized",
                    "failed": "failed",
                }[recovered.status]
                transition_session_if_present_unlocked(
                    run_dir,
                    prepare_job_id=recovered.job_id,
                    status=session_status,
                    now=result.finished_at,
                    error=str(recovered.error) if recovered.error is not None else None,
                )
            repaired += 1
    return repaired


def reconcile_materialization_requests(run_dir: Path) -> int:
    """Repair request-ledger/Job tears before any new claim is issued."""
    with ControlPlaneUnitOfWork(run_dir) as uow:
        store = MaterializationRequestStore(run_dir)
        before = store._load_unlocked()
        before_state = [record.model_dump(mode="json") for record in before]
        store.reconcile_unlocked(uow, now=_utcnow())
        after = store._load_unlocked()
        after_state = [record.model_dump(mode="json") for record in after]
        return int(before_state != after_state)


def reconcile_control_plane_events(run_dir: Path) -> int:
    """Append idempotent terminal projections without holding the run lock."""
    store = ControlPlaneEventStore(run_dir)
    store.read_since()
    projection = recover_contract_confirmation(run_dir)
    session = load_experiment_session(run_dir)
    readiness = load_experiment_readiness(run_dir)
    jobs = PipelineJobStore(run_dir).list()
    requests = MaterializationRequestStore(run_dir).list()

    events: list[tuple[str, str, dict]] = []
    if projection is not None and projection.status != "pending":
        events.append((
            "control_plane.contract.reconciled",
            f"control_plane.contract.reconciled:{projection.confirmation_id}:{projection.status}",
            {
                "confirmation_id": projection.confirmation_id,
                "status": projection.status,
                "contract_sha256": projection.contract_sha256,
                "inconsistency": projection.inconsistency,
            },
        ))
    if session is not None:
        events.append((
            "control_plane.session.reconciled",
            f"control_plane.session.reconciled:{session.session_id}:{session.status}:{session.updated_at.isoformat()}",
            {
                "session_id": session.session_id,
                "prepare_job_id": session.prepare_job_id,
                "status": session.status,
            },
        ))
    for job in jobs:
        if job.status not in {"completed", "failed"}:
            continue
        events.append((
            "control_plane.job.reconciled",
            f"control_plane.job.reconciled:{job.job_id}:{job.status}:attempt:{job.attempt_count}",
            {
                "job_id": job.job_id,
                "job_type": job.job_type,
                "status": job.status,
                "attempt_count": job.attempt_count,
                "outputs": job.outputs,
                "error": job.error,
            },
        ))
    if readiness is not None:
        events.append((
            "control_plane.readiness.reconciled",
            f"control_plane.readiness.reconciled:{readiness.session_id}:revision:{readiness.revision}",
            {
                "session_id": readiness.session_id,
                "revision": readiness.revision,
                "materialization_input_sha256": readiness.materialization_input_sha256,
                "planning_ready": readiness.planning_readiness.ready,
                "implementation_ready": readiness.implementation_readiness.ready,
                "execution_ready": readiness.execution_readiness.ready,
            },
        ))
    for request in requests:
        if request.status not in {"not_scheduled", "completed", "failed"}:
            continue
        events.append((
            "control_plane.materialization_request.reconciled",
            f"control_plane.materialization_request.reconciled:{request.request_id}:{request.status}",
            request.model_dump(mode="json", exclude_none=True),
        ))

    before = len(store.read_since())
    for event_type, key, payload in events:
        store.append_once(event_type, key, payload)
    added = len(store.read_since()) - before
    if projection is not None and projection.audit_repair_required:
        mark_confirmation_audit_repaired(run_dir)
    health_path = run_dir / "events" / "audit_health.json"
    if health_path.is_file():
        atomic_write_json(health_path, {"schema_version": 1, "status": "healthy"})
    return added
