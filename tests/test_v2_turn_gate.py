from __future__ import annotations

import json
from pathlib import Path

from autoad_researcher.assistant.v2.turn_gate import (
    _validate_turn_gate_payload,
    decide_turn_gate_with_llm,
)


def test_turn_gate_replay_fixture_uses_only_safe_local_recovery():
    fixture_path = Path(__file__).parent / "fixtures" / "turn_gate_replay.json"
    cases = json.loads(fixture_path.read_text(encoding="utf-8"))

    for case in cases:
        decision, _errors, _recovery = _validate_turn_gate_payload(
            case["payload"],
            user_input=case["user_input"],
            context_user_text=case["context_user_text"],
        )
        expected = case["expected_action"]
        assert (decision.contract_action if decision is not None else None) == expected, case["name"]


def test_turn_gate_without_api_does_not_update_natural_language_contract():
    decision = decide_turn_gate_with_llm(
        user_input="我想基于 PatchCore 提升 MVTec AD",
        transcript_tail=[],
        existing_contract_draft=None,
        created_sources=[],
        created_jobs=[],
        answerability={},
        api_key="",
        provider_url="",
    )

    assert decision.contract_action == "answer_without_contract_update"
    assert decision.contract_update_allowed is False
    assert decision.need_discovery_allowed is False
    assert decision.save_draft_allowed is False


def test_turn_gate_without_api_keeps_structured_source_intake_out_of_contract():
    decision = decide_turn_gate_with_llm(
        user_input="https://github.com/amazon-science/patchcore-inspection",
        transcript_tail=[],
        existing_contract_draft=None,
        created_sources=[{"source_id": "src_1", "kind": "github_repo"}],
        created_jobs=[],
        answerability={},
        api_key="",
        provider_url="",
    )

    assert decision.turn_type == "source_intake"
    assert decision.contract_action == "answer_without_contract_update"
    assert decision.contract_update_allowed is False
    assert decision.need_discovery_allowed is False
    assert decision.save_draft_allowed is False


def test_turn_gate_uses_llm_for_research_keyword_joke(monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
        assert "不是关键词分类器" in messages[0]["content"]
        return {"reply": json.dumps({
            "turn_type": "joke",
            "contract_action": "answer_without_contract_update",
            "contract_update_allowed": False,
            "need_discovery_allowed": False,
            "save_draft_allowed": False,
            "user_intent_summary": "用户在开玩笑，不是在推进研究合同。",
            "evidence_from_current_turn": ["你是 PatchCore 战神哈哈哈"],
            "evidence_from_context": [],
            "confidence": 0.92,
            "reason": "语用意图是玩笑。",
            "next_reply_instruction": "自然回应，不追问合同字段。",
        }, ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    decision = decide_turn_gate_with_llm(
        user_input="你是 PatchCore 战神哈哈哈",
        transcript_tail=[],
        existing_contract_draft=None,
        created_sources=[],
        created_jobs=[],
        answerability={},
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert decision.turn_type == "joke"
    assert decision.contract_update_allowed is False
    assert decision.need_discovery_allowed is False
    assert decision.save_draft_allowed is False


def test_turn_gate_profile_evidence_cannot_authorize_contract_update(monkeypatch):
    user_input = "PatchCore"

    def fake_call(api_key, provider_base_url, messages, **kwargs):
        return {"reply": json.dumps({
            "turn_type": "contract_update",
            "contract_action": "update_contract",
            "contract_update_allowed": True,
            "need_discovery_allowed": True,
            "save_draft_allowed": True,
            "task_profile_proposal": "empirical_model_research",
            "task_profile_evidence": "PatchCore",
            "user_intent_summary": "profile name",
            "evidence_from_current_turn": ["PatchCore"],
            "evidence_from_context": [],
            "confidence": 0.9,
            "reason": "misrouted update",
        }, ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    decision = decide_turn_gate_with_llm(
        user_input=user_input,
        transcript_tail=[],
        existing_contract_draft=None,
        created_sources=[],
        created_jobs=[],
        answerability={},
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert decision.contract_action == "answer_without_contract_update"
    assert decision.contract_update_allowed is False


def test_turn_gate_invalid_llm_output_falls_back_to_no_contract_update(monkeypatch):
    calls = 0

    def fake_call(api_key, provider_base_url, messages, **kwargs):
        nonlocal calls
        calls += 1
        return {"reply": "not json", "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    decision = decide_turn_gate_with_llm(
        user_input="可以，就这个",
        transcript_tail=[],
        existing_contract_draft=None,
        created_sources=[],
        created_jobs=[],
        answerability={},
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert decision.contract_action == "answer_without_contract_update"
    assert decision.save_draft_allowed is False
    assert calls == 1


def test_turn_gate_ignores_only_extra_fields_without_repair_call(monkeypatch):
    calls = 0

    def fake_call(api_key, provider_base_url, messages, **kwargs):
        nonlocal calls
        calls += 1
        return {"reply": json.dumps({
            "turn_type": "contract_update",
            "contract_action": "update_contract",
            "contract_update_allowed": True,
            "need_discovery_allowed": True,
            "save_draft_allowed": True,
            "user_intent_summary": "research intent supplied",
            "evidence_from_current_turn": [],
            "evidence_from_context": [],
            "mutation_evidence_from_current_turn": "我想基于 PatchCore 在 MVTec AD 上提升 image AUROC",
            "confidence": 0.9,
            "reason": "research turn",
            "next_reply_instruction": None,
            "unrecognized_explanation": "ignore this field",
        }, ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    decision = decide_turn_gate_with_llm(
        user_input="我想基于 PatchCore 在 MVTec AD 上提升 image AUROC",
        transcript_tail=[],
        existing_contract_draft=None,
        created_sources=[],
        created_jobs=[],
        answerability={},
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert decision.contract_action == "update_contract"
    assert decision.contract_update_allowed is True
    assert decision.need_discovery_allowed is True
    assert decision.save_draft_allowed is True
    assert calls == 1


def test_turn_gate_missing_required_field_falls_back_without_repair(monkeypatch):
    reply = {
        "turn_type": "contract_update",
        "contract_update_allowed": True,
        "need_discovery_allowed": True,
        "save_draft_allowed": True,
        "user_intent_summary": "research intent supplied",
        "evidence_from_current_turn": [],
        "evidence_from_context": [],
        "confidence": 0.9,
        "reason": "research turn",
        "next_reply_instruction": None,
    }
    captured_system_prompts: list[str] = []

    def fake_call(api_key, provider_base_url, messages, **kwargs):
        captured_system_prompts.append(messages[0]["content"])
        return {"reply": json.dumps(reply, ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    decision = decide_turn_gate_with_llm(
        user_input="我想基于 PatchCore 在 MVTec AD 上提升 image AUROC",
        transcript_tail=[],
        existing_contract_draft=None,
        created_sources=[],
        created_jobs=[],
        answerability={},
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert decision.contract_action == "answer_without_contract_update"
    assert decision.contract_update_allowed is False
    assert len(captured_system_prompts) == 1


def test_turn_gate_schema_and_examples_are_generated_from_model(monkeypatch):
    captured: list[dict[str, str]] = []

    def fake_call(api_key, provider_base_url, messages, **kwargs):
        captured.extend(messages)
        return {"reply": json.dumps({
            "turn_type": "ordinary_chat",
            "contract_action": "answer_without_contract_update",
            "contract_update_allowed": False,
            "need_discovery_allowed": False,
            "save_draft_allowed": False,
            "evidence_from_current_turn": "你好",
        }, ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    decision = decide_turn_gate_with_llm(
        user_input="你好",
        transcript_tail=[],
        existing_contract_draft=None,
        created_sources=[],
        created_jobs=[],
        answerability={},
        api_key="sk-test",
        provider_url="https://example.test",
    )

    schema_text = captured[1]["content"]
    assert '"turn_type"' in schema_text
    assert '"ordinary_chat"' in schema_text
    assert '"contract_update"' in schema_text
    assert '"mutation_evidence_from_current_turn"' in schema_text
    assert decision.evidence_from_current_turn == ["你好"]


def test_update_contract_action_normalizes_inconsistent_model_flags(monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
        return {"reply": json.dumps({
            "turn_type": "contract_update",
            "contract_action": "update_contract",
            "contract_update_allowed": True,
            "need_discovery_allowed": False,
            "save_draft_allowed": True,
            "user_intent_summary": "用户补充了数值成功标准。",
            "evidence_from_current_turn": ["我要提升5%"],
            "evidence_from_context": ["PatchCore", "MVTec AD", "image-level AUROC"],
            "mutation_evidence_from_current_turn": "我要提升5%",
            "confidence": 0.93,
            "reason": "研究合同更新。",
            "next_reply_instruction": "更新成功标准。",
        }, ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    decision = decide_turn_gate_with_llm(
        user_input="我要提升5%",
        transcript_tail=[
            {
                "role": "user",
                "content": (
                    "我想以 PatchCore 为 baseline，在 MVTec AD 数据集上提升 image-level AUROC。"
                    "保持测试集、指标定义和数据划分不变，代码修改需要逐步确认。"
                ),
            },
        ],
        existing_contract_draft=None,
        created_sources=[],
        created_jobs=[],
        answerability={},
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert decision.turn_type == "contract_update"
    assert decision.contract_action == "update_contract"
    assert decision.contract_update_allowed is True
