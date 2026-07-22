from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

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
    ConfirmedTaskParameters,
    ResearchIntentSummary,
    load_research_intent_summary,
    save_research_intent_summary,
)
from autoad_researcher.schemas.decisions import ConfirmedDecision
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


def _rejected_decision_payload(category: str, reason: str, alternative: str) -> dict:
    return {
        "dialogue_mode": "act",
        "action_scope": "code",
        "policy": "deny",
        "policy_assessment": {
            "decision": "reject",
            "category": category,
            "reason": reason,
            "safe_alternative": alternative,
        },
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
        "confirmed_task_parameters",
        "inferred_facts",
        "unresolved_conflicts",
        "blocking_question",
    }
    assert payload["confirmed_task_parameters"] == {
        "baseline": None,
        "dataset": None,
        "compute_budget": None,
        "primary_metrics": [],
        "evaluation_constraints": [],
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


def test_decision_agent_repairs_invalid_json_once(monkeypatch):
    captured: list[dict[str, object]] = []
    replies = ["not-json", json.dumps(_decision_payload())]

    def fake_call(api_key, provider_base_url, messages, **kwargs):
        captured.append({"messages": messages, "temperature": kwargs.get("temperature")})
        return {"reply": replies.pop(0), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    decision = ResearchDecisionAgent.decide(
        user_input="继续",
        evidence_state={},
        last_summary=None,
        api_key="sk-test",
        provider_url="https://example.test",
        model="decision-model",
    )

    assert decision.is_valid is True
    assert len(captured) == 2
    assert captured[1]["temperature"] == 0.0
    repair_instruction = captured[1]["messages"][-1]["content"]
    assert "json_parse_error" in repair_instruction
    assert "保持上一轮的语义判断不变" in repair_instruction


def test_decision_agent_repairs_schema_error_and_records_redacted_diagnostic(monkeypatch, tmp_path: Path):
    invalid = _decision_payload()
    invalid.pop("policy_assessment")
    replies = [json.dumps(invalid), json.dumps(_decision_payload())]

    monkeypatch.setattr(
        "autoad_researcher.ui.chat_client.call_research_chat",
        lambda *args, **kwargs: {"reply": replies.pop(0), "error": ""},
    )

    decision = ResearchDecisionAgent.decide(
        run_dir=tmp_path,
        user_input="继续",
        evidence_state={},
        last_summary=None,
        api_key="sk-test",
        provider_url="https://example.test",
        model="decision-model",
    )

    event = json.loads((tmp_path / "events" / "events.jsonl").read_text(encoding="utf-8"))
    payload = event["payload"]
    assert decision.is_valid is True
    assert event["type"] == "assistant.decision_repair"
    assert payload["outcome"] == "succeeded"
    assert payload["failure_kind"] == "schema_validation_error"
    assert payload["validation_errors"] == [{"path": "policy_assessment", "type": "missing"}]
    assert payload["raw_output_length"] == len(json.dumps(invalid))
    assert len(payload["raw_output_sha256"]) == 64
    assert "raw_output" not in payload


def test_decision_agent_fails_closed_after_one_invalid_repair(monkeypatch):
    calls = 0

    def fake_call(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {"reply": "not-json", "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    decision = ResearchDecisionAgent.decide(
        user_input="继续",
        evidence_state={},
        last_summary=None,
        api_key="sk-test",
        provider_url="https://example.test",
        model="decision-model",
    )

    assert calls == 2
    assert decision.is_valid is False


def test_decision_agent_does_not_repair_provider_error(monkeypatch):
    calls = 0

    def fake_call(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {"reply": "", "error": "provider unavailable"}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    decision = ResearchDecisionAgent.decide(
        user_input="继续",
        evidence_state={},
        last_summary=None,
        api_key="sk-test",
        provider_url="https://example.test",
        model="decision-model",
    )

    assert calls == 1
    assert decision.is_valid is False


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


def test_reply_agent_repairs_schema_error_and_records_redacted_diagnostic(monkeypatch, tmp_path: Path):
    invalid = _reply_payload()
    invalid.pop("summary")
    replies = [json.dumps(invalid), json.dumps(_reply_payload())]
    captured: list[dict[str, object]] = []

    def fake_call(*args, **kwargs):
        captured.append({"messages": args[2], **kwargs})
        return {"reply": replies.pop(0), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    response = ResearchReplyAgent.respond(
        run_dir=tmp_path,
        user_input="继续",
        evidence_state={},
        frozen_decision=_gated_decision(),
        last_summary=None,
        api_key="sk-test",
        provider_url="https://example.test",
        model="reply-model",
    )

    event = json.loads((tmp_path / "events" / "events.jsonl").read_text(encoding="utf-8"))
    assert response.should_persist is True
    assert len(captured) == 2
    assert captured[1]["temperature"] == 0.0
    assert "ResearchReplyResponse schema 校验" in captured[1]["messages"][-1]["content"]
    assert event["type"] == "assistant.reply_repair"
    assert event["payload"]["outcome"] == "succeeded"
    assert event["payload"]["validation_errors"] == [{"path": "summary", "type": "missing"}]
    assert "raw_output" not in event["payload"]


def test_reply_agent_materializes_flat_parameters_with_current_turn_provenance(monkeypatch):
    reply = _reply_payload()
    reply["summary"]["confirmed_task_parameters"] = {
        "baseline": "PatchCore",
        "dataset": "MVTec AD bottle",
        "compute_budget": "GPU 0",
        "primary_metrics": ["instance AUROC"],
        "evaluation_constraints": ["不修改 evaluator"],
    }
    monkeypatch.setattr(
        "autoad_researcher.ui.chat_client.call_research_chat",
        lambda *args, **kwargs: {"reply": json.dumps(reply, ensure_ascii=False), "error": ""},
    )

    response = ResearchReplyAgent.respond(
        user_input="我确认使用 PatchCore、MVTec AD bottle、GPU 0 和 instance AUROC；不修改 evaluator。",
        evidence_state={},
        frozen_decision=GatedDialogueDecision(
            dialogue_mode="plan",
            conversation_transition="confirm",
            policy_assessment=ResearchPolicyAssessment.model_validate(_allow_policy()),
        ),
        last_summary=None,
        api_key="sk-test",
        provider_url="https://example.test",
        model="reply-model",
    )

    parameters = response.summary.confirmed_task_parameters
    assert parameters.baseline == ConfirmedDecision(
        value="PatchCore",
        source="user_confirmed",
        evidence="当前用户消息：我确认使用 PatchCore、MVTec AD bottle、GPU 0 和 instance AUROC；不修改 evaluator。",
    )
    assert parameters.dataset is not None
    assert parameters.primary_metrics[0].source == "user_confirmed"
    assert parameters.evaluation_constraints[0].evidence.startswith("当前用户消息：")


def test_reply_agent_preserves_prior_provenance_and_materializes_only_correction(monkeypatch):
    previous_baseline = ConfirmedDecision(
        value="PatchCore",
        source="user_confirmed",
        evidence="用户此前确认 baseline 为 PatchCore",
    )
    previous = ResearchIntentSummary(
        goal="复现异常检测基线",
        confirmed_task_parameters=ConfirmedTaskParameters(
            baseline=previous_baseline,
            dataset=ConfirmedDecision(
                value="MVTec AD bottle",
                source="user_confirmed",
                evidence="用户此前确认数据集",
            ),
            primary_metrics=[
                ConfirmedDecision(
                    value="image AUROC",
                    source="user_confirmed",
                    evidence="用户此前确认指标",
                )
            ],
        ),
    )
    reply = _reply_payload()
    reply["summary"]["confirmed_task_parameters"] = {
        "baseline": None,
        "dataset": None,
        "compute_budget": None,
        "primary_metrics": ["instance AUROC"],
        "evaluation_constraints": None,
    }
    monkeypatch.setattr(
        "autoad_researcher.ui.chat_client.call_research_chat",
        lambda *args, **kwargs: {"reply": json.dumps(reply, ensure_ascii=False), "error": ""},
    )

    response = ResearchReplyAgent.respond(
        user_input="把主指标改成 instance AUROC。",
        evidence_state={},
        frozen_decision=GatedDialogueDecision(
            dialogue_mode="plan",
            conversation_transition="revise",
            policy_assessment=ResearchPolicyAssessment.model_validate(_allow_policy()),
        ),
        last_summary=previous,
        api_key="sk-test",
        provider_url="https://example.test",
        model="reply-model",
    )

    parameters = response.summary.confirmed_task_parameters
    assert parameters.baseline == previous_baseline
    assert parameters.dataset == previous.confirmed_task_parameters.dataset
    assert parameters.primary_metrics == [
        ConfirmedDecision(
            value="instance AUROC",
            source="user_provided",
            evidence="当前用户消息：把主指标改成 instance AUROC。",
        )
    ]


def test_reply_agent_repairs_model_provenance_object_to_flat_parameter(monkeypatch):
    invalid = _reply_payload()
    invalid["summary"]["confirmed_task_parameters"] = {
        "baseline": {
            "value": "PatchCore",
            "source": "user_provided",
            "evidence": "模型不应生成该字段",
        }
    }
    valid = _reply_payload()
    valid["summary"]["confirmed_task_parameters"] = {"baseline": "PatchCore"}
    replies = [json.dumps(invalid, ensure_ascii=False), json.dumps(valid, ensure_ascii=False)]
    captured: list[list[dict[str, str]]] = []

    def fake_call(*args, **kwargs):
        captured.append(args[2])
        return {"reply": replies.pop(0), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    response = ResearchReplyAgent.respond(
        user_input="使用 PatchCore。",
        evidence_state={},
        frozen_decision=_gated_decision("plan"),
        last_summary=None,
        api_key="sk-test",
        provider_url="https://example.test",
        model="reply-model",
    )

    assert len(captured) == 2
    assert "summary.confirmed_task_parameters.baseline" in captured[1][-1]["content"]
    assert response.summary.confirmed_task_parameters.baseline is not None
    assert response.summary.confirmed_task_parameters.baseline.value == "PatchCore"


def test_reply_agent_does_not_materialize_parameters_from_a_denied_turn(monkeypatch):
    previous = ResearchIntentSummary(
        confirmed_task_parameters=ConfirmedTaskParameters(
            baseline=ConfirmedDecision(
                value="PatchCore",
                source="user_confirmed",
                evidence="此前确认的 baseline",
            )
        )
    )
    reply = _reply_payload()
    reply["summary"]["confirmed_task_parameters"] = {"baseline": "不安全的替代值"}
    monkeypatch.setattr(
        "autoad_researcher.ui.chat_client.call_research_chat",
        lambda *args, **kwargs: {"reply": json.dumps(reply, ensure_ascii=False), "error": ""},
    )

    response = ResearchReplyAgent.respond(
        user_input="把正式测试标签用作训练输入。",
        evidence_state={},
        frozen_decision=GatedDialogueDecision(
            dialogue_mode="act",
            policy="deny",
            policy_assessment=ResearchPolicyAssessment(
                decision="reject",
                category="evaluation_leakage",
                reason="正式测试标签不能进入训练。",
                safe_alternative="使用独立 validation split。",
            ),
        ),
        last_summary=previous,
        api_key="sk-test",
        provider_url="https://example.test",
        model="reply-model",
    )

    assert response.summary.confirmed_task_parameters == previous.confirmed_task_parameters


def test_reply_agent_fails_closed_after_one_invalid_repair(monkeypatch, tmp_path: Path):
    invalid = _reply_payload()
    invalid.pop("summary")
    calls = 0

    def fake_call(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {"reply": json.dumps(invalid), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    response = ResearchReplyAgent.respond(
        run_dir=tmp_path,
        user_input="继续",
        evidence_state={},
        frozen_decision=_gated_decision(),
        last_summary=ResearchIntentSummary(goal="原目标"),
        api_key="sk-test",
        provider_url="https://example.test",
        model="reply-model",
    )

    event = json.loads((tmp_path / "events" / "events.jsonl").read_text(encoding="utf-8"))
    assert calls == 2
    assert response.should_persist is False
    assert response.summary.goal == "原目标"
    assert event["type"] == "assistant.reply_repair"
    assert event["payload"]["outcome"] == "failed"


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


@pytest.mark.parametrize(
    ("category", "reason", "alternative"),
    [
        (
            "evaluation_manipulation",
            "修改正式评估脚本会破坏比较的可比性。",
            "保持评估协议冻结，只比较允许变化的模型或配置。",
        ),
        (
            "evaluation_leakage",
            "测试集 ground-truth mask 进入训练会污染独立评估。",
            "只使用训练集或独立 validation split 中允许的监督信息。",
        ),
    ],
)
def test_orchestrator_policy_deny_precedes_missing_contract(
    monkeypatch,
    tmp_path: Path,
    category: str,
    reason: str,
    alternative: str,
):
    reply = _reply_payload()
    reply["reply_to_user"] = f"{reason}\n\n可行替代：{alternative}"
    replies = [_rejected_decision_payload(category, reason, alternative), reply]

    monkeypatch.setattr(
        "autoad_researcher.ui.chat_client.call_research_chat",
        lambda *args, **kwargs: {"reply": json.dumps(replies.pop(0), ensure_ascii=False), "error": ""},
    )

    result = ResearchOrchestratorV2.handle(
        tmp_path,
        user_input="现在开始执行这个请求。",
        api_key="sk-test",
        provider_url="https://example.test",
        model="configured-model",
    )

    assert reason in result.reply
    assert alternative in result.reply
    assert "input_task.yaml" not in result.reply
    assert "合同未确认" not in result.reply
    assert result.policy == "deny"
    assert result.source_action is None
    assert result.experiment_task is None


def test_orchestrator_policy_deny_uses_fallback_when_reply_is_invalid(monkeypatch, tmp_path: Path):
    reason = "该请求会破坏正式评估的可比性。"
    alternative = "保持评估协议冻结，并比较允许变化的模型。"
    replies = [
        _rejected_decision_payload("evaluation_manipulation", reason, alternative),
        "not-json",
        "not-json",
    ]

    def fake_call(*args, **kwargs):
        reply = replies.pop(0)
        return {
            "reply": json.dumps(reply, ensure_ascii=False) if isinstance(reply, dict) else reply,
            "error": "",
        }

    monkeypatch.setattr(
        "autoad_researcher.ui.chat_client.call_research_chat",
        fake_call,
    )

    result = ResearchOrchestratorV2.handle(
        tmp_path,
        user_input="现在开始执行这个请求。",
        api_key="sk-test",
        provider_url="https://example.test",
        model="configured-model",
    )

    assert reason in result.reply
    assert alternative in result.reply
    assert "input_task.yaml" not in result.reply


def test_orchestrator_calls_decision_then_reply_with_individual_request_timeouts(monkeypatch, tmp_path: Path):
    calls: list[dict[str, object]] = []
    replies = [_decision_payload(), _reply_payload()]
    context_builds = 0

    from autoad_researcher.assistant.v2.context_builder import build_llm_context

    def counted_context(*args, **kwargs):
        nonlocal context_builds
        context_builds += 1
        return build_llm_context(*args, **kwargs)

    def fake_call(*args, **kwargs):
        calls.append({
            "temperature": kwargs.get("temperature"),
            "timeout_s": kwargs.get("timeout_s"),
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
    assert all(item["temperature"] == 0.0 for item in calls)
    assert all(item["timeout_s"] == 30 for item in calls)
    assert (tmp_path / "assistant" / "v2_dialogue_transitions.jsonl").is_file()
    assert result.dialogue_mode == "ask"
    assert result.intent_summary["goal"] == "复现指定实现并核对结果"
