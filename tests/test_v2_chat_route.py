from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from autoad_researcher.server.routes.chat import (
    _append_transcript,
    _load_transcript_tail,
    _run_sync_cancellation_safe,
    _single_chat_turn,
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


def test_auto_name_task_uses_selected_model_and_persists_title(tmp_path: Path, monkeypatch):
    import asyncio

    from autoad_researcher.server.routes import chat as chat_route
    from autoad_researcher.task_workspace.task_profile import (
        TaskProfile,
        create_task_profile,
        load_task_profile,
    )

    run_dir = tmp_path / "run_auto_name"
    create_task_profile(
        run_dir=run_dir,
        run_id=run_dir.name,
        task_title=None,
        created_at=chat_route.datetime(2026, 7, 13, tzinfo=chat_route.timezone.utc),
    )
    captured: dict[str, str] = {}

    def fake_generate(run_dir_arg, api_key, provider_url, user_input, model):
        captured.update({
            "run_id": run_dir_arg.name,
            "api_key": api_key,
            "provider_url": provider_url,
            "user_input": user_input,
            "model": model,
        })
        return TaskProfile(
            run_id=run_dir_arg.name,
            task_title="PatchCore 指标优化",
            task_summary="在 MVTec AD 上提升 image-level AUROC。",
            source="llm_first_user_instruction",
        )

    monkeypatch.setattr(chat_route, "generate_task_profile_from_first_message", fake_generate)
    applied = asyncio.run(chat_route._maybe_auto_name_task(
        run_dir=run_dir,
        user_input="我想提升 PatchCore 的 image-level AUROC",
        eligible=True,
        api_key="sk-test",
        provider_url="https://provider.test",
        model="selected-model",
    ))

    assert applied is True
    assert captured["model"] == "selected-model"
    assert captured["user_input"] == "我想提升 PatchCore 的 image-level AUROC"
    assert load_task_profile(run_dir).task_title == "PatchCore 指标优化"


def test_chat_route_forwards_selected_model_to_orchestrator(tmp_path: Path, monkeypatch):
    import asyncio

    from starlette.requests import Request

    from autoad_researcher.assistant.v2.orchestrator import OrchestratorResult
    from autoad_researcher.server.models import ChatRequest
    from autoad_researcher.server.routes import chat as chat_route

    captured: dict[str, object] = {}

    def fake_handle(run_dir: Path, **kwargs):
        captured["run_dir"] = run_dir
        captured.update(kwargs)
        return OrchestratorResult(reply="ok", reply_kind="answer")

    async def fake_broadcast(run_id: str, message: dict[str, str]):
        return None

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

    response = asyncio.run(chat_route.chat_send(
        ChatRequest(user_input="你好", run_id="run_model_forward"),
        request,
    ))

    assert response.reply == "ok"
    assert captured["model"] == "selected-model"
    assert captured["provider_url"] == "https://provider.test"
