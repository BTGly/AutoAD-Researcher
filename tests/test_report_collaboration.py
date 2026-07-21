import json
from pathlib import Path

import pytest

from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.reporting.discussion import DiscussionResponse, ReportDiscussionBudget, append_message, complete_turn, load_messages, respond_to_turn, start_turn
from autoad_researcher.reporting.review import create_proposal, record_review
from autoad_researcher.reporting.service import ReportRequestService
from autoad_researcher.reporting.store import ReportStore
from autoad_researcher.worker.main import _process_pending_jobs


def _ready_report(tmp_path: Path):
    run_dir = tmp_path / "run_report_collaboration"; run_dir.mkdir()
    session = ExperimentSessionStore().create_or_get(run_dir, task_ref="tasks/task.json", task_hash="e" * 64, execution_mode="approve_each_step")[0]
    result, _ = ReportRequestService().request(run_dir, session_id=session.session_id)
    for _ in range(5): _process_pending_jobs(run_dir)
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


def test_discussion_responder_uses_only_structured_output(monkeypatch, tmp_path: Path):
    run_dir, report_id = _ready_report(tmp_path)
    turn = start_turn(run_dir, report_id=report_id, request_id="turn_model", content="请解释当前证据")
    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", lambda *args, **kwargs: {"reply": '{"answer":"报告当前没有已登记的提升证据。","response_kind":"insufficient_evidence","evidence_ids":[],"unsupported_claims":["提升结论"]}', "error": ""})
    completed = respond_to_turn(run_dir, report_id=report_id, turn_id=turn.turn_id, api_key="test", provider_url="https://example.test", model="test")
    assert completed.status == "completed"
    assert completed.response is not None and completed.response.response_kind == "insufficient_evidence"


def test_discussion_budget_and_factual_evidence_requirements(monkeypatch, tmp_path: Path):
    run_dir, report_id = _ready_report(tmp_path)
    evidence_id = json.loads((run_dir / "reports" / report_id / "evidence_index.json").read_text())["entries"][0]["evidence_id"]
    turn = start_turn(run_dir, report_id=report_id, request_id="turn_budget", content="请核查证据")
    captured = {}

    def fake_call(*_args, **kwargs):
        captured.update(kwargs)
        return {"reply": '{"answer":"已登记的证据支持此解释。","response_kind":"verify","evidence_ids":["' + evidence_id + '"],"unsupported_claims":[]}', "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    completed = respond_to_turn(
        run_dir,
        report_id=report_id,
        turn_id=turn.turn_id,
        api_key="test",
        provider_url="https://example.test",
        model="test",
        budget=ReportDiscussionBudget(max_output_tokens=128, max_wall_time_seconds=12),
    )
    assert completed.status == "completed"
    assert captured["max_tokens"] == 128 and captured["timeout_s"] == 12

    missing = start_turn(run_dir, report_id=report_id, request_id="turn_missing_evidence", content="请解释")
    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", lambda *_args, **_kwargs: {"reply": '{"answer":"解释。","response_kind":"explain","evidence_ids":[],"unsupported_claims":[]}', "error": ""})
    assert respond_to_turn(run_dir, report_id=report_id, turn_id=missing.turn_id, api_key="test", provider_url="https://example.test", model="test").status == "failed"


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
