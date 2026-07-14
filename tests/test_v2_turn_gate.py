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
    assert calls == 2


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


def test_turn_gate_repairs_missing_required_field_once(monkeypatch):
    replies = [
        {
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
        },
        {
            "turn_type": "contract_update",
            "contract_action": "update_contract",
            "contract_update_allowed": True,
            "need_discovery_allowed": True,
            "save_draft_allowed": True,
            "user_intent_summary": "research intent supplied",
            "evidence_from_current_turn": [],
            "evidence_from_context": [],
            "confidence": 0.9,
            "reason": "research turn",
            "next_reply_instruction": None,
        },
    ]
    captured_system_prompts: list[str] = []

    def fake_call(api_key, provider_base_url, messages, **kwargs):
        captured_system_prompts.append(messages[0]["content"])
        return {"reply": json.dumps(replies.pop(0), ensure_ascii=False), "error": ""}

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
    assert len(captured_system_prompts) == 2
    assert "Repair one TurnGateDecision response" in captured_system_prompts[1]


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
