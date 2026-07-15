"""Tests for the shared chat-client facade and legacy chat prompts."""

from __future__ import annotations

import json

import httpx
import pytest

from autoad_researcher.assistant import llm_runtime
from autoad_researcher.assistant.llm_runtime import reset_llm_call_broker
from autoad_researcher.ui.chat_client import call_research_chat
from autoad_researcher.ui.chat_prompts import MODE_PROMPTS

_HTTPX_CLIENT = httpx.Client


@pytest.fixture(autouse=True)
def _isolated_broker():
    reset_llm_call_broker()
    yield
    reset_llm_call_broker()


def _install_transport(monkeypatch, handler):
    transport = httpx.MockTransport(handler)

    def client_factory(**kwargs):
        kwargs.pop("limits", None)
        return _HTTPX_CLIENT(transport=transport, **kwargs)

    monkeypatch.setattr(llm_runtime.httpx, "Client", client_factory)


def _completion(content: str = "助手回复", *, status: int = 200, headers=None) -> httpx.Response:
    return httpx.Response(
        status,
        json={"choices": [{"message": {"content": content}}]},
        headers=headers,
    )


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


def test_client_returns_reply_and_runtime_metadata_on_200(monkeypatch):
    _install_transport(monkeypatch, lambda request: _completion(headers={"x-request-id": "req-1"}))

    result = call_research_chat(
        "sk-test", "https://test.api", [{"role": "user", "content": "hello"}]
    )

    assert result["reply"] == "助手回复"
    assert result["error"] == ""
    assert result["runtime"]["provider_request_id"] == "req-1"
    assert result["runtime"]["http_status"] == 200


def test_client_returns_safe_error_on_403(monkeypatch):
    _install_transport(monkeypatch, lambda request: httpx.Response(
        403, json={"error": {"message": "secret provider detail"}}
    ))

    result = call_research_chat(
        "sk-bad", "https://test.api", [{"role": "user", "content": "hello"}]
    )

    assert result["reply"] == ""
    assert "403" in result["error"]
    assert "secret provider detail" not in result["error"]


def test_client_handles_non_json_response(monkeypatch):
    _install_transport(monkeypatch, lambda request: httpx.Response(200, text="not json"))

    result = call_research_chat(
        "sk-test", "https://test.api", [{"role": "user", "content": "hello"}]
    )

    assert "解析" in result["error"]
    assert result["runtime"]["error_type"] == "response_parse_error"


def test_client_handles_timeout_without_exposing_exception(monkeypatch):
    def timeout(_request):
        raise httpx.ReadTimeout("private upstream timeout")

    _install_transport(monkeypatch, timeout)
    result = call_research_chat(
        "sk-test", "https://test.api", [{"role": "user", "content": "hello"}]
    )

    assert "超时" in result["error"]
    assert "private upstream" not in result["error"]
    assert result["runtime"]["retry_count"] == 0


def test_client_never_returns_api_key(monkeypatch):
    _install_transport(monkeypatch, lambda request: _completion())
    result = call_research_chat(
        "sk-secret-key-123",
        "https://test.api",
        [{"role": "user", "content": "hello"}],
    )

    assert "sk-secret-key-123" not in json.dumps(result, ensure_ascii=False)


def test_client_streams_openai_compatible_sse(monkeypatch):
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["json"] = json.loads(request.content)
        stream = "\n".join([
            "data: " + json.dumps({"choices": [{"delta": {"content": "助"}}]}),
            "data: " + json.dumps({"choices": [{"delta": {"content": "手回复"}}]}),
            "data: [DONE]",
            "",
        ])
        return httpx.Response(200, text=stream)

    _install_transport(monkeypatch, handler)
    deltas: list[str] = []
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
    assert result["reply"] == "助手回复"
    assert result["error"] == ""
    assert result["runtime"]["first_token_ms"] is not None


def test_json_response_format_falls_back_only_on_explicit_unsupported_error(monkeypatch):
    payloads: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        payloads.append(payload)
        if len(payloads) == 1:
            return httpx.Response(400, json={
                "error": {
                    "param": "response_format",
                    "message": "response_format is not supported",
                }
            })
        return _completion('{"reply_to_user":"ok"}')

    _install_transport(monkeypatch, handler)
    result = call_research_chat(
        "sk-test",
        "https://test.api",
        [{"role": "user", "content": "hello"}],
        response_format_json=True,
    )

    assert len(payloads) == 2
    assert "response_format" in payloads[0]
    assert "response_format" not in payloads[1]
    assert result["runtime"]["compatibility_reason"] == "response_format_not_supported"
