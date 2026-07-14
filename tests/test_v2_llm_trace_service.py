from __future__ import annotations

import json
from pathlib import Path

from autoad_researcher.assistant.v2.llm_trace_service import TRACE_DIR, TRACE_INDEX, append_llm_trace
from autoad_researcher.assistant.v2.source_action_planner import plan_source_actions


def _load_trace_records(run_dir: Path) -> list[dict]:
    path = run_dir / TRACE_DIR / TRACE_INDEX
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_llm_trace_writes_redacted_metadata(tmp_path: Path):
    run_dir = tmp_path / "run_trace"
    run_dir.mkdir()

    record = append_llm_trace(
        run_dir,
        call_site="reply_planner",
        prompt_id="assistant.v2.reply_plan.v1",
        prompt_version="v1",
        prompt_text="system prompt with private project context",
        model="deepseek-v4-flash",
        provider_url="https://user:sk-secret@example.test/v1/chat?api_key=sk-query-secret",
        messages=[
            {"role": "system", "content": "hidden system context"},
            {"role": "user", "content": "private user message sk-message-secret"},
        ],
        raw_output="raw model output with sk-output-secret",
        parse_status="error",
        schema_validation="skipped",
        schema_validation_errors=[{"loc": "contract_action", "type": "missing"}],
        fallback_reason="llm_error_or_non_json",
        latency_ms=12.5,
        provider_request_id="provider-request-1",
        http_status=503,
        error_type="http_error",
        queue_wait_ms=2.5,
        ttfb_ms=8.0,
        first_token_ms=9.0,
        total_latency_ms=12.5,
        retry_count=1,
        retry_after_ms=1000.0,
        circuit_breaker_state="closed",
        provider_fallback_reason="provider_http_error",
        compatibility_reason="response_format_not_supported",
    )

    assert record is not None
    assert record["prompt_id"] == "assistant.v2.reply_plan.v1"
    assert record["prompt_render_mode"] == "profile_only"
    assert record["include_global"] is False
    assert record["provider_url_host"] == "example.test"
    assert record["messages_hash"]
    assert record["prompt_hash"]
    assert record["raw_output_hash"]
    assert record["schema_validation_errors"] == [{"loc": "contract_action", "type": "missing"}]
    assert record["provider_request_id"] == "provider-request-1"
    assert record["http_status"] == 503
    assert record["queue_wait_ms"] == 2.5
    assert record["ttfb_ms"] == 8.0
    assert record["first_token_ms"] == 9.0
    assert record["total_latency_ms"] == 12.5
    assert record["retry_count"] == 1
    assert record["retry_after_ms"] == 1000.0
    assert record["provider_fallback_reason"] == "provider_http_error"

    raw_trace_text = (run_dir / TRACE_DIR / TRACE_INDEX).read_text(encoding="utf-8")
    assert "sk-secret" not in raw_trace_text
    assert "sk-query-secret" not in raw_trace_text
    assert "sk-message-secret" not in raw_trace_text
    assert "sk-output-secret" not in raw_trace_text
    assert "/v1/chat" not in raw_trace_text
    assert "hidden system context" not in raw_trace_text
    assert "private user message" not in raw_trace_text


def test_source_action_planner_trace_records_prompt_id_and_fallback(monkeypatch, tmp_path: Path):
    run_dir = tmp_path / "run_source_trace"
    run_dir.mkdir()

    def fake_call(api_key, provider_base_url, messages, **kwargs):
        return {
            "reply": "not json with sk-output-secret",
            "error": "",
            "runtime": {
                "provider_request_id": "req-source-1",
                "http_status": 200,
                "error_type": "",
                "queue_wait_ms": 3.0,
                "ttfb_ms": 11.0,
                "first_token_ms": None,
                "total_latency_ms": 15.0,
                "retry_count": 0,
                "retry_after_ms": None,
                "circuit_breaker_state": "closed",
                "fallback_reason": "",
                "compatibility_reason": "",
            },
        }

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    plan = plan_source_actions(
        run_dir=run_dir,
        user_input="帮我找一下 PatchCore 相关资料",
        api_key="sk-test",
        provider_url="https://user:sk-provider-secret@example.test/v1?api_key=sk-query-secret",
    )

    assert plan.actions == []
    assert "non-JSON" in plan.reason

    records = _load_trace_records(run_dir)
    assert len(records) == 1
    record = records[0]
    assert record["call_site"] == "source_action_planner"
    assert record["prompt_id"] == "assistant.v2.source_action_plan.v1"
    assert record["prompt_version"] == "v1"
    assert record["parse_status"] == "error"
    assert record["schema_validation"] == "skipped"
    assert record["fallback_reason"] == "llm_error_or_non_json"
    assert record["prompt_render_mode"] == "profile_only"
    assert record["include_global"] is False
    assert record["provider_url_host"] == "example.test"
    assert record["provider_request_id"] == "req-source-1"
    assert record["queue_wait_ms"] == 3.0
    assert record["ttfb_ms"] == 11.0
    assert record["total_latency_ms"] == 15.0

    raw_trace_text = (run_dir / TRACE_DIR / TRACE_INDEX).read_text(encoding="utf-8")
    assert "sk-provider-secret" not in raw_trace_text
    assert "sk-query-secret" not in raw_trace_text
    assert "sk-output-secret" not in raw_trace_text
