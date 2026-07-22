from __future__ import annotations

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx
import pytest

from autoad_researcher.assistant import llm_runtime
from autoad_researcher.assistant.llm_runtime import (
    LLMCallRequest,
    get_llm_call_broker,
    reset_llm_call_broker,
)

_HTTPX_CLIENT = httpx.Client


@pytest.fixture(autouse=True)
def _isolated_runtime(monkeypatch):
    for name in (
        "AUTOAD_LLM_MAX_INFLIGHT_PER_PROVIDER",
        "AUTOAD_LLM_RESERVED_INTERACTIVE_SLOTS",
        "AUTOAD_LLM_CIRCUIT_FAILURE_THRESHOLD",
        "AUTOAD_LLM_CIRCUIT_COOLDOWN_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    reset_llm_call_broker()
    yield
    reset_llm_call_broker()


def _install_transport(monkeypatch, handler, *, factory_calls: list[int] | None = None):
    transport = httpx.MockTransport(handler)

    def client_factory(**kwargs):
        if factory_calls is not None:
            factory_calls.append(1)
        kwargs.pop("limits", None)
        return _HTTPX_CLIENT(transport=transport, **kwargs)

    monkeypatch.setattr(llm_runtime.httpx, "Client", client_factory)


def _request(*, priority="interactive", timeout_s=2.0) -> LLMCallRequest:
    return LLMCallRequest(
        api_key="sk-test",
        provider_base_url="https://provider.test",
        messages=[{"role": "user", "content": "hello"}],
        priority=priority,
        timeout_s=timeout_s,
    )


def _completion() -> httpx.Response:
    return httpx.Response(200, json={
        "choices": [{"message": {"content": "ok"}}],
    })


def test_provider_client_is_reused(monkeypatch):
    factory_calls: list[int] = []
    _install_transport(monkeypatch, lambda request: _completion(), factory_calls=factory_calls)
    broker = get_llm_call_broker()

    assert broker.call(_request()).reply == "ok"
    assert broker.call(replace(
        _request(), provider_base_url="https://provider.test/v1"
    )).reply == "ok"
    assert len(factory_calls) == 1


def test_provider_inflight_calls_do_not_exceed_configured_limit(monkeypatch):
    monkeypatch.setenv("AUTOAD_LLM_MAX_INFLIGHT_PER_PROVIDER", "2")
    reset_llm_call_broker()
    release = threading.Event()
    two_entered = threading.Event()
    guard = threading.Lock()
    active = 0
    maximum = 0

    def handler(request):
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
            if active == 2:
                two_entered.set()
        release.wait(timeout=2)
        with guard:
            active -= 1
        return _completion()

    _install_transport(monkeypatch, handler)
    broker = get_llm_call_broker()
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(broker.call, _request()) for _ in range(3)]
        assert two_entered.wait(timeout=1)
        time.sleep(0.03)
        assert maximum == 2
        release.set()
        assert [future.result(timeout=2).reply for future in futures] == ["ok", "ok", "ok"]


def test_reserved_interactive_slot_is_not_consumed_by_routing(monkeypatch):
    monkeypatch.setenv("AUTOAD_LLM_MAX_INFLIGHT_PER_PROVIDER", "2")
    monkeypatch.setenv("AUTOAD_LLM_RESERVED_INTERACTIVE_SLOTS", "1")
    reset_llm_call_broker()
    release = threading.Event()
    entered_priorities: list[str] = []
    guard = threading.Lock()

    # The transport cannot observe broker priority, so use different messages
    # and inspect their serialized body instead of adding production headers.
    def body_handler(request):
        body = request.content.decode("utf-8")
        with guard:
            entered_priorities.append("interactive" if "interactive" in body else "routing")
        release.wait(timeout=2)
        return _completion()

    _install_transport(monkeypatch, body_handler)
    broker = get_llm_call_broker()
    routing = replace(
        _request(priority="routing"),
        messages=[{"role": "user", "content": "routing"}],
    )
    interactive = replace(
        _request(priority="interactive"),
        messages=[{"role": "user", "content": "interactive"}],
    )
    with ThreadPoolExecutor(max_workers=3) as executor:
        first_routing = executor.submit(broker.call, routing)
        deadline = time.monotonic() + 1
        while entered_priorities != ["routing"] and time.monotonic() < deadline:
            time.sleep(0.005)
        second_routing = executor.submit(broker.call, routing)
        interactive_call = executor.submit(broker.call, interactive)
        deadline = time.monotonic() + 1
        while len(entered_priorities) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert entered_priorities == ["routing", "interactive"]
        release.set()
        assert first_routing.result(timeout=2).reply == "ok"
        assert second_routing.result(timeout=2).reply == "ok"
        assert interactive_call.result(timeout=2).reply == "ok"


def test_queue_wait_uses_the_individual_request_timeout(monkeypatch):
    monkeypatch.setenv("AUTOAD_LLM_MAX_INFLIGHT_PER_PROVIDER", "1")
    reset_llm_call_broker()
    entered = threading.Event()
    release = threading.Event()

    def handler(request):
        entered.set()
        release.wait(timeout=2)
        return _completion()

    _install_transport(monkeypatch, handler)
    broker = get_llm_call_broker()
    with ThreadPoolExecutor(max_workers=1) as executor:
        active = executor.submit(broker.call, _request(timeout_s=2))
        assert entered.wait(timeout=1)
        started = time.monotonic()
        queued = broker.call(_request(timeout_s=0.05))
        elapsed = time.monotonic() - started
        release.set()
        assert active.result(timeout=2).reply == "ok"

    assert queued.error_type == "queue_timeout"
    assert queued.fallback_reason == "provider_queue_timeout"
    assert elapsed < 0.2


def test_default_request_omits_generation_limits(monkeypatch):
    captured: dict[str, object] = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return _completion()

    _install_transport(monkeypatch, handler)
    result = get_llm_call_broker().call(_request())

    assert result.reply == "ok"
    assert captured == {
        "model": "deepseek-v4-flash",
        "messages": [{"role": "user", "content": "hello"}],
    }


def test_deepseek_reasoning_route_and_usage_are_preserved(monkeypatch):
    def handler(request):
        body = json.loads(request.content)
        assert body["thinking"] == {"type": "enabled"}
        assert body["reasoning_effort"] == "max"
        assert body["tools"] == [{"type": "function", "function": {"name": "inspect"}}]
        return httpx.Response(200, json={
            "choices": [{
                "message": {
                    "content": "answer",
                    "reasoning_content": "internal reasoning",
                    "tool_calls": [{"id": "call_1", "type": "function"}],
                },
                "finish_reason": "length",
            }],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
                "prompt_cache_hit_tokens": 4,
                "prompt_tokens_details": {"cached_tokens": 4},
                "completion_tokens_details": {"reasoning_tokens": 12},
            },
        })

    _install_transport(monkeypatch, handler)
    result = get_llm_call_broker().call(replace(
        _request(),
        model="deepseek-v4-pro",
        thinking_type="enabled",
        reasoning_effort="max",
        tools=[{"type": "function", "function": {"name": "inspect"}}],
    ))

    assert result.reply == "answer"
    assert result.reasoning == "internal reasoning"
    assert result.finish_reason == "length"
    assert result.usage == {
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "total_tokens": 30,
        "prompt_cache_hit_tokens": 4,
        "cached_tokens": 4,
        "reasoning_tokens": 12,
    }
    assert result.tool_calls == [{"id": "call_1", "type": "function"}]


def test_streaming_preserves_reasoning_and_tool_call_fragments(monkeypatch):
    content = "\n".join([
        'data: {"choices":[{"delta":{"reasoning_content":"think "}}]}',
        'data: {"choices":[{"delta":{"reasoning_content":"more","tool_calls":[{"index":0,"id":"call_1","function":{"name":"inspect","arguments":"{\\"path\\":"}}]}}]}',
        'data: {"choices":[{"delta":{"content":"answer","tool_calls":[{"index":0,"function":{"arguments":"\\\"x\\\"}"}}]},"finish_reason":"tool_calls"}],"usage":{"prompt_tokens":3,"completion_tokens":5,"total_tokens":8}}',
        "data: [DONE]",
        "",
    ]).encode()

    _install_transport(monkeypatch, lambda request: httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=content,
    ))
    visible: list[str] = []
    reasoning: list[str] = []
    result = get_llm_call_broker().call(replace(
        _request(),
        on_delta=visible.append,
        on_reasoning_delta=reasoning.append,
    ))

    assert result.reply == "answer"
    assert visible == ["answer"]
    assert reasoning == ["think ", "more"]
    assert result.finish_reason == "tool_calls"
    assert result.usage == {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8}
    assert result.tool_calls == [{
        "index": 0,
        "id": "call_1",
        "type": "function",
        "function": {"name": "inspect", "arguments": '{"path":"x"}'},
    }]


def test_only_recoverable_network_failure_is_retried_once(monkeypatch):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("private connect failure", request=request)
        return _completion()

    _install_transport(monkeypatch, handler)
    result = get_llm_call_broker().call(_request())

    assert result.reply == "ok"
    assert result.retry_count == 1
    assert calls == 2


def test_provider_schema_shape_error_is_not_retried(monkeypatch):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"unexpected": True})

    _install_transport(monkeypatch, handler)
    result = get_llm_call_broker().call(_request())

    assert result.error_type == "response_parse_error"
    assert result.retry_count == 0
    assert calls == 1


def test_circuit_opens_after_three_failures_and_half_open_probe_recovers(monkeypatch):
    monkeypatch.setenv("AUTOAD_LLM_CIRCUIT_FAILURE_THRESHOLD", "3")
    monkeypatch.setenv("AUTOAD_LLM_CIRCUIT_COOLDOWN_SECONDS", "0.01")
    reset_llm_call_broker()
    should_fail = True
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if should_fail:
            raise httpx.ReadTimeout("private timeout", request=request)
        return _completion()

    _install_transport(monkeypatch, handler)
    broker = get_llm_call_broker()
    results = [broker.call(_request()) for _ in range(3)]
    blocked = broker.call(_request())

    assert results[-1].circuit_breaker_state == "open"
    assert blocked.error_type == "circuit_open"
    assert calls == 3

    should_fail = False
    time.sleep(0.02)
    recovered = broker.call(_request())
    assert recovered.reply == "ok"
    assert recovered.circuit_breaker_state == "closed"
    assert calls == 4
