from pathlib import Path

import pytest

from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.reporting.discussion import append_message, load_messages
from autoad_researcher.reporting.review import create_proposal, record_review
from autoad_researcher.reporting.service import ReportRequestService
from autoad_researcher.reporting.store import ReportStore
from autoad_researcher.worker.main import _process_pending_jobs


def _ready_report(tmp_path: Path):
    run_dir = tmp_path / "run_report_collaboration"; run_dir.mkdir()
    session = ExperimentSessionStore().create_or_get(run_dir, task_ref="tasks/task.json", task_hash="e" * 64, execution_mode="approve_each_step")[0]
    result, _ = ReportRequestService().request(run_dir, session_id=session.session_id)
    for _ in range(3): _process_pending_jobs(run_dir)
    return run_dir, result["manifest"].report_id


def test_discussion_is_report_bound_and_rejects_unknown_evidence(tmp_path: Path):
    run_dir, report_id = _ready_report(tmp_path)
    item = append_message(run_dir, report_id=report_id, role="user", content="解释报告")
    assert load_messages(run_dir, report_id=report_id)[0].message_id == item.message_id
    with pytest.raises(ValueError, match="unknown Evidence"):
        append_message(run_dir, report_id=report_id, role="user", content="x", evidence_ids=["evidence_missing"])


def test_proposal_is_not_handoff_and_accept_is_only_review(tmp_path: Path):
    run_dir, report_id = _ready_report(tmp_path)
    proposal = create_proposal(run_dir, report_id=report_id, proposal_type="REQUEST_HUMAN", rationale="需要人工判断")
    assert proposal.status == "READY_FOR_CONFIRMATION"
    assert not list((run_dir / "jobs").glob("pipeline_jobs.jsonl")) or len((run_dir / "jobs" / "pipeline_jobs.jsonl").read_text().splitlines()) == 3
    review = record_review(run_dir, report_id=report_id, decision="accept")
    assert review.decision == "accept"
    assert ReportStore().load_state(run_dir, report_id).review_status == "accepted"
