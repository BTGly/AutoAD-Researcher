import json
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.reporting.discussion import DiscussionCapacityBusy, DiscussionResponse, DiscussionTurn, ReportDiscussionBudget, _recent_history, _respond_with_slot, append_message, complete_turn, load_messages, load_turns, respond_to_turn, start_turn
from autoad_researcher.reporting.review import create_proposal, record_review
from autoad_researcher.reporting.service import ReportRequestService
from autoad_researcher.reporting.store import ReportStore
from autoad_researcher.server.routes import report_collaboration as report_route
from autoad_researcher.worker.main import _process_pending_jobs
from autoad_researcher.assistant import llm_runtime
from autoad_researcher.assistant.llm_runtime import reset_llm_call_broker


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


def test_capacity_busy_keeps_pending_turn_for_same_request_retry(monkeypatch, tmp_path: Path):
    run_dir, report_id = _ready_report(tmp_path)
    turn = start_turn(run_dir, report_id=report_id, request_id="turn_capacity", content="请解释当前证据")
    slot_available = False
    monkeypatch.setattr(
        "autoad_researcher.reporting.discussion._try_response_slot",
        lambda *_args: slot_available,
    )

    with pytest.raises(DiscussionCapacityBusy) as excinfo:
        respond_to_turn(
            run_dir,
            report_id=report_id,
            turn_id=turn.turn_id,
            api_key="test",
            provider_url="https://example.test",
            model="test",
        )

    assert excinfo.value.turn_id == turn.turn_id
    assert load_turns(run_dir, report_id=report_id)[-1].status == "pending"
    replay = start_turn(run_dir, report_id=report_id, request_id="turn_capacity", content="请解释当前证据")
    assert replay.turn_id == turn.turn_id

    slot_available = True
    monkeypatch.setattr(
        "autoad_researcher.ui.chat_client.call_research_chat",
        lambda *_args, **_kwargs: {
            "reply": '{"answer":"当前没有可核验的提升结论。","response_kind":"insufficient_evidence","evidence_ids":[],"unsupported_claims":[]}',
            "error": "",
        },
    )
    completed = respond_to_turn(
        run_dir,
        report_id=report_id,
        turn_id=replay.turn_id,
        api_key="test",
        provider_url="https://example.test",
        model="test",
    )
    assert completed.status == "completed"
    assert not (run_dir / "reports" / report_id / "discussion" / ".response.lock").exists()


def test_discussion_responder_repairs_plain_provider_text(monkeypatch, tmp_path: Path):
    run_dir, report_id = _ready_report(tmp_path)
    turn = start_turn(run_dir, report_id=report_id, request_id="turn_plain_text", content="请解释当前证据")
    calls = []

    def fake_call(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            return {"reply": "当前没有可核验的提升结论。", "error": ""}
        assert kwargs["response_format_json"] is True
        assert "DiscussionResponse JSON" in args[2][-1]["content"]
        return {"reply": '{"answer":"当前没有可核验的提升结论。","response_kind":"insufficient_evidence","evidence_ids":[],"unsupported_claims":[]}', "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    completed = respond_to_turn(run_dir, report_id=report_id, turn_id=turn.turn_id, api_key="test", provider_url="https://example.test", model="test")
    assert completed.status == "completed"
    assert len(calls) == 2


def test_discussion_responder_uses_native_tool_call_pairing(monkeypatch, tmp_path: Path):
    run_dir, report_id = _ready_report(tmp_path)
    turn = start_turn(run_dir, report_id=report_id, request_id="turn_native_tool", content="请读取报告摘要")
    calls = []

    def fake_call(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            assert kwargs["tools"]
            return {
                "reply": "",
                "reasoning": "需要读取冻结摘要。",
                "tool_calls": [{
                    "id": "call_digest",
                    "type": "function",
                    "function": {"name": "get_report_digest", "arguments": "{}"},
                }],
                "error": "",
            }
        messages = calls[1][0][2]
        assistant = next(item for item in messages if item.get("role") == "assistant" and item.get("tool_calls"))
        tool = next(item for item in messages if item.get("role") == "tool")
        assert assistant["reasoning_content"] == "需要读取冻结摘要。"
        assert tool["tool_call_id"] == "call_digest"
        return {"reply": '{"answer":"报告摘要已读取。","response_kind":"insufficient_evidence","evidence_ids":[],"unsupported_claims":[]}', "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    completed = respond_to_turn(run_dir, report_id=report_id, turn_id=turn.turn_id, api_key="test", provider_url="https://example.test", model="test")

    assert completed.status == "completed"
    assert len(calls) == 2


def test_discussion_native_tool_loop_uses_real_broker_facade(monkeypatch, tmp_path: Path):
    run_dir, report_id = _ready_report(tmp_path)
    turn = start_turn(run_dir, report_id=report_id, request_id="turn_broker_tool", content="请读取报告摘要")
    calls: list[dict] = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        if len(calls) == 1:
            return httpx.Response(200, json={
                "choices": [{
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": None,
                        "reasoning_content": "读取冻结摘要。",
                        "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "get_report_digest", "arguments": "{}"}}],
                    },
                }],
            })
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": '{"answer":"摘要已读取。","response_kind":"insufficient_evidence","evidence_ids":[],"unsupported_claims":[]}'}}],
        })

    transport = httpx.MockTransport(handler)
    client_type = httpx.Client

    def client_factory(**kwargs):
        kwargs.pop("limits", None)
        return client_type(transport=transport, **kwargs)

    monkeypatch.setattr(llm_runtime.httpx, "Client", client_factory)
    reset_llm_call_broker()
    try:
        completed = respond_to_turn(
            run_dir,
            report_id=report_id,
            turn_id=turn.turn_id,
            api_key="sk-test",
            provider_url="https://provider.test",
            model="deepseek-v4-flash",
        )
    finally:
        reset_llm_call_broker()

    assert completed.status == "completed"
    assert len(calls) == 2
    assert calls[0]["tools"]
    assert any(message.get("role") == "tool" and message.get("tool_call_id") == "call_1" for message in calls[1]["messages"])


@pytest.mark.asyncio
async def test_discussion_route_maps_failed_turn_to_bad_gateway(monkeypatch, tmp_path: Path):
    run_dir, report_id = _ready_report(tmp_path)
    monkeypatch.setattr(report_route, "RUNS_ROOT", str(tmp_path))

    def failed_response(run_dir, *, report_id, turn_id, **_kwargs):
        pending = load_turns(run_dir, report_id=report_id)[-1]
        return pending.model_copy(update={"status": "failed", "error": "模型服务返回 HTTP 400。"})

    monkeypatch.setattr(report_route, "respond_to_turn", failed_response)
    request = Request({
        "type": "http",
        "method": "POST",
        "path": f"/api/runs/{run_dir.name}/reports/{report_id}/discussion",
        "headers": [],
        "query_string": b"",
    })

    with pytest.raises(HTTPException) as excinfo:
        await report_route.post_discussion(
            run_dir.name,
            report_id,
            report_route.DiscussionRequest(request_id="route_failed", content="请解释"),
            request,
        )

    assert excinfo.value.status_code == 502
    assert excinfo.value.detail["status"] == "failed"


@pytest.mark.asyncio
async def test_discussion_route_maps_capacity_busy_to_retryable_429(monkeypatch, tmp_path: Path):
    run_dir, report_id = _ready_report(tmp_path)
    monkeypatch.setattr(report_route, "RUNS_ROOT", str(tmp_path))

    def busy_response(*_args, **_kwargs):
        raise DiscussionCapacityBusy("turn_busy")

    monkeypatch.setattr(report_route, "respond_to_turn", busy_response)
    request = Request({
        "type": "http",
        "method": "POST",
        "path": f"/api/runs/{run_dir.name}/reports/{report_id}/discussion",
        "headers": [],
        "query_string": b"",
    })

    with pytest.raises(HTTPException) as excinfo:
        await report_route.post_discussion(
            run_dir.name,
            report_id,
            report_route.DiscussionRequest(request_id="route_busy", content="请解释"),
            request,
        )

    assert excinfo.value.status_code == 429
    assert excinfo.value.headers["Retry-After"] == "2"
    assert excinfo.value.detail == {
        "code": "report_discussion_busy",
        "message": "报告讨论当前繁忙，请稍后重试。",
        "turn_id": "turn_busy",
        "status": "pending",
    }


def test_direct_discussion_response_verifies_turn_snapshot_before_model_call(monkeypatch, tmp_path: Path):
    run_dir, report_id = _ready_report(tmp_path)
    turn = start_turn(run_dir, report_id=report_id, request_id="turn_snapshot", content="请解释")
    mismatched = turn.model_copy(update={"snapshot_content_sha256": "f" * 64})
    monkeypatch.setattr(
        "autoad_researcher.ui.chat_client.call_research_chat",
        lambda *_args, **_kwargs: pytest.fail("model must not receive a mismatched report context"),
    )

    with pytest.raises(ValueError, match="snapshot identity conflicts with manifest"):
        _respond_with_slot(
            run_dir,
            report_id=report_id,
            turn=mismatched,
            api_key="test",
            provider_url="https://example.test",
            model="test",
        )


def test_discussion_response_receives_completed_history(monkeypatch, tmp_path: Path):
    run_dir, report_id = _ready_report(tmp_path)
    earlier = start_turn(run_dir, report_id=report_id, request_id="turn_earlier", content="刚才的实验")
    complete_turn(run_dir, report_id=report_id, turn_id=earlier.turn_id, response=DiscussionResponse(answer="先前回答", response_kind="insufficient_evidence"))
    current = start_turn(run_dir, report_id=report_id, request_id="turn_current", content="继续解释")
    captured = {}

    def fake_call(*args, **_kwargs):
        captured["messages"] = args[2]
        return {"reply": '{"answer":"上下文已加载。","response_kind":"insufficient_evidence","evidence_ids":[],"unsupported_claims":[]}', "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    assert respond_to_turn(run_dir, report_id=report_id, turn_id=current.turn_id, api_key="test", provider_url="https://example.test", model="test").status == "completed"
    contents = [item["content"] for item in captured["messages"]]
    assert "刚才的实验" in contents and "先前回答" in contents and contents[-1] == "继续解释"
    history = _recent_history(load_turns(run_dir, report_id=report_id), current_turn_id="missing")
    assert [item["content"] for item in history] == ["刚才的实验", "先前回答", "继续解释", "上下文已加载。"]


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
        budget=ReportDiscussionBudget(max_wall_time_seconds=12),
    )
    assert completed.status == "completed"
    assert "max_tokens" not in captured and captured["timeout_s"] == 12

    missing = start_turn(run_dir, report_id=report_id, request_id="turn_missing_evidence", content="请解释")
    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", lambda *_args, **_kwargs: {"reply": '{"answer":"解释。","response_kind":"explain","evidence_ids":[],"unsupported_claims":[]}', "error": ""})
    assert respond_to_turn(run_dir, report_id=report_id, turn_id=missing.turn_id, api_key="test", provider_url="https://example.test", model="test").status == "failed"


def test_discussion_compresses_old_turns_only_at_real_context_boundary(tmp_path: Path):
    turns = [
        DiscussionTurn(
            turn_id=f"turn_{index}", request_id=f"request_{index}", report_id="report_1",
            snapshot_content_sha256="a" * 64, user_message="用户问题 " + ("x" * 200),
            response=DiscussionResponse(answer="回答 " + ("y" * 200), response_kind="insufficient_evidence"),
            status="completed", created_at=f"2026-01-01T00:00:0{index}+00:00",
        )
        for index in range(2)
    ]
    compressed = _recent_history(turns, current_turn_id="missing", context_window=1)
    assert all(item["role"] == "system" for item in compressed)
    assert all("compressed_turn" in item["content"] for item in compressed)
    assert "用户问题" not in compressed[0]["content"]


def test_discussion_rejects_invalid_second_native_tool_response(monkeypatch, tmp_path: Path):
    run_dir, report_id = _ready_report(tmp_path)
    turn = start_turn(run_dir, report_id=report_id, request_id="turn_repeated_tool", content="请核查")
    calls = 0

    def fake_call(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "reply": "",
                "tool_calls": [{
                    "id": "call_digest",
                    "type": "function",
                    "function": {"name": "get_report_digest", "arguments": "{}"},
                }],
                "error": "",
            }
        return {"reply": '{"tool_calls":[{"name":"get_report_digest","arguments":{}}]}', "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    completed = respond_to_turn(run_dir, report_id=report_id, turn_id=turn.turn_id, api_key="test", provider_url="https://example.test", model="test")
    assert completed.status == "failed"
    assert calls == 2
    assert "DiscussionResponse" in (completed.error or "")


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
