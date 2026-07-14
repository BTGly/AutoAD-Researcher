"""Shared synchronous runtime for OpenAI-compatible assistant model calls."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from email.utils import parsedate_to_datetime
from functools import wraps
from typing import Any, Literal, ParamSpec, TypeVar

import httpx


CallPriority = Literal["interactive", "routing", "contract", "background"]
P = ParamSpec("P")
R = TypeVar("R")


@dataclass(frozen=True)
class ConversationDeadline:
    """One monotonic deadline shared by every model phase in a chat turn."""

    started_at: float
    deadline_at: float

    @classmethod
    def start(cls, timeout_s: float | None = None) -> "ConversationDeadline":
        now = time.monotonic()
        budget = timeout_s if timeout_s is not None else _env_float(
            "AUTOAD_CONVERSATION_DEADLINE_SECONDS", 30.0, minimum=1.0
        )
        return cls(started_at=now, deadline_at=now + budget)

    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline_at - time.monotonic())


@dataclass(frozen=True)
class LLMCallRequest:
    api_key: str
    provider_base_url: str
    messages: list[dict[str, str]]
    model: str = "deepseek-chat"
    timeout_s: float = 60.0
    max_tokens: int = 2048
    temperature: float = 0.3
    priority: CallPriority = "contract"
    response_format_json: bool = False
    on_delta: Callable[[str], None] | None = None


@dataclass
class LLMCallResult:
    reply: str = ""
    error: str = ""
    provider_request_id: str = ""
    http_status: int | None = None
    error_type: str = ""
    queue_wait_ms: float = 0.0
    ttfb_ms: float | None = None
    first_token_ms: float | None = None
    total_latency_ms: float = 0.0
    retry_count: int = 0
    retry_after_ms: float | None = None
    circuit_breaker_state: str = "closed"
    fallback_reason: str = ""
    compatibility_reason: str = ""

    def as_public_dict(self) -> dict[str, Any]:
        runtime = asdict(self)
        reply = runtime.pop("reply")
        error = runtime.pop("error")
        return {"reply": reply, "error": error, "runtime": runtime}


@dataclass
class _CircuitState:
    consecutive_failures: int = 0
    opened_at: float | None = None
    half_open_probe_active: bool = False


class _ProviderState:
    def __init__(self, client: httpx.Client, max_inflight: int, reserved_interactive: int) -> None:
        self.client = client
        self.all_slots = threading.BoundedSemaphore(max_inflight)
        standard_slots = max(1, max_inflight - reserved_interactive)
        self.standard_slots = threading.BoundedSemaphore(standard_slots)
        self.circuit = _CircuitState()
        self.circuit_lock = threading.Lock()


_CURRENT_DEADLINE: ContextVar[ConversationDeadline | None] = ContextVar(
    "autoad_conversation_deadline", default=None
)


@contextmanager
def conversation_deadline_scope(timeout_s: float | None = None) -> Iterator[ConversationDeadline]:
    existing = _CURRENT_DEADLINE.get()
    if existing is not None:
        yield existing
        return
    deadline = ConversationDeadline.start(timeout_s)
    token = _CURRENT_DEADLINE.set(deadline)
    try:
        yield deadline
    finally:
        _CURRENT_DEADLINE.reset(token)


def with_conversation_deadline(func: Callable[P, R]) -> Callable[P, R]:
    """Wrap one synchronous orchestration entry point in the shared deadline."""

    @wraps(func)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
        with conversation_deadline_scope():
            return func(*args, **kwargs)

    return wrapped


def current_conversation_deadline() -> ConversationDeadline | None:
    return _CURRENT_DEADLINE.get()


class LLMCallBroker:
    """Connection-pooled, deadline-aware and concurrency-bounded model broker."""

    def __init__(self) -> None:
        self._states: dict[str, _ProviderState] = {}
        self._states_lock = threading.Lock()
        self._max_inflight = _env_int("AUTOAD_LLM_MAX_INFLIGHT_PER_PROVIDER", 2, minimum=1)
        self._reserved_interactive = min(
            _env_int("AUTOAD_LLM_RESERVED_INTERACTIVE_SLOTS", 1, minimum=0),
            max(0, self._max_inflight - 1),
        )
        self._failure_threshold = _env_int("AUTOAD_LLM_CIRCUIT_FAILURE_THRESHOLD", 3, minimum=1)
        self._cooldown_s = _env_float("AUTOAD_LLM_CIRCUIT_COOLDOWN_SECONDS", 60.0, minimum=0.01)

    def close(self) -> None:
        with self._states_lock:
            states = list(self._states.values())
            self._states.clear()
        for state in states:
            state.client.close()

    def call(self, request: LLMCallRequest) -> LLMCallResult:
        if current_conversation_deadline() is None:
            with conversation_deadline_scope(request.timeout_s):
                return self.call(request)
        started = time.monotonic()
        state = self._provider_state(request.provider_base_url)
        circuit_state, probe_allowed = self._enter_circuit(state)
        if not probe_allowed:
            return LLMCallResult(
                error="模型服务暂时不可用，请稍后重试。",
                error_type="circuit_open",
                total_latency_ms=_elapsed_ms(started),
                circuit_breaker_state=circuit_state,
                fallback_reason="provider_circuit_open",
            )

        queue_started = time.monotonic()
        acquired: list[threading.BoundedSemaphore] = []
        try:
            if request.priority != "interactive":
                if not self._acquire(state.standard_slots, request.timeout_s):
                    return self._queue_timeout_result(started, queue_started, state, probe_allowed)
                acquired.append(state.standard_slots)
            if not self._acquire(state.all_slots, request.timeout_s):
                return self._queue_timeout_result(started, queue_started, state, probe_allowed)
            acquired.append(state.all_slots)
            queue_wait_ms = _elapsed_ms(queue_started)
            result = self._call_with_retry(state, request, started)
            result.queue_wait_ms = queue_wait_ms
            result.total_latency_ms = _elapsed_ms(started)
            result.circuit_breaker_state = self._record_circuit_result(state, result, probe_allowed)
            return result
        finally:
            for semaphore in reversed(acquired):
                semaphore.release()

    def _provider_state(self, provider_base_url: str) -> _ProviderState:
        key = _provider_key(provider_base_url)
        with self._states_lock:
            state = self._states.get(key)
            if state is None:
                client = httpx.Client(
                    limits=httpx.Limits(
                        max_connections=max(8, self._max_inflight),
                        max_keepalive_connections=max(4, self._max_inflight),
                    )
                )
                state = _ProviderState(client, self._max_inflight, self._reserved_interactive)
                self._states[key] = state
            return state

    def _acquire(self, semaphore: threading.BoundedSemaphore, requested_timeout_s: float) -> bool:
        remaining = _remaining_budget(requested_timeout_s)
        return remaining > 0 and semaphore.acquire(timeout=remaining)

    def _queue_timeout_result(
        self,
        started: float,
        queue_started: float,
        state: _ProviderState,
        probe_allowed: bool,
    ) -> LLMCallResult:
        if probe_allowed:
            self._release_half_open_probe(state)
        return LLMCallResult(
            error="本轮处理时间已用尽，请重试。",
            error_type="queue_timeout",
            queue_wait_ms=_elapsed_ms(queue_started),
            total_latency_ms=_elapsed_ms(started),
            fallback_reason="conversation_deadline_exhausted",
        )

    def _call_with_retry(
        self,
        state: _ProviderState,
        request: LLMCallRequest,
        started: float,
    ) -> LLMCallResult:
        response_format_json = request.response_format_json
        retry_count = 0
        compatibility_reason = ""
        observed_retry_after_ms: float | None = None
        while True:
            timeout_s = _remaining_budget(request.timeout_s)
            if timeout_s <= 0:
                return LLMCallResult(
                    error="本轮处理时间已用尽，请重试。",
                    error_type="deadline_exceeded",
                    retry_count=retry_count,
                    total_latency_ms=_elapsed_ms(started),
                    fallback_reason="conversation_deadline_exhausted",
                    compatibility_reason=compatibility_reason,
                )
            result = self._send_once(
                state.client,
                request,
                timeout_s=timeout_s,
                response_format_json=response_format_json,
            )
            result.retry_count = retry_count
            result.compatibility_reason = compatibility_reason
            if observed_retry_after_ms is not None and result.retry_after_ms is None:
                result.retry_after_ms = observed_retry_after_ms
            if response_format_json and _response_format_is_unsupported(result):
                response_format_json = False
                compatibility_reason = "response_format_not_supported"
                continue
            if retry_count == 0 and _is_retryable(result):
                retry_count = 1
                observed_retry_after_ms = result.retry_after_ms
                delay_s = min((result.retry_after_ms or 0.0) / 1000.0, 2.0)
                if delay_s > 0:
                    remaining = _remaining_budget(request.timeout_s)
                    if delay_s >= remaining:
                        return result
                    time.sleep(delay_s)
                continue
            return result

    def _send_once(
        self,
        client: httpx.Client,
        request: LLMCallRequest,
        *,
        timeout_s: float,
        response_format_json: bool,
    ) -> LLMCallResult:
        url = _chat_completions_url(request.provider_base_url)
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": request.messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        if response_format_json:
            payload["response_format"] = {"type": "json_object"}
        if request.on_delta is not None:
            payload["stream"] = True
        headers = {
            "Authorization": f"Bearer {request.api_key}",
            "Content-Type": "application/json",
        }
        started = time.monotonic()
        try:
            with client.stream(
                "POST",
                url,
                json=payload,
                headers=headers,
                timeout=httpx.Timeout(timeout_s),
            ) as response:
                ttfb_ms = _elapsed_ms(started)
                request_id = _safe_request_id(response.headers.get("x-request-id", ""))
                retry_after_ms = _retry_after_ms(response.headers.get("retry-after"))
                if response.status_code != 200:
                    response.read()
                    unsupported_detail = _safe_response_format_detail(response, response_format_json)
                    return LLMCallResult(
                        error=f"模型服务返回 HTTP {response.status_code}。",
                        provider_request_id=request_id,
                        http_status=response.status_code,
                        error_type="http_error",
                        ttfb_ms=ttfb_ms,
                        retry_after_ms=retry_after_ms,
                        fallback_reason=unsupported_detail,
                    )
                if request.on_delta is not None:
                    return _consume_streaming_response(
                        response,
                        request.on_delta,
                        started=started,
                        ttfb_ms=ttfb_ms,
                        request_id=request_id,
                    )
                try:
                    response.read()
                    body = response.json()
                    content = body["choices"][0]["message"]["content"]
                    if not isinstance(content, str):
                        raise TypeError("message content is not text")
                except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
                    return LLMCallResult(
                        error="模型响应格式无法解析。",
                        provider_request_id=request_id,
                        http_status=200,
                        error_type="response_parse_error",
                        ttfb_ms=ttfb_ms,
                        fallback_reason="provider_response_invalid",
                    )
                return LLMCallResult(
                    reply=content.strip(),
                    provider_request_id=request_id,
                    http_status=200,
                    ttfb_ms=ttfb_ms,
                )
        except httpx.TimeoutException:
            return LLMCallResult(
                error="模型请求超时，请重试。",
                error_type="timeout",
                fallback_reason="provider_timeout",
            )
        except httpx.NetworkError:
            return LLMCallResult(
                error="无法连接模型服务，请检查网络后重试。",
                error_type="network_error",
                fallback_reason="provider_network_error",
            )
        except Exception:
            return LLMCallResult(
                error="模型服务调用失败，请稍后重试。",
                error_type="unexpected_error",
                fallback_reason="provider_unexpected_error",
            )

    def _enter_circuit(self, state: _ProviderState) -> tuple[str, bool]:
        now = time.monotonic()
        with state.circuit_lock:
            circuit = state.circuit
            if circuit.opened_at is None:
                return "closed", True
            if now - circuit.opened_at < self._cooldown_s:
                return "open", False
            if circuit.half_open_probe_active:
                return "half_open", False
            circuit.half_open_probe_active = True
            return "half_open", True

    def _record_circuit_result(
        self,
        state: _ProviderState,
        result: LLMCallResult,
        probe_allowed: bool,
    ) -> str:
        with state.circuit_lock:
            circuit = state.circuit
            if probe_allowed:
                circuit.half_open_probe_active = False
            if _counts_as_circuit_failure(result):
                circuit.consecutive_failures += 1
                if circuit.consecutive_failures >= self._failure_threshold:
                    circuit.opened_at = time.monotonic()
                    return "open"
                return "closed" if circuit.opened_at is None else "half_open"
            circuit.consecutive_failures = 0
            circuit.opened_at = None
            return "closed"

    @staticmethod
    def _release_half_open_probe(state: _ProviderState) -> None:
        with state.circuit_lock:
            state.circuit.half_open_probe_active = False


_BROKER: LLMCallBroker | None = None
_BROKER_LOCK = threading.Lock()


def get_llm_call_broker() -> LLMCallBroker:
    global _BROKER
    with _BROKER_LOCK:
        if _BROKER is None:
            _BROKER = LLMCallBroker()
        return _BROKER


def reset_llm_call_broker() -> None:
    """Close shared clients and reload runtime configuration on next use."""

    global _BROKER
    with _BROKER_LOCK:
        broker = _BROKER
        _BROKER = None
    if broker is not None:
        broker.close()


def runtime_trace_fields(result: dict[str, Any]) -> dict[str, Any]:
    runtime = result.get("runtime")
    if not isinstance(runtime, dict):
        return {}
    allowed = {
        "provider_request_id",
        "http_status",
        "error_type",
        "queue_wait_ms",
        "ttfb_ms",
        "first_token_ms",
        "total_latency_ms",
        "retry_count",
        "retry_after_ms",
        "circuit_breaker_state",
        "compatibility_reason",
    }
    fields = {key: runtime.get(key) for key in allowed if key in runtime}
    if "fallback_reason" in runtime:
        fields["provider_fallback_reason"] = runtime.get("fallback_reason")
    return fields


def _consume_streaming_response(
    response: httpx.Response,
    on_delta: Callable[[str], None],
    *,
    started: float,
    ttfb_ms: float,
    request_id: str,
) -> LLMCallResult:
    chunks: list[str] = []
    first_token_ms: float | None = None
    for line in response.iter_lines():
        data = _sse_data(line)
        if data is None:
            continue
        if data == "[DONE]":
            break
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            return LLMCallResult(
                error="模型流式响应格式无法解析。",
                provider_request_id=request_id,
                http_status=200,
                error_type="stream_parse_error",
                ttfb_ms=ttfb_ms,
                first_token_ms=first_token_ms,
                fallback_reason="provider_stream_invalid",
            )
        delta = _content_delta_from_stream_event(event)
        if delta is None:
            continue
        if first_token_ms is None:
            first_token_ms = _elapsed_ms(started)
        chunks.append(delta)
        on_delta(delta)
    return LLMCallResult(
        reply="".join(chunks).strip(),
        provider_request_id=request_id,
        http_status=200,
        ttfb_ms=ttfb_ms,
        first_token_ms=first_token_ms,
    )


def _remaining_budget(requested_timeout_s: float) -> float:
    deadline = current_conversation_deadline()
    if deadline is None:
        return max(0.0, requested_timeout_s)
    return max(0.0, min(requested_timeout_s, deadline.remaining_seconds()))


def _normalize_provider_base(provider_base_url: str) -> str:
    return provider_base_url.rstrip("/")


def _provider_key(provider_base_url: str) -> str:
    base = _normalize_provider_base(provider_base_url)
    return base.removesuffix("/v1")


def _chat_completions_url(provider_base_url: str) -> str:
    base = _normalize_provider_base(provider_base_url)
    return base + "/chat/completions" if base.endswith("/v1") else base + "/v1/chat/completions"


def _safe_response_format_detail(response: httpx.Response, requested: bool) -> str:
    if not requested or response.status_code != 400:
        return "provider_http_error"
    try:
        payload = response.json()
    except Exception:
        return "provider_http_error"
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return "provider_http_error"
    param = error.get("param")
    message = error.get("message")
    if param == "response_format" or (
        isinstance(message, str) and "response_format" in message
    ):
        return "response_format_not_supported"
    return "provider_http_error"


def _response_format_is_unsupported(result: LLMCallResult) -> bool:
    return result.fallback_reason == "response_format_not_supported"


def _is_retryable(result: LLMCallResult) -> bool:
    return result.error_type == "network_error" or result.http_status in {429, 502, 503, 504}


def _counts_as_circuit_failure(result: LLMCallResult) -> bool:
    return result.error_type in {"network_error", "timeout"} or result.http_status in {429, 502, 503, 504}


def _retry_after_ms(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value) * 1000.0)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(value)
            return max(0.0, (retry_at.timestamp() - time.time()) * 1000.0)
        except (TypeError, ValueError, OverflowError):
            return None


def _safe_request_id(value: str) -> str:
    candidate = value.strip()
    if re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", candidate):
        return candidate
    return ""


def _sse_data(line: str | bytes) -> str | None:
    if isinstance(line, bytes):
        line = line.decode("utf-8", errors="replace")
    stripped = line.strip()
    if not stripped or not stripped.startswith("data:"):
        return None
    return stripped.removeprefix("data:").strip()


def _content_delta_from_stream_event(event: dict[str, Any]) -> str | None:
    choices = event.get("choices")
    if not isinstance(choices, list):
        return None
    parts: list[str] = []
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            continue
        content = delta.get("content")
        if isinstance(content, str):
            parts.append(content)
    return "".join(parts) or None


def _elapsed_ms(started: float) -> float:
    return (time.monotonic() - started) * 1000.0


def _env_int(name: str, default: int, *, minimum: int) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


def _env_float(name: str, default: float, *, minimum: float) -> float:
    try:
        return max(minimum, float(os.environ.get(name, str(default))))
    except ValueError:
        return default
