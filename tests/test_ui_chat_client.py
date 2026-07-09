"""Tests for chat_client.py and chat_prompts.py."""

import json
from unittest.mock import patch

import httpx
import pytest

from autoad_researcher.ui.chat_client import call_research_chat
from autoad_researcher.ui.chat_prompts import MODE_PROMPTS

# ── chat_prompts ────────────────────────────────────────────────────────


def test_prompts_have_three_modes():
    assert set(MODE_PROMPTS.keys()) == {
        "intent_clarification", "run_explanation", "next_experiment",
    }


def test_prompts_contain_safety_rules():
    for name, prompt in MODE_PROMPTS.items():
        assert "禁止" in prompt or "不" in prompt, f"{name}: missing safety rules"


def test_prompts_reasonable_length():
    for name, prompt in MODE_PROMPTS.items():
        assert 500 < len(prompt) < 4000, f"{name}: {len(prompt)} chars"


# ── chat_client ─────────────────────────────────────────────────────────


def _mock_response(status=200, body=None):
    """Create a mock httpx.Response."""
    if body is None:
        body = {"choices": [{"message": {"content": "助手回复"}}]}

    class MockResponse:
        def __init__(self):
            self.status_code = status
            self._body = body

        def json(self):
            return self._body

    return MockResponse()


def test_client_returns_reply_on_200():
    with patch("httpx.post", return_value=_mock_response(200)):
        result = call_research_chat("sk-test", "https://test.api", [{"role": "user", "content": "hello"}])
        assert result["reply"] == "助手回复"
        assert result["error"] == ""


def test_client_returns_error_on_403():
    with patch("httpx.post", return_value=_mock_response(403, {"error": {"message": "forbidden"}})):
        result = call_research_chat("sk-bad", "https://test.api", [{"role": "user", "content": "hello"}])
        assert result["reply"] == ""
        assert "403" in result["error"]


def test_client_handles_non_json_response():
    class BadResponse:
        status_code = 200

        def json(self):
            raise ValueError("not json")

    with patch("httpx.post", return_value=BadResponse()):
        result = call_research_chat("sk-test", "https://test.api", [{"role": "user", "content": "hello"}])
        assert "解析响应" in result["error"]


def test_client_handles_timeout():
    with patch("httpx.post", side_effect=httpx.TimeoutException("timeout")):
        result = call_research_chat("sk-test", "https://test.api", [{"role": "user", "content": "hello"}])
        assert "超时" in result["error"]


def test_client_never_returns_api_key():
    with patch("httpx.post", return_value=_mock_response(200)):
        result = call_research_chat("sk-secret-key-123", "https://test.api", [{"role": "user", "content": "hello"}])
        reply_str = result["reply"] + result["error"]
        assert "sk-secret-key-123" not in reply_str


def test_client_streams_openai_compatible_sse():
    captured: dict[str, object] = {}

    class MockStreamResponse:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def iter_lines(self):
            yield "data: " + json.dumps({"choices": [{"delta": {"content": "助"}}]})
            yield "data: " + json.dumps({"choices": [{"delta": {"content": "手回复"}}]})
            yield "data: [DONE]"

    def fake_stream(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["json"] = kwargs["json"]
        return MockStreamResponse()

    deltas: list[str] = []
    with patch("httpx.stream", side_effect=fake_stream):
        result = call_research_chat(
            "sk-test",
            "https://test.api",
            [{"role": "user", "content": "hello"}],
            on_delta=deltas.append,
        )

    assert captured["method"] == "POST"
    assert captured["url"] == "https://test.api/v1/chat/completions"
    assert captured["json"]["stream"] is True
    assert deltas == ["助", "手回复"]
    assert result == {"reply": "助手回复", "error": ""}
