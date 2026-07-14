from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from autoad_researcher.assistant.v2.contract_confirmation_service import (
    decide_contract_confirmation,
    recover_contract_confirmation,
    request_contract_confirmation,
)
from autoad_researcher.assistant.v2.intent_contract import (
    ResearchIntentContract,
    save_confirmed_contract,
    save_contract_draft,
)
from autoad_researcher.core.control_plane import (
    ControlPlaneEventStore,
    ContractConfirmationProjection,
    CorruptAuthoritativeStore,
    PipelineJobStore,
)
from autoad_researcher.core.control_plane.io import atomic_write_json
from autoad_researcher.core.control_plane.materialization_requests import (
    MaterializationRequestStore,
)
from autoad_researcher.core.control_plane.readiness import load_experiment_session
from autoad_researcher.core.control_plane.reconciliation import reconcile_control_plane_events
from autoad_researcher.server.routes import experiment_control
from autoad_researcher.worker.main import _process_pending_jobs


def _run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run_control_api"
    run_dir.mkdir()
    return run_dir


def _contract(run_dir: Path) -> ResearchIntentContract:
    return ResearchIntentContract(
        run_id=run_dir.name,
        research_goal="Improve PatchCore",
        baseline="PatchCore",
        dataset="MVTec AD",
        primary_metrics=["image_level_auroc"],
        success_criteria="improve image AUROC under the same protocol",
        execution_mode="approve_each_step",
    )


def _approve(run_dir: Path):
    contract = _contract(run_dir)
    save_contract_draft(run_dir, contract)
    pending = request_contract_confirmation(run_dir, contract)
    decide_contract_confirmation(
        run_dir,
        confirmation_id=pending["confirmation_id"],
        decision="approved",
    )
    session = load_experiment_session(run_dir)
    assert session is not None
    return session


@pytest.mark.asyncio
async def test_materialization_api_returns_409_for_already_queued_and_replays_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(experiment_control, "RUNS_ROOT", str(tmp_path))
    run_dir = _run_dir(tmp_path)
    session = _approve(run_dir)
    command = experiment_control.MaterializationCommand(
        request_id="remat_queued",
        force=True,
        reason="user requested refresh",
    )

    with pytest.raises(experiment_control.HTTPException) as first:
        await experiment_control.request_experiment_materialization(run_dir.name, command)
    with pytest.raises(experiment_control.HTTPException) as replay:
        await experiment_control.request_experiment_materialization(run_dir.name, command)

    assert first.value.status_code == 409
    assert first.value.detail["error"] == "job_already_running"
    assert replay.value.detail == first.value.detail
    record = MaterializationRequestStore(run_dir).get("remat_queued")
    assert record is not None and record.executed is False
    assert record.active_job_id == session.prepare_job_id


@pytest.mark.asyncio
async def test_completed_job_is_reused_and_request_completes_after_no_op(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(experiment_control, "RUNS_ROOT", str(tmp_path))
    run_dir = _run_dir(tmp_path)
    session = _approve(run_dir)
    assert _process_pending_jobs(run_dir, worker_id="worker_initial") == 1
    initial_job = PipelineJobStore(run_dir).get(session.prepare_job_id)
    assert initial_job is not None and initial_job.status == "completed"

    command = experiment_control.MaterializationCommand(
        request_id="remat_completed",
        force=False,
        reason="verify current local snapshots",
    )
    scheduled = await experiment_control.request_experiment_materialization(run_dir.name, command)
    queued = PipelineJobStore(run_dir).get(session.prepare_job_id)
    assert scheduled["status"] == "scheduled"
    assert queued is not None and queued.status == "queued"
    assert queued.job_id == initial_job.job_id
    assert queued.pending_control_request_id == "remat_completed"

    assert _process_pending_jobs(run_dir, worker_id="worker_remat") == 1
    completed = PipelineJobStore(run_dir).get(session.prepare_job_id)
    record = MaterializationRequestStore(run_dir).get("remat_completed")
    assert completed is not None and completed.status == "completed"
    assert completed.attempt_count == 2
    assert record is not None and record.status == "completed"
    attempt_dirs = list(
        (run_dir / "experiment_agents" / "attempts" / completed.job_id).glob("attempt_2_*")
    )
    attempt_result = json.loads(
        (attempt_dirs[0] / "attempt_result.json").read_text(encoding="utf-8")
    )
    assert attempt_result["status"] == "no_op"
    replay = await experiment_control.request_experiment_materialization(run_dir.name, command)
    assert replay["status"] == "completed"

    conflict = command.model_copy(update={"force": True})
    with pytest.raises(experiment_control.HTTPException) as exc_info:
        await experiment_control.request_experiment_materialization(run_dir.name, conflict)
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_retry_api_reuses_failed_job_and_resets_recovery_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(experiment_control, "RUNS_ROOT", str(tmp_path))
    run_dir = _run_dir(tmp_path)
    session = _approve(run_dir)
    store = PipelineJobStore(run_dir)
    claimed = store.claim_next(worker_id="worker_failure")
    assert claimed is not None and claimed.claim_token is not None
    store.fail(
        claimed.job_id,
        claim_token=claimed.claim_token,
        expected_attempt_count=claimed.attempt_count,
        error="technical_failure",
    )

    result = await experiment_control.retry_experiment_materialization(
        run_dir.name,
        experiment_control.MaterializationCommand(
            request_id="remat_retry",
            force=True,
            reason="explicit user retry",
        ),
    )

    queued = store.get(session.prepare_job_id)
    assert result["status"] == "scheduled"
    assert queued is not None and queued.status == "queued"
    assert queued.consecutive_stale_count == 0
    assert queued.consecutive_lease_expiry_count == 0
    assert queued.job_id == session.prepare_job_id


def test_contract_survives_corrupt_audit_and_marks_repair_required(tmp_path: Path):
    run_dir = _run_dir(tmp_path)
    contract = _contract(run_dir)
    save_contract_draft(run_dir, contract)
    events = run_dir / "events" / "events.jsonl"
    events.parent.mkdir(parents=True, exist_ok=True)
    events.write_text("{truncated\n", encoding="utf-8")

    pending = request_contract_confirmation(run_dir, contract)
    result = decide_contract_confirmation(
        run_dir,
        confirmation_id=pending["confirmation_id"],
        decision="approved",
    )

    assert result["status"] == "approved"
    assert result["repair_required"] is True
    assert (run_dir / "research_intent_contract.json").is_file()
    assert load_experiment_session(run_dir) is not None
    assert events.read_text(encoding="utf-8") == "{truncated\n"


def test_contract_projection_recovery_matrix_and_hash_conflict(tmp_path: Path):
    run_dir = _run_dir(tmp_path)
    contract = _contract(run_dir)
    save_confirmed_contract(run_dir, contract)

    recovered = recover_contract_confirmation(run_dir)
    assert recovered is not None and recovered.status == "confirmed"
    assert load_experiment_session(run_dir) is not None

    damaged = recovered.model_copy(update={"contract_sha256": "f" * 64})
    atomic_write_json(
        run_dir / "contract_confirmation.json",
        damaged.model_dump(mode="json", exclude_none=True),
    )
    with pytest.raises(CorruptAuthoritativeStore, match="hash mismatch"):
        recover_contract_confirmation(run_dir)

    second = tmp_path / "run_projection_only"
    second.mkdir()
    projection = ContractConfirmationProjection(
        confirmation_id="contract_confirmation_projection_only",
        draft_sha256="a" * 64,
        status="confirmed",
        decision="approved",
        contract_sha256="b" * 64,
        requested_at=datetime.now(timezone.utc),
        resolved_at=datetime.now(timezone.utc),
    )
    atomic_write_json(
        second / "contract_confirmation.json",
        projection.model_dump(mode="json", exclude_none=True),
    )
    repaired = recover_contract_confirmation(second)
    assert repaired is not None and repaired.status == "rejected"
    assert repaired.inconsistency == "confirmed_projection_without_contract"


def test_terminal_events_can_be_rebuilt_after_audit_projection_repair(tmp_path: Path):
    run_dir = _run_dir(tmp_path)
    _approve(run_dir)
    assert _process_pending_jobs(run_dir, worker_id="worker_reconcile") == 1
    events_path = run_dir / "events" / "events.jsonl"
    events_path.unlink()

    appended = reconcile_control_plane_events(run_dir)
    event_types = {event.type for event in ControlPlaneEventStore(run_dir).read_since()}

    assert appended >= 4
    assert "control_plane.contract.reconciled" in event_types
    assert "control_plane.session.reconciled" in event_types
    assert "control_plane.job.reconciled" in event_types
    assert "control_plane.readiness.reconciled" in event_types
    assert reconcile_control_plane_events(run_dir) == 0
