from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from autoad_researcher.server.routes.chat import (
    _append_transcript,
    _assistant_progress_message,
    _load_transcript_tail,
    _run_sync_cancellation_safe,
    _single_chat_turn,
    _task_updated_message,
)


def test_v2_chat_transcript_tail_round_trips_recent_messages(tmp_path: Path):
    run_dir = tmp_path / "run_chat"
    for index in range(14):
        _append_transcript(run_dir, "user", f"user {index}")
        _append_transcript(run_dir, "assistant", f"assistant {index}")

    tail = _load_transcript_tail(run_dir, limit=5)

    assert tail == [
        {"role": "assistant", "content": "assistant 11"},
        {"role": "user", "content": "user 12"},
        {"role": "assistant", "content": "assistant 12"},
        {"role": "user", "content": "user 13"},
        {"role": "assistant", "content": "assistant 13"},
    ]


def test_same_run_rejects_overlapping_chat_turn_but_other_run_is_allowed():
    with _single_chat_turn("run_one"):
        with _single_chat_turn("run_two"):
            pass
        with pytest.raises(HTTPException) as exc_info:
            with _single_chat_turn("run_one"):
                pass

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "chat_turn_in_progress"
    with _single_chat_turn("run_one"):
        pass


def test_cancelled_http_task_keeps_same_run_guard_until_worker_thread_exits():
    import asyncio
    import threading

    entered = threading.Event()
    release = threading.Event()

    def blocked_worker():
        entered.set()
        release.wait(timeout=2)
        return "done"

    async def scenario():
        async def guarded_call():
            with _single_chat_turn("run_cancelled"):
                return await _run_sync_cancellation_safe(blocked_worker)

        task = asyncio.create_task(guarded_call())
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        await asyncio.sleep(0.01)
        assert task.done() is False
        with pytest.raises(HTTPException):
            with _single_chat_turn("run_cancelled"):
                pass
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_progress_and_task_updated_message_shapes_are_user_facing():
    assert _assistant_progress_message("assistant-1", "正在理解你的任务……") == {
        "type": "assistant.progress",
        "message_id": "assistant-1",
        "content": "正在理解你的任务……",
    }


def test_validated_route_updates_placeholder_without_a_naming_model_call(tmp_path: Path):
    from datetime import datetime, timezone

    from autoad_researcher.assistant.v2.intent_contract import ResearchIntentContract
    from autoad_researcher.assistant.v2.orchestrator import _maybe_update_task_profile
    from autoad_researcher.assistant.v2.turn_gate import TurnGateDecision
    from autoad_researcher.task_workspace.task_profile import create_task_profile, load_task_profile

    run_dir = tmp_path / "run_route_name"
    run_dir.mkdir()
    create_task_profile(
        run_dir=run_dir,
        run_id=run_dir.name,
        task_title=None,
        created_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
    )
    decision = TurnGateDecision(
        turn_type="contract_update",
        contract_action="update_contract",
        contract_update_allowed=True,
        need_discovery_allowed=True,
        save_draft_allowed=True,
        task_profile_proposal="empirical_model_research",
        task_profile_evidence="PatchCore",
        suggested_task_title="PatchCore MVTec AUROC优化",
        suggested_task_summary="提升 MVTec AD 的图像级 AUROC。",
    )
    callback_payloads: list[dict] = []

    payload = _maybe_update_task_profile(
        run_dir=run_dir,
        turn_decision=decision,
        contract=ResearchIntentContract(
            run_id=run_dir.name,
            research_goal="提升图像级 AUROC",
            baseline="PatchCore",
            dataset="MVTec AD",
            primary_metrics=["image_level_auroc"],
        ),
        on_task_updated=callback_payloads.append,
    )

    assert payload["task_title"] == "PatchCore MVTec AUROC优化"
    assert payload["task_source"] == "router_suggested"
    assert callback_payloads == [payload]
    persisted = load_task_profile(run_dir)
    assert persisted is not None
    assert persisted.run_id == run_dir.name
    assert persisted.task_title == payload["task_title"]


def test_ordinary_chat_does_not_name_placeholder(tmp_path: Path):
    from datetime import datetime, timezone

    from autoad_researcher.assistant.v2.intent_contract import ResearchIntentContract
    from autoad_researcher.assistant.v2.orchestrator import _maybe_update_task_profile
    from autoad_researcher.assistant.v2.turn_gate import TurnGateDecision
    from autoad_researcher.task_workspace.task_profile import create_task_profile, load_task_profile

    run_dir = tmp_path / "run_chat_name"
    run_dir.mkdir()
    create_task_profile(
        run_dir=run_dir,
        run_id=run_dir.name,
        task_title=None,
        created_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
    )
    payload = _maybe_update_task_profile(
        run_dir=run_dir,
        turn_decision=TurnGateDecision(
            turn_type="ordinary_chat",
            contract_action="answer_without_contract_update",
            contract_update_allowed=False,
            need_discovery_allowed=False,
            save_draft_allowed=False,
            suggested_task_title="不应采用的闲聊标题",
        ),
        contract=ResearchIntentContract(run_id=run_dir.name),
        on_task_updated=None,
    )

    assert payload == {}
    persisted = load_task_profile(run_dir)
    assert persisted is not None
    assert persisted.task_title == "未命名研究任务"
    assert _task_updated_message({
        "run_id": "run_one",
        "task_title": "PatchCore MVTec AUROC优化",
        "task_summary": "提升 image AUROC。",
        "task_source": "router_suggested",
        "updated_at": "2026-07-14T00:00:00+00:00",
    }) == {
        "type": "task.updated",
        "run_id": "run_one",
        "task_title": "PatchCore MVTec AUROC优化",
        "task_summary": "提升 image AUROC。",
        "task_source": "router_suggested",
        "updated_at": "2026-07-14T00:00:00+00:00",
    }


def test_chat_route_forwards_selected_model_to_orchestrator(tmp_path: Path, monkeypatch):
    import asyncio
    import time

    from starlette.requests import Request

    from autoad_researcher.assistant.v2.orchestrator import OrchestratorResult
    from autoad_researcher.server.models import ChatRequest
    from autoad_researcher.server.routes import chat as chat_route

    captured: dict[str, object] = {}

    def fake_handle(run_dir: Path, **kwargs):
        captured["run_dir"] = run_dir
        captured.update(kwargs)
        return OrchestratorResult(reply="ok", reply_kind="answer")

    broadcasts: list[dict[str, object]] = []
    broadcast_times: list[float] = []

    async def fake_broadcast(run_id: str, message: dict[str, object]):
        broadcasts.append(message)
        broadcast_times.append(time.perf_counter())

    monkeypatch.chdir(tmp_path)
    (tmp_path / "runs" / "run_model_forward").mkdir(parents=True)
    monkeypatch.setattr(chat_route.ResearchOrchestratorV2, "handle", staticmethod(fake_handle))
    monkeypatch.setattr(chat_route.manager, "broadcast", fake_broadcast)
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/chat/send",
        "headers": [
            (b"x-autoad-api-key", b"sk-test"),
            (b"x-autoad-base-url", b"https://provider.test"),
            (b"x-autoad-model", b"selected-model"),
        ],
        "query_string": b"",
    })

    started = time.perf_counter()
    response = asyncio.run(chat_route.chat_send(
        ChatRequest(user_input="你好", run_id="run_model_forward"),
        request,
    ))

    assert response.reply == "ok"
    assert captured["model"] == "selected-model"
    assert captured["provider_url"] == "https://provider.test"
    assert callable(captured["on_progress"])
    assert callable(captured["on_task_updated"])
    assert broadcasts[0] == {
        "type": "assistant.progress",
        "message_id": broadcasts[0]["message_id"],
        "content": "正在理解你的任务……",
    }
    assert broadcast_times[0] - started < 0.3
    transcript = _load_transcript_tail(tmp_path / "runs" / "run_model_forward")
    assert transcript == [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "content": "ok"},
    ]
