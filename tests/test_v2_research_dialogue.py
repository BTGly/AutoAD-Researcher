from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from autoad_researcher.assistant.llm_runtime import current_conversation_deadline
from autoad_researcher.assistant.v2.orchestrator import ResearchOrchestratorV2
from autoad_researcher.assistant.v2.research_dialogue_agent import (
    GatedDialogueDecision,
    ResearchDecisionAgent,
    ResearchPolicyAssessment,
    ResearchReplyAgent,
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


def _allow_policy() -> dict[str, str]:
    return {
        "decision": "allow",
        "category": "none",
        "reason": "",
        "safe_alternative": "",
    }


def _decision_payload(mode: str = "ask") -> dict:
    return {
        "dialogue_mode": mode,
        "policy_assessment": _allow_policy(),
        "source_action": None,
        "task_action": None,
        "target_spec": None,
    }


def _reply_payload() -> dict:
    return {
        "reply_to_user": "你的目标是复现指定实现；当前材料还在处理，我不会假装已经读过。",
        "summary": {
            "goal": "复现指定实现并核对结果",
            "confirmed_facts": ["用户明确要求只做复现"],
            "inferred_facts": [{
                "statement": "仓库分析尚未完成",
                "basis": "pending_jobs: job_000001",
            }],
            "unresolved_conflicts": [],
            "blocking_question": None,
        },
    }


def _gated_decision(mode: str = "ask") -> GatedDialogueDecision:
    return GatedDialogueDecision(
        dialogue_mode=mode,
        policy_assessment=ResearchPolicyAssessment.model_validate(_allow_policy()),
    )


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


def test_decision_agent_calls_llm_once_with_short_decision_contract(monkeypatch):
    captured: dict[str, object] = {"calls": 0}

    def fake_call(api_key, provider_base_url, messages, **kwargs):
        captured["calls"] = int(captured["calls"]) + 1
        captured["messages"] = messages
        captured["temperature"] = kwargs.get("temperature")
        return {"reply": json.dumps(_decision_payload()), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    decision = ResearchDecisionAgent.decide(
        user_input="只做复现，不要改代码。",
        evidence_state={"pending_jobs": [{"job_id": "job_000001"}]},
        last_summary=ResearchIntentSummary(goal="复现实验"),
        transcript_tail=[{"role": "user", "content": "先看仓库"}],
        api_key="sk-test",
        provider_url="https://example.test",
        model="decision-model",
    )

    assert captured["calls"] == 1
    assert captured["temperature"] == 0.0
    system = captured["messages"][0]["content"]
    assert "job_000001" in system
    assert "<decision_scope>" in system
    assert system.rstrip().endswith("</decision_output>")
    assert "不写用户回复，不生成 summary" in system
    assert "reply_to_user" not in system
    assert decision.is_valid is True
    assert decision.dialogue_mode == "ask"


def test_reply_agent_calls_llm_once_with_frozen_decision(monkeypatch):
    captured: dict[str, object] = {}

    def fake_call(api_key, provider_base_url, messages, **kwargs):
        captured["messages"] = messages
        return {"reply": json.dumps(_reply_payload(), ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    response = ResearchReplyAgent.respond(
        user_input="只做复现，不要改代码。",
        evidence_state={"pending_jobs": [{"job_id": "job_000001"}]},
        frozen_decision=_gated_decision("plan"),
        last_summary=ResearchIntentSummary(goal="复现实验"),
        api_key="sk-test",
        provider_url="https://example.test",
        model="reply-model",
    )

    system = captured["messages"][0]["content"]
    assert "job_000001" in system
    assert '"dialogue_mode": "plan"' in system
    assert "冻结决策（不可改写）" in system
    assert "<identity>" in system
    assert system.rstrip().endswith("</style_and_output>")
    assert "不要输出 mode、policy" in system
    assert response.should_persist is True
    assert response.summary.confirmed_facts == ["用户明确要求只做复现"]


def test_reply_agent_receives_exact_repository_structure_evidence(monkeypatch):
    captured: dict[str, object] = {}

    def fake_call(api_key, provider_base_url, messages, **kwargs):
        captured["messages"] = messages
        return {"reply": json.dumps(_reply_payload(), ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    ResearchReplyAgent.respond(
        user_input="请你自己看仓库。",
        evidence_state={
            "usable_evidence": [{
                "source_id": "src_repo",
                "evidence_type": "repo_summary",
                "artifact_path": "repository_intelligence/src_repo/structure.json",
                "parser_name": "repository_intelligence_v2",
                "summary": "Repository structure was inspected.",
                "raw": {
                    "validation_status": "passed",
                    "entrypoint_candidates": ["src/main.py"],
                    "configuration_candidates": ["configs/baseline.yaml"],
                    "declared_entrypoints": {"demo": "src.main:main"},
                },
            }],
        },
        frozen_decision=_gated_decision(),
        last_summary=None,
        api_key="sk-test",
        provider_url="https://example.test",
        model="reply-model",
    )

    system = captured["messages"][0]["content"]
    assert '"entrypoint_candidates": ["src/main.py"]' in system
    assert '"configuration_candidates": ["configs/baseline.yaml"]' in system
    assert '"declared_entrypoints": {"demo": "src.main:main"}' in system
    assert '"validation_status": "passed"' in system


def test_policy_assessment_requires_structured_refusal_details():
    assessment = ResearchPolicyAssessment(
        decision="reject",
        category="evaluation_leakage",
        reason="正式测试标签进入训练会污染独立评估。",
        safe_alternative="使用独立 validation split。",
    )

    assert assessment.category == "evaluation_leakage"
    with pytest.raises(ValidationError):
        ResearchPolicyAssessment(
            decision="reject",
            category="none",
            reason="",
            safe_alternative="",
        )


def test_agents_fail_closed_without_model_configuration(monkeypatch):
    def fail_if_called(*args, **kwargs):
        raise AssertionError("model call must not run without an injected model")

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fail_if_called)

    decision = ResearchDecisionAgent.decide(
        user_input="继续",
        evidence_state={},
        last_summary=None,
        api_key="sk-test",
        provider_url="https://example.test",
        model="",
    )
    reply = ResearchReplyAgent.respond(
        user_input="继续",
        evidence_state={},
        frozen_decision=_gated_decision(),
        last_summary=ResearchIntentSummary(goal="原目标"),
        api_key="sk-test",
        provider_url="https://example.test",
        model="",
    )

    assert decision.is_valid is False
    assert reply.should_persist is False
    assert reply.summary.goal == "原目标"


def test_source_removal_and_target_specs_are_typed():
    source = SourceInstruction(
        action="request_source_removal",
        source_id="src_wrong",
        label_hint="wrong.md",
        reason="用户明确要求撤回",
    )
    assert source.source_id == "src_wrong"
    with pytest.raises(ValidationError):
        SourceInstruction.model_validate({
            "action": "remove_latest",
            "source_id": "src_wrong",
        })

    target = TargetSpec.model_validate({
        "adapter_id": "kernelbench",
        "selectors": {"level": 2, "problem_id": 40},
    })
    resolved = get_target_adapter_registry().resolve(target.adapter_id, target.selectors)
    assert resolved is not None
    assert resolved.selectors == {"level": 2, "problem_id": 40}


def test_orchestrator_invalid_decision_preserves_existing_summary(monkeypatch, tmp_path: Path):
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
        model="configured-model",
    )

    assert "意图判定失败" in result.reply
    assert load_research_intent_summary(tmp_path) == previous
    assert result.source_action is None
    assert result.experiment_task is None
    assert not (tmp_path / "assistant" / "v2_dialogue_transitions.jsonl").exists()


def test_orchestrator_calls_decision_then_reply_under_one_deadline(monkeypatch, tmp_path: Path):
    calls: list[dict[str, object]] = []
    replies = [_decision_payload(), _reply_payload()]
    context_builds = 0

    from autoad_researcher.assistant.v2.context_builder import build_llm_context

    def counted_context(*args, **kwargs):
        nonlocal context_builds
        context_builds += 1
        return build_llm_context(*args, **kwargs)

    def fake_call(*args, **kwargs):
        deadline = current_conversation_deadline()
        calls.append({
            "deadline": deadline,
            "temperature": kwargs.get("temperature"),
        })
        return {"reply": json.dumps(replies.pop(0), ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    monkeypatch.setattr(
        "autoad_researcher.assistant.v2.orchestrator.build_llm_context",
        counted_context,
    )

    result = ResearchOrchestratorV2.handle(
        tmp_path,
        user_input="继续",
        api_key="sk-test",
        provider_url="https://example.test",
        model="configured-model",
    )

    assert len(calls) == 2
    assert context_builds == 1
    assert all(item["deadline"] is not None for item in calls)
    assert calls[0]["deadline"] is calls[1]["deadline"]
    assert all(item["temperature"] == 0.0 for item in calls)
    assert (tmp_path / "assistant" / "v2_dialogue_transitions.jsonl").is_file()
    assert result.dialogue_mode == "ask"
    assert result.intent_summary["goal"] == "复现指定实现并核对结果"
