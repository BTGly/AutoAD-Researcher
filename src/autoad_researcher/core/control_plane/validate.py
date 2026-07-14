"""Two-phase validation for canonical control-plane stores."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoad_researcher.core.control_plane.errors import CorruptAuthoritativeStore
from autoad_researcher.core.control_plane.event_store import ControlPlaneEventStore
from autoad_researcher.core.control_plane.experiment_state import load_session_unlocked
from autoad_researcher.core.control_plane.job_store import (
    EXPERIMENT_PREPARE_JOB_TYPE,
    PipelineJobStore,
)
from autoad_researcher.core.control_plane.lock import RunMutationLock
from autoad_researcher.core.control_plane.materialization_requests import (
    MaterializationRequestStore,
)


def validate_control_plane_store(run_dir: Path) -> dict[str, Any]:
    events = ControlPlaneEventStore(run_dir).read_since()
    validate_authoritative_store_syntax(run_dir)
    result = validate_authoritative_control_plane_invariants(run_dir)
    return {
        "run_id": run_dir.name,
        "valid": True,
        "event_count": len(events),
        "job_count": result["job_count"],
    }


def validate_authoritative_store_syntax(run_dir: Path) -> dict[str, Any]:
    """Validate individual files and immutable artifact identities only.

    This phase intentionally accepts cross-store tears that recovery can repair.
    """
    from autoad_researcher.core.control_plane.readiness import load_readiness_unlocked

    with RunMutationLock(run_dir, mode="shared"):
        job_store = PipelineJobStore(run_dir)
        jobs = job_store._load_unlocked()
        load_session_unlocked(run_dir)
        load_readiness_unlocked(run_dir)
        requests = MaterializationRequestStore(run_dir)._load_unlocked()
        claims = job_store._load_claim_records_unlocked()
        for claim, attempt_dir in claims:
            if not (attempt_dir / "attempt_result.json").is_file():
                continue
            result = job_store._load_attempt_result_unlocked(attempt_dir)
            job_store._validate_attempt_result_identity_unlocked(result, claim, attempt_dir)
    return {
        "run_id": run_dir.name,
        "valid": True,
        "job_count": len(jobs),
        "request_count": len(requests),
        "claim_count": len(claims),
    }


def validate_authoritative_control_plane_invariants(run_dir: Path) -> dict[str, Any]:
    """Validate cross-store invariants after recovery has completed."""
    from autoad_researcher.core.control_plane.readiness import load_readiness_unlocked

    with RunMutationLock(run_dir, mode="shared"):
        job_store = PipelineJobStore(run_dir)
        jobs = job_store._load_unlocked()
        jobs_by_id = {job.job_id: job for job in jobs}
        session = load_session_unlocked(run_dir)
        readiness = load_readiness_unlocked(run_dir)
        requests = MaterializationRequestStore(run_dir)._load_unlocked()
        requests_by_id = {record.request_id: record for record in requests}
        claims = job_store._load_claim_records_unlocked()

        scheduled_by_job: dict[str, list[str]] = {}
        for record in requests:
            if record.status == "scheduled":
                scheduled_by_job.setdefault(record.active_job_id, []).append(record.request_id)
        for job_id, request_ids in scheduled_by_job.items():
            if len(request_ids) > 1:
                raise CorruptAuthoritativeStore(
                    f"multiple scheduled materialization requests for job {job_id}"
                )

        if session is not None:
            job = jobs_by_id.get(session.prepare_job_id)
            if job is None or job.job_type != EXPERIMENT_PREPARE_JOB_TYPE:
                raise CorruptAuthoritativeStore("ExperimentSession prepare job is missing or invalid")
            expected_status = {
                "queued": "queued",
                "running": "preparing",
                "completed": "materialized",
                "failed": "failed",
            }[job.status]
            if session.status != expected_status:
                raise CorruptAuthoritativeStore(
                    "ExperimentSession status does not match its prepare job"
                )

        if readiness is not None and (
            session is None
            or readiness.session_id != session.session_id
            or readiness.contract_sha256 != session.contract_sha256
        ):
            raise CorruptAuthoritativeStore("ExperimentReadiness does not match ExperimentSession")

        for job in jobs:
            if job.pending_control_request_id and job.active_control_request_id:
                raise CorruptAuthoritativeStore(
                    f"job {job.job_id} has both pending and active control requests"
                )
            pointer = job.pending_control_request_id or job.active_control_request_id
            if pointer is not None:
                record = requests_by_id.get(pointer)
                if (
                    record is None
                    or record.status != "scheduled"
                    or record.active_job_id != job.job_id
                ):
                    raise CorruptAuthoritativeStore(
                        f"job {job.job_id} has an invalid materialization request reference"
                    )
            if job.status == "queued" and job.active_control_request_id is not None:
                raise CorruptAuthoritativeStore(f"queued job {job.job_id} has an active request")
            if job.status == "running" and job.pending_control_request_id is not None:
                raise CorruptAuthoritativeStore(f"running job {job.job_id} has a pending request")
            if job.status in {"completed", "failed"} and pointer is not None:
                raise CorruptAuthoritativeStore(f"terminal job {job.job_id} retains a request")

            job_claims = [claim for claim, _ in claims if claim.job_id == job.job_id]
            if job.attempt_count < max((claim.attempt_count for claim in job_claims), default=0) - 1:
                raise CorruptAuthoritativeStore(
                    f"job {job.job_id} attempt_count is inconsistent with attempt paths"
                )
            if job.status == "running":
                active = [
                    (claim, attempt_dir)
                    for claim, attempt_dir in claims
                    if claim.job_id == job.job_id
                    and claim.attempt_count == job.attempt_count
                    and claim.claim_token == job.claim_token
                ]
                if len(active) != 1:
                    raise CorruptAuthoritativeStore(
                        f"running job {job.job_id} has no unique active claim"
                    )
                result_path = active[0][1] / "attempt_result.json"
                if result_path.is_file():
                    raise CorruptAuthoritativeStore(
                        f"running job {job.job_id} retains a terminal AttemptResult"
                    )

            if job.job_type != EXPERIMENT_PREPARE_JOB_TYPE or job.status not in {
                "completed",
                "failed",
            }:
                continue
            results = []
            for claim, attempt_dir in claims:
                if claim.job_id != job.job_id or claim.attempt_count != job.attempt_count:
                    continue
                if not (attempt_dir / "attempt_result.json").is_file():
                    continue
                result = job_store._load_attempt_result_unlocked(attempt_dir)
                job_store._validate_attempt_result_identity_unlocked(result, claim, attempt_dir)
                if job.status == "completed" and result.status in {"published", "no_op"}:
                    results.append(result)
                if job.status == "failed" and result.status in {
                    "failed",
                    "lease_lost",
                    "stale_input",
                }:
                    results.append(result)
            if len(results) != 1:
                raise CorruptAuthoritativeStore(
                    f"terminal experiment_prepare job {job.job_id} has no unique terminal result"
                )
            if job.status == "completed":
                if readiness is None or session is None or session.prepare_job_id != job.job_id:
                    raise CorruptAuthoritativeStore(
                        f"completed experiment_prepare job {job.job_id} has no canonical readiness"
                    )

        for record in requests:
            if record.status != "scheduled":
                continue
            job = jobs_by_id.get(record.active_job_id)
            if job is None or (
                job.pending_control_request_id != record.request_id
                and job.active_control_request_id != record.request_id
            ):
                raise CorruptAuthoritativeStore(
                    f"scheduled request {record.request_id} is not bound to its job"
                )

    return {"run_id": run_dir.name, "valid": True, "job_count": len(jobs)}


def validate_authoritative_control_plane_store(run_dir: Path) -> dict[str, Any]:
    """Compatibility facade for callers that require full validation."""
    validate_authoritative_store_syntax(run_dir)
    return validate_authoritative_control_plane_invariants(run_dir)
