from pathlib import Path

import pytest

from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.reporting.review import (
    confirm_proposal,
    create_proposal,
    reject_proposal,
)
from autoad_researcher.reporting.service import ReportRequestService
from autoad_researcher.worker.main import _process_pending_jobs


def _ready_report(tmp_path: Path) -> tuple[Path, str]:
    run_dir = tmp_path / "run_report_handoff"
    run_dir.mkdir()
    session, _ = ExperimentSessionStore().create_or_get(
        run_dir,
        task_ref="tasks/task.json",
        task_hash="e" * 64,
        execution_mode="approve_each_step",
    )
    result, _ = ReportRequestService().request(run_dir, session_id=session.session_id)
    for _ in range(3):
        _process_pending_jobs(run_dir)
    return run_dir, result["manifest"].report_id


def test_human_proposal_only_handoffs_after_confirmation_and_replays(tmp_path: Path):
    run_dir, report_id = _ready_report(tmp_path)
    proposal = create_proposal(
        run_dir,
        report_id=report_id,
        proposal_type="REQUEST_HUMAN",
        rationale="需要人工决定是否追加实验",
    )
    assert proposal.status == "READY_FOR_CONFIRMATION"
    first = confirm_proposal(run_dir, report_id=report_id, proposal_id=proposal.proposal_id)
    second = confirm_proposal(run_dir, report_id=report_id, proposal_id=proposal.proposal_id)
    assert first.status == second.status == "HANDED_OFF"
    assert first.handoff == second.handoff == {"kind": "human_queue", "proposal_id": proposal.proposal_id}


def test_rejected_proposal_cannot_handoff(tmp_path: Path):
    run_dir, report_id = _ready_report(tmp_path)
    proposal = create_proposal(
        run_dir,
        report_id=report_id,
        proposal_type="REQUEST_HUMAN",
        rationale="请人工复核",
    )
    assert reject_proposal(run_dir, report_id=report_id, proposal_id=proposal.proposal_id).status == "REJECTED"
    with pytest.raises(ValueError, match="rejected proposal"):
        confirm_proposal(run_dir, report_id=report_id, proposal_id=proposal.proposal_id)
