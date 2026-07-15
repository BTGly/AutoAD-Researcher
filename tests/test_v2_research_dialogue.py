from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from autoad_researcher.assistant.v2.orchestrator import ResearchOrchestratorV2
from autoad_researcher.assistant.v2.research_dialogue_agent import (
    ResearchDialogueAgent,
    ResearchDialogueResponse,
    SourceInstruction,
    TargetSpec,
    _parse_json_object,
)
from autoad_researcher.assistant.v2.research_intent_summary import (
    BasedStatement,
    ResearchIntentSummary,
    load_research_intent_summary,
    save_research_intent_summary,
)
from autoad_researcher.assistant.v2.target_adapter import get_target_adapter_registry


def _response_payload() -> dict:
    return {
        "reply_to_user": "你的目标是复现指定实现；当前材料还在处理，我不会假装已经读过。",
        "summary": {
            "goal": "复现指定实现并核对结果",
            "confirmed_facts": ["用户明确要求只做复现"],
            "inferred_facts": [
                {
                    "statement": "仓库分析尚未完成",
                    "basis": "pending_jobs: job_000001",
                }
            ],
            "unresolved_conflicts": [],
            "blocking_question": None,
        },
    }


def test_summary_round_trip_uses_exact_schema(tmp_path: Path):
    summary = ResearchIntentSummary(
        goal="复现 PatchCore",
        confirmed_facts=["用户使用 RTX 4090"],
        inferred_facts=[BasedStatement(statement="材料待解析", basis="source_id=src_1")],
        unresolved_conflicts=[],
        blocking_question=None,
    )

    path = save_research_intent_summary(tmp_path, summary)

    assert path == tmp_path / "summary.json"
    assert load_research_intent_summary(tmp_path) == summary
    assert not (tmp_path / "summary.json.tmp").exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {
        "goal",
        "confirmed_facts",
        "inferred_facts",
        "unresolved_conflicts",
        "blocking_question",
    }


def test_summary_rejects_unidentified_statement_fields():
    with pytest.raises(ValidationError):
        BasedStatement.model_validate({"statement": "风险", "source": "repo"})


def test_dialogue_json_parser_accepts_transport_text_around_one_object():
    assert _parse_json_object('prefix\n{"reply_to_user":"ok","summary":{}}\nsuffix') == {
        "reply_to_user": "ok",
        "summary": {},
    }


def test_dialogue_agent_calls_llm_once_and_supplies_behavior_contract(monkeypatch):
    captured: dict[str, object] = {"calls": 0}

    def fake_call(api_key, provider_base_url, messages, **kwargs):
        captured["calls"] = int(captured["calls"]) + 1
        captured["messages"] = messages
        return {"reply": json.dumps(_response_payload(), ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    previous = ResearchIntentSummary(
        goal="复现实验",
        confirmed_facts=["用户要求 plan_only"],
    )

    response = ResearchDialogueAgent.respond(
        user_input="只做复现，不要改代码。",
        evidence_state={"pending_jobs": [{"job_id": "job_000001"}]},
        last_summary=previous,
        transcript_tail=[{"role": "user", "content": "先看仓库"}],
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert captured["calls"] == 1
    system = captured["messages"][0]["content"]
    assert "Propose first" in system
    assert "Don't interrogate" in system
    assert "不要宣告“已保存”、“已更新”" in system
    assert "job_000001" in system
    assert "用户要求 plan_only" in system
    assert "source_id 必须逐字复制" in system
    assert "先不要删除" in system
    assert 'task_action={"action":"prepare_experiment_task"}' in system
    assert "只准备一个 plan_only" in system
    assert "没有匹配 Adapter" in system
    assert '"adapter_id": "kernelbench"' in system
    assert '"problem_id"' in system
    assert "初步假设（preliminary hypothesis）" in system
    assert "初步假设不得写成 inferred_facts" in system
    assert "当用户明确要求对比、步骤、清单、表格或实施细节时" in system
    assert "两到四个短自然段" not in system
    assert "禁止给出并行分支" not in system
    assert response.should_persist is True
    assert response.summary.confirmed_facts == ["用户明确要求只做复现"]


def test_source_removal_instruction_is_typed_and_forbids_extra_fields():
    response = ResearchDialogueResponse.model_validate({
        **_response_payload(),
        "source_action": {
            "action": "request_source_removal",
            "source_id": "src_wrong",
            "label_hint": "wrong.md",
            "reason": "用户明确要求撤回",
        },
    })

    assert response.source_action == SourceInstruction(
        action="request_source_removal",
        source_id="src_wrong",
        label_hint="wrong.md",
        reason="用户明确要求撤回",
    )
    with pytest.raises(ValidationError):
        SourceInstruction.model_validate({
            "action": "remove_latest",
            "source_id": "src_wrong",
        })


def test_target_spec_is_generic_and_registry_validates_selectors():
    target = TargetSpec.model_validate({
        "adapter_id": "kernelbench",
        "selectors": {"level": 2, "problem_id": 40},
    })

    assert target.selectors == {"level": 2, "problem_id": 40}
    resolved = get_target_adapter_registry().resolve(target.adapter_id, target.selectors)
    assert resolved is not None
    assert resolved.selectors == {"level": 2, "problem_id": 40}
    assert get_target_adapter_registry().resolve(
        "kernelbench",
        {"level": 2, "problem_id": 40, "variant": 1},
    ) is None
    assert get_target_adapter_registry().resolve(
        "kernelbench",
        {"level": -1, "problem_id": 40},
    ) is None
    assert get_target_adapter_registry().resolve(
        "unknown_benchmark",
        {"workload": 1},
    ) is None


def test_orchestrator_invalid_llm_output_preserves_existing_summary(monkeypatch, tmp_path: Path):
    previous = ResearchIntentSummary(goal="原目标", confirmed_facts=["用户明确说了原目标"])
    save_research_intent_summary(tmp_path, previous)

    monkeypatch.setattr(
        "autoad_researcher.ui.chat_client.call_research_chat",
        lambda *args, **kwargs: {"reply": "not-json", "error": ""},
    )

    result = ResearchOrchestratorV2.handle(
        tmp_path,
        user_input="继续",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert "格式无效" not in result.reply
    assert "生成失败" in result.reply
    assert load_research_intent_summary(tmp_path) == previous
