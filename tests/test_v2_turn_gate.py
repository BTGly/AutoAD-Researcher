from __future__ import annotations

import json

from autoad_researcher.assistant.v2.turn_gate import decide_turn_gate_with_llm


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


def test_turn_gate_allows_structured_source_intake_without_keyword_judgment():
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
    assert decision.contract_update_allowed is True
    assert decision.need_discovery_allowed is True
    assert decision.save_draft_allowed is True


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


def test_turn_gate_invalid_llm_output_falls_back_to_no_contract_update(monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
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
