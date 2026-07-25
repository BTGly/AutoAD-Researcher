from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from autoad_researcher.server.models import ChatRequest
from autoad_researcher.server.routes.chat import (
    _append_transcript,
    _extract_api_headers,
    _extract_role_route,
    _load_transcript_tail,
    _resolve_message_id,
)
from autoad_researcher.assistant.v2.conversation_store import append_message
from autoad_researcher.server.routes.runs import get_run_transcript


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


def test_chat_request_id_is_reused_as_assistant_message_id():
    request = ChatRequest(user_input="hello", request_id="client.request_1")

    assert _resolve_message_id(request.request_id) == "client.request_1"


def test_chat_request_id_falls_back_and_rejects_unsafe_characters():
    assert _resolve_message_id(None).startswith("assistant_")

    with pytest.raises(ValidationError):
        ChatRequest(user_input="hello", request_id="request id with spaces")


def test_chat_headers_supply_the_dialogue_model():
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/chat/send",
        "headers": [
            (b"x-autoad-api-key", b"sk-test"),
            (b"x-autoad-base-url", b"https://example.test"),
            (b"x-autoad-model", b"configured-dialogue-model"),
        ],
    })

    assert _extract_api_headers(request) == (
        "sk-test",
        "https://example.test",
        "configured-dialogue-model",
    )


def test_chat_headers_supply_explicit_role_model_routes():
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/chat/send",
        "headers": [
            (b"x-autoad-dialogue-model", b"deepseek-v4-pro"),
            (b"x-autoad-report-model", b"deepseek-v4-flash"),
            (b"x-autoad-experiment-model", b"deepseek-v4-pro"),
        ],
    })

    assert _extract_role_route(request, "research_dialogue").model_id == "deepseek-v4-pro"
    assert _extract_role_route(request, "report").model_id == "deepseek-v4-flash"
    experiment = _extract_role_route(request, "experiment_agent")
    assert experiment.model_id == "deepseek-v4-pro" and experiment.thinking_type == "enabled"


@pytest.mark.asyncio
async def test_transcript_route_keeps_task_message_metadata(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "run_chat"
    run_dir.mkdir()
    monkeypatch.setattr("autoad_researcher.server.routes.runs.RUNS_ROOT", tmp_path)
    append_message(
        run_dir, role="assistant", content="**数据集清单生成已完成**",
        message_kind="task_result", notification_key="task-result:job_000001",
        job_id="job_000001", source_id="src_dataset",
        artifact_paths=["sources/src_dataset/dataset_manifest.json"],
    )

    transcript = await get_run_transcript("run_chat")

    assert transcript[0].message_kind == "task_result"
    assert transcript[0].job_id == "job_000001"
    assert transcript[0].artifact_paths == ["sources/src_dataset/dataset_manifest.json"]
