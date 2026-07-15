from __future__ import annotations

import json
from pathlib import Path

from autoad_researcher.assistant.v2.research_intent_interpreter import (
    _build_interpreter_messages,
    interpret_research_intent,
)


def _payload(user_input: str) -> dict:
    evidence = "复现 Library-A"
    start = user_input.index(evidence)
    return {
        "research_modes": {
            "primary_mode": "reproduction",
            "secondary_modes": ["feasibility_assessment", "reproduction"],
            "confidence": 0.94,
            "rationale": "The user wants reproduction before feasibility assessment.",
        },
        "intent_mutation": {
            "base_draft_sha256": None,
            "full_turn_mutation_evidence": user_input,
            "operations": [{
                "operation": "set",
                "target": "research_goal",
                "proposed_value": evidence,
                "evidence_spans": [{
                    "source": "current_user_turn",
                    "start": start,
                    "end": start + len(evidence),
                    "text": evidence,
                }],
                "confidence": 0.96,
            }],
        },
        "material_observations": [],
        "open_questions": [{
            "category": "evaluation",
            "question": "如何判断复现结果可接受？",
            "required_now": True,
            "rationale": "The success criterion is absent.",
        }],
        "evidence_conflicts": [],
        "advisory_suggestions": [],
    }


def _call(monkeypatch, tmp_path: Path, response: dict):
    monkeypatch.setattr(
        "autoad_researcher.ui.chat_client.call_research_chat",
        lambda *args, **kwargs: response,
    )
    return interpret_research_intent(
        run_dir=tmp_path,
        user_input="先复现 Library-A，再评估能否迁移。",
        persisted_contract=None,
        recent_mutation_receipts=[],
        recent_dialogue=[],
        active_sources=[],
        usable_evidence=[],
        unusable_evidence=[],
        jobs=[],
        pending_confirmation=None,
        system_safety_policy=["plan_only"],
        api_key="sk-test",
        provider_url="https://example.test",
        model="test-model",
    )


def test_interpreter_returns_composite_modes_and_field_operations(monkeypatch, tmp_path: Path):
    user_input = "先复现 Library-A，再评估能否迁移。"
    outcome = _call(monkeypatch, tmp_path, {"reply": json.dumps(_payload(user_input), ensure_ascii=False), "error": ""})

    assert outcome.status == "ok"
    assert outcome.interpretation is not None
    assert outcome.interpretation.research_modes.primary_mode == "reproduction"
    assert outcome.interpretation.research_modes.secondary_modes == ["feasibility_assessment"]
    assert outcome.interpretation.intent_mutation.operations[0].target == "research_goal"


def test_interpreter_fails_closed_on_provider_error(monkeypatch, tmp_path: Path):
    outcome = _call(monkeypatch, tmp_path, {"reply": "", "error": "timeout", "error_type": "timeout"})

    assert outcome.status == "failed"
    assert outcome.failure_reason == "provider_error"
    assert outcome.interpretation is None


def test_interpreter_rejects_forged_current_turn_span(monkeypatch, tmp_path: Path):
    user_input = "先复现 Library-A，再评估能否迁移。"
    payload = _payload(user_input)
    payload["intent_mutation"]["operations"][0]["evidence_spans"][0]["text"] = "复现 Library-B"
    outcome = _call(monkeypatch, tmp_path, {"reply": json.dumps(payload, ensure_ascii=False), "error": ""})

    assert outcome.status == "failed"
    assert outcome.failure_reason == "invalid_current_turn_provenance"


def test_interpreter_context_keeps_current_turn_separate():
    messages = _build_interpreter_messages(
        system_prompt="system",
        user_input="当前纠正",
        persisted_contract={"research_goal": "持久化目标"},
        recent_mutation_receipts=[{"status": "applied"}],
        recent_dialogue=[{"role": "user", "content": "旧目标"}],
        active_sources=[],
        usable_evidence=[],
        unusable_evidence=[],
        jobs=[],
        pending_confirmation=None,
        system_safety_policy=["no_execution"],
    )

    assert messages[-1] == {"role": "user", "content": "当前纠正"}
    snapshot = json.loads(messages[-2]["content"].split("\n", 1)[1])
    assert snapshot["current_persisted_contract"]["research_goal"] == "持久化目标"
    assert snapshot["recent_dialogue"][0]["content"] == "旧目标"
    assert "当前纠正" not in messages[-2]["content"]
