"""Lightweight, redacted LLM trace writer for V2 assistant calls."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from autoad_researcher.assistant.v2.event_service import append_typed_event

TRACE_DIR = "assistant/llm_traces"
TRACE_INDEX = "llm_traces.jsonl"


def append_llm_trace(
    run_dir: Path | None,
    *,
    call_site: str,
    prompt_id: str,
    prompt_version: str,
    prompt_text: str,
    model: str,
    provider_url: str,
    messages: list[dict[str, Any]],
    raw_output: str = "",
    parse_status: str,
    schema_validation: str,
    schema_validation_errors: list[dict[str, str]] | None = None,
    fallback_reason: str = "",
    latency_ms: float | None = None,
    prompt_render_mode: str = "profile_only",
    include_global: bool = False,
    raw_output_ref: str | None = None,
    provider_request_id: str = "",
    http_status: int | None = None,
    error_type: str = "",
    queue_wait_ms: float | None = None,
    ttfb_ms: float | None = None,
    first_token_ms: float | None = None,
    total_latency_ms: float | None = None,
    retry_count: int = 0,
    retry_after_ms: float | None = None,
    circuit_breaker_state: str = "",
    provider_fallback_reason: str = "",
    compatibility_reason: str = "",
) -> dict[str, Any] | None:
    """Append one redacted trace record.

    The trace deliberately stores hashes and provider host only. It must not
    persist prompt text, message content, raw model output, API keys, or full
    provider URLs.
    """

    if run_dir is None:
        return None

    trace_id = _new_trace_id()
    record: dict[str, Any] = {
        "trace_id": trace_id,
        "run_id": run_dir.name,
        "call_site": call_site,
        "prompt_id": prompt_id,
        "prompt_version": prompt_version,
        "prompt_hash": hash_text(prompt_text),
        "prompt_render_mode": prompt_render_mode,
        "include_global": include_global,
        "model": model,
        "provider_url_host": provider_url_host(provider_url),
        "messages_hash": hash_messages(messages),
        "raw_output_ref": raw_output_ref,
        "raw_output_hash": hash_text(raw_output) if raw_output else "",
        "parse_status": parse_status,
        "schema_validation": schema_validation,
        "schema_validation_errors": schema_validation_errors or [],
        "fallback_reason": fallback_reason,
        "latency_ms": latency_ms,
        "provider_request_id": provider_request_id,
        "http_status": http_status,
        "error_type": error_type,
        "queue_wait_ms": queue_wait_ms,
        "ttfb_ms": ttfb_ms,
        "first_token_ms": first_token_ms,
        "total_latency_ms": total_latency_ms,
        "retry_count": retry_count,
        "retry_after_ms": retry_after_ms,
        "circuit_breaker_state": circuit_breaker_state,
        "provider_fallback_reason": provider_fallback_reason,
        "compatibility_reason": compatibility_reason,
        "created_at_ms": int(time.time() * 1000),
    }
    trace_dir = run_dir / TRACE_DIR
    trace_dir.mkdir(parents=True, exist_ok=True)
    index_path = trace_dir / TRACE_INDEX
    with index_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    _append_trace_events(run_dir, record)
    return record


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_messages(messages: list[dict[str, Any]]) -> str:
    serialized = json.dumps(messages, ensure_ascii=False, sort_keys=True, default=str)
    return hash_text(serialized)


def provider_url_host(provider_url: str) -> str:
    parsed = urlparse(provider_url)
    host = parsed.hostname or ""
    if not host and provider_url and "://" not in provider_url:
        host = provider_url.split("/", 1)[0].split("?", 1)[0].split("@")[-1]
    return host.lower()


def _new_trace_id() -> str:
    return f"trace_{int(time.time() * 1000)}_{uuid.uuid4().hex[:12]}"


def _append_trace_events(run_dir: Path, record: dict[str, Any]) -> None:
    summary = {
        "trace_id": record["trace_id"],
        "call_site": record["call_site"],
        "prompt_id": record["prompt_id"],
        "prompt_version": record["prompt_version"],
        "parse_status": record["parse_status"],
        "schema_validation": record["schema_validation"],
        "schema_validation_error_count": len(record.get("schema_validation_errors") or []),
        "fallback_reason": record["fallback_reason"],
        "latency_ms": record["latency_ms"],
        "http_status": record["http_status"],
        "error_type": record["error_type"],
        "queue_wait_ms": record["queue_wait_ms"],
        "ttfb_ms": record["ttfb_ms"],
        "first_token_ms": record["first_token_ms"],
        "total_latency_ms": record["total_latency_ms"],
        "retry_count": record["retry_count"],
        "circuit_breaker_state": record["circuit_breaker_state"],
        "provider_fallback_reason": record["provider_fallback_reason"],
        "compatibility_reason": record["compatibility_reason"],
    }
    append_typed_event(run_dir, "prompt.trace.created", summary)
    if record.get("schema_validation") == "error":
        append_typed_event(run_dir, "schema.validation.failed", summary)
