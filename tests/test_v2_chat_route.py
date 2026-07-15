from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from autoad_researcher.server.models import ChatRequest
from autoad_researcher.server.routes.chat import (
    _append_transcript,
    _load_transcript_tail,
    _resolve_message_id,
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


def test_chat_request_id_is_reused_as_assistant_message_id():
    request = ChatRequest(user_input="hello", request_id="client.request_1")

    assert _resolve_message_id(request.request_id) == "client.request_1"


def test_chat_request_id_falls_back_and_rejects_unsafe_characters():
    assert _resolve_message_id(None).startswith("assistant_")

    with pytest.raises(ValidationError):
        ChatRequest(user_input="hello", request_id="request id with spaces")
