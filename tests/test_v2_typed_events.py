from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from autoad_researcher.assistant.v2.event_service import append_typed_event, event_to_ws_message, load_events_since
from autoad_researcher.assistant.v2.llm_trace_service import append_llm_trace
from autoad_researcher.assistant.v2.orchestrator import ResearchOrchestratorV2
from autoad_researcher.server.models import ChatRequest
from autoad_researcher.server.routes.chat import _assistant_delta_message, _assistant_done_message, _resolve_message_id


def test_low_frequency_typed_event_writes_and_replays(tmp_path: Path):
    run_dir = tmp_path / "run_events"
    run_dir.mkdir()

    append_typed_event(run_dir, "planner.turn_gate.decided", {
        "turn_type": "ordinary_chat",
        "contract_action": "answer_without_contract_update",
    })

    events = load_events_since(run_dir)
    assert [event["type"] for event in events] == ["planner.turn_gate.decided"]
    assert event_to_ws_message(events[0]) == {
        "type": "planner.turn_gate.decided",
        "turn_type": "ordinary_chat",
        "contract_action": "answer_without_contract_update",
    }

    with pytest.raises(ValueError, match="unsupported low-frequency typed event"):
        append_typed_event(run_dir, "assistant.delta", {"content": "token"})


def test_prompt_trace_created_event_is_redacted_summary(tmp_path: Path):
    run_dir = tmp_path / "run_trace_event"
    run_dir.mkdir()

    append_llm_trace(
        run_dir,
        call_site="turn_gate",
        prompt_id="assistant.v2.turn_gate.v1",
        prompt_version="v1",
        prompt_text="system prompt with sk-prompt-secret",
        model="deepseek-v4-flash",
        provider_url="https://user:sk-provider-secret@example.test/v1?api_key=sk-query-secret",
        messages=[{"role": "user", "content": "private message sk-message-secret"}],
        raw_output="raw output sk-output-secret",
        parse_status="ok",
        schema_validation="error",
        fallback_reason="schema_validation_error",
        latency_ms=5.0,
    )

    events = load_events_since(run_dir)
    assert [event["type"] for event in events] == ["prompt.trace.created", "schema.validation.failed"]
    trace_payload = events[0]["payload"]
    assert trace_payload["call_site"] == "turn_gate"
    assert trace_payload["prompt_id"] == "assistant.v2.turn_gate.v1"
    assert trace_payload["schema_validation"] == "error"
    assert trace_payload["fallback_reason"] == "schema_validation_error"

    raw_events_text = (run_dir / "events" / "events.jsonl").read_text(encoding="utf-8")
    assert "sk-provider-secret" not in raw_events_text
    assert "sk-query-secret" not in raw_events_text
    assert "sk-prompt-secret" not in raw_events_text
    assert "sk-message-secret" not in raw_events_text
    assert "sk-output-secret" not in raw_events_text
    assert "private message" not in raw_events_text


def test_orchestrator_persists_low_frequency_typed_events_without_token_deltas(monkeypatch, tmp_path: Path):
    run_dir = tmp_path / "run_orchestrator_events"
    run_dir.mkdir()

    def fake_call(api_key, provider_base_url, messages, **kwargs):
        system_text = messages[0]["content"]
        if "ConversationRouter" in system_text:
            return {
                "reply": json.dumps({
                    "turn_gate": {
                        "turn_type": "contract_update",
                        "contract_action": "update_contract",
                        "contract_update_allowed": True,
                        "need_discovery_allowed": True,
                        "save_draft_allowed": True,
                        "confirmation_action_proposal": "none",
                        "task_profile_proposal": "empirical_model_research",
                        "task_profile_evidence": "PatchCore",
                        "requires_need_discovery_enrichment": False,
                        "suggested_task_title": "PatchCore MVTec AUROC优化",
                        "suggested_task_summary": "提升 MVTec AD 的图像级 AUROC。",
                        "user_intent_summary": "research contract update",
                        "evidence_from_current_turn": ["PatchCore"],
                        "evidence_from_context": [],
                        "confidence": 0.9,
                        "reason": "research turn",
                        "next_reply_instruction": None,
                    },
                    "source_action_plan": {
                        "actions": [],
                        "user_visible_summary": "",
                        "confidence": 0.7,
                        "reason": "no source action",
                    },
                    "task_profile_proposal": "empirical_model_research",
                    "task_profile_evidence": "PatchCore",
                    "suggested_task_title": "PatchCore MVTec AUROC优化",
                    "suggested_task_summary": "提升 MVTec AD 的图像级 AUROC。",
                    "requires_need_discovery_enrichment": False,
                }, ensure_ascii=False),
                "error": "",
            }
        if "Need Discovery" in system_text:
            return {
                "reply": json.dumps(_ready_need_spec_payload(), ensure_ascii=False),
                "error": "",
            }
        return {"reply": json.dumps({"reply_to_user": "ok"}, ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="我想基于 PatchCore 提升 MVTec AD，主要看 image AUROC。",
        api_key="sk-test",
        provider_url="https://user:sk-provider-secret@example.test/v1?api_key=sk-query-secret",
    )

    assert result.reply_kind == "intent_contract_confirmation"

    event_types = [event["type"] for event in load_events_since(run_dir)]
    assert "planner.conversation_route.decided" in event_types
    assert "planner.source_action.decided" not in event_types
    assert "planner.turn_gate.decided" not in event_types
    assert "planner.need_discovery.decided" in event_types
    assert "contract.draft.updated" in event_types
    assert "contract.confirmation.requested" in event_types
    assert "prompt.trace.created" in event_types
    assert "assistant.delta" not in event_types
    assert "assistant.done" not in event_types

    raw_events_text = (run_dir / "events" / "events.jsonl").read_text(encoding="utf-8")
    assert "sk-provider-secret" not in raw_events_text
    assert "sk-query-secret" not in raw_events_text


def test_assistant_delta_and_done_message_shapes_remain_compatible():
    assert _assistant_delta_message("assistant_1", "hello") == {
        "type": "assistant.delta",
        "message_id": "assistant_1",
        "content": "hello",
    }
    assert _assistant_done_message("assistant_1", "answer", "done") == {
        "type": "assistant.done",
        "message_id": "assistant_1",
        "reply_kind": "answer",
        "content": "done",
    }


def test_chat_request_id_is_reused_as_assistant_message_id():
    request = ChatRequest(user_input="hello", request_id="client.request_1")

    assert _resolve_message_id(request.request_id) == "client.request_1"


def test_chat_request_id_falls_back_and_rejects_unsafe_characters():
    assert _resolve_message_id(None).startswith("assistant_")

    with pytest.raises(ValidationError):
        ChatRequest(user_input="hello", request_id="request id with spaces")


def _ready_need_spec_payload() -> dict:
    return {
        "task_summary": "PatchCore MVTec AD improvement",
        "inferred_task_type": "image_anomaly_detection_improvement",
        "current_stage_goal": "generate_plan",
        "needs": [
            _need("research_goal", "intent", "plan", "提升 PatchCore 在 MVTec AD 上的 image_level_auroc"),
            _need("baseline", "experiment_object", "plan", "PatchCore"),
            _need("dataset", "experiment_object", "plan", "MVTec AD"),
            _need("metrics", "evaluation", "plan", ["image_level_auroc"]),
            _need("success_criteria", "evaluation", "plan", "improve image_level_auroc under the same evaluation protocol"),
            _need("execution_mode", "execution", "plan", "plan_only"),
        ],
        "blocking_needs": [],
        "next_best_question": None,
        "ready_for_plan": True,
        "ready_for_repo_analysis": False,
        "ready_for_experiment_design": True,
        "ready_for_patch": False,
        "ready_for_run": False,
    }


def _need(name: str, category: str, required_for: str, current_value):
    return {
        "name": name,
        "category": category,
        "required_for": required_for,
        "necessity": "required_now",
        "current_value": current_value,
        "source": "user",
        "confidence": 0.9,
        "blocking": False,
        "question_to_user": None,
    }
