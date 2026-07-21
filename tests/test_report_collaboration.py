from pathlib import Path

import pytest

from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.reporting.discussion import DiscussionResponse, append_message, complete_turn, load_messages, start_turn
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


def test_discussion_turn_replay_completion_and_tail_recovery(tmp_path: Path):
    run_dir, report_id = _ready_report(tmp_path)
    first = start_turn(run_dir, report_id=report_id, request_id="turn_1", content="解释结论")
    replay = start_turn(run_dir, report_id=report_id, request_id="turn_1", content="解释结论")
    assert replay.turn_id == first.turn_id and replay.status == "pending"
    completed = complete_turn(run_dir, report_id=report_id, turn_id=first.turn_id, response=DiscussionResponse(answer="当前报告没有可核验的提升结论。", response_kind="insufficient_evidence"))
    assert completed.status == "completed"
    assert [item.role for item in load_messages(run_dir, report_id=report_id)] == ["user", "assistant"]
    path = run_dir / "reports" / report_id / "discussion" / "turns.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"incomplete"')
    assert len(load_messages(run_dir, report_id=report_id)) == 2
    with pytest.raises(ValueError, match="request_id conflicts"):
        start_turn(run_dir, report_id=report_id, request_id="turn_1", content="不同消息")


def test_proposal_is_not_handoff_and_accept_is_only_review(tmp_path: Path):
    run_dir, report_id = _ready_report(tmp_path)
    job_count_before = len((run_dir / "jobs" / "pipeline_jobs.jsonl").read_text().splitlines())
    proposal = create_proposal(run_dir, report_id=report_id, proposal_type="REQUEST_HUMAN", rationale="需要人工判断")
    assert proposal.status == "READY_FOR_CONFIRMATION"
    assert len((run_dir / "jobs" / "pipeline_jobs.jsonl").read_text().splitlines()) == job_count_before
    review = record_review(run_dir, report_id=report_id, request_id="review_accept", decision="accept")
    assert review.decision == "accept"
    assert ReportStore().load_state(run_dir, report_id).review_status == "accepted"


def test_review_claims_are_idempotent_and_project_the_latest_status(tmp_path: Path):
    run_dir, report_id = _ready_report(tmp_path)
    first = record_review(
        run_dir,
        report_id=report_id,
        request_id="review_claims",
        decision="disputed",
        disputed_claims=["claim_summary"],
    )
    replay = record_review(
        run_dir,
        report_id=report_id,
        request_id="review_claims",
        decision="disputed",
        disputed_claims=["claim_summary"],
    )
    assert replay.decision_id == first.decision_id
    assert ReportStore().load_state(run_dir, report_id).review_status == "disputed"
    with pytest.raises(ValueError, match="request_id conflicts"):
        record_review(
            run_dir,
            report_id=report_id,
            request_id="review_claims",
            decision="accept",
        )
    with pytest.raises(ValueError, match="unknown claim IDs"):
        record_review(
            run_dir,
            report_id=report_id,
            request_id="review_unknown",
            decision="accept",
            accepted_claims=["claim_missing"],
        )
