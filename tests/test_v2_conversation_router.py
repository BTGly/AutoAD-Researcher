from __future__ import annotations

import json
from pathlib import Path

from autoad_researcher.assistant.v2.conversation_router import (
    ConversationRouteDecision,
    _build_conversation_route_messages,
    _validate_route_payload,
    route_conversation_with_llm,
)
from autoad_researcher.assistant.v2.intent_contract import load_contract_draft
from autoad_researcher.assistant.v2.llm_trace_service import TRACE_DIR, TRACE_INDEX
from autoad_researcher.assistant.v2.orchestrator import ResearchOrchestratorV2
from autoad_researcher.assistant.v2.source_action_planner import SourceAction, SourceActionPlan
from autoad_researcher.task_workspace.task_profile import load_task_profile
from autoad_researcher.ui.sources import load_source_registry


def _route_payload(*, user_input: str, contract_action: str = "answer_without_contract_update") -> dict:
    mutating = contract_action == "update_contract"
    return {
        "turn_gate": {
            "turn_type": "contract_update" if mutating else "ordinary_chat",
            "contract_action": contract_action,
            "contract_update_allowed": mutating,
            "need_discovery_allowed": mutating,
            "save_draft_allowed": mutating,
            "confirmation_action_proposal": "none",
            "task_profile_proposal": "general_research",
            "task_profile_evidence": None,
            "requires_need_discovery_enrichment": False,
            "suggested_task_title": None,
            "suggested_task_summary": None,
            "user_intent_summary": user_input,
            "evidence_from_current_turn": [user_input] if mutating else [],
            "evidence_from_context": [],
            "mutation_evidence_from_current_turn": user_input if mutating else None,
            "confidence": 0.9,
            "reason": "replay route",
            "next_reply_instruction": None,
        },
        "source_action_plan": {
            "actions": [],
            "user_visible_summary": "",
            "confidence": 0.9,
            "reason": "no source action",
        },
        "task_profile_proposal": "general_research",
        "task_profile_evidence": None,
        "suggested_task_title": None,
        "suggested_task_summary": None,
        "requires_need_discovery_enrichment": False,
    }


def test_conversation_router_replay_uses_only_safe_local_recovery():
    fixture_path = Path(__file__).parent / "fixtures" / "conversation_router_replay.json"
    cases = json.loads(fixture_path.read_text(encoding="utf-8"))

    for case in cases:
        transcript = (
            [{"role": "user", "content": case["context_user_text"]}]
            if case["context_user_text"] else []
        )
        decision, _errors, recovery = _validate_route_payload(
            case["payload"],
            user_input=case["user_input"],
            transcript_tail=transcript,
            deterministic_source_plan=None,
            repository_hints=[],
        )
        expected = case["expected_action"]
        assert (decision.turn_gate.contract_action if decision is not None else None) == expected, case["name"]
        assert recovery == case["expected_recovery"], case["name"]
        if case["name"] == "update_action_normalizes_inconsistent_flags":
            assert decision is not None
            assert decision.turn_gate.contract_update_allowed is True
            assert decision.turn_gate.need_discovery_allowed is True
            assert decision.turn_gate.save_draft_allowed is True
        if case["name"] == "task_profile_evidence_cannot_authorize_mutation":
            assert decision is not None
            assert decision.task_profile_proposal == "general_research"
            assert decision.suggested_task_title is None


def test_router_schema_instruction_requires_verbatim_complete_mutation_evidence():
    messages = _build_conversation_route_messages(
        user_input="不对啊，我真的想做 AI infra、AI 算子优化、底层的，你有什么建议吗？",
        transcript_tail=[],
        existing_contract_draft=None,
        source_registry=[],
        pending_jobs=[],
        created_sources=[],
        created_jobs=[],
        answerability={},
        deterministic_source_plan=None,
        repository_hints=[],
    )

    assert "copy the complete current user message" in messages[0]["content"]
    assert "Distinguish a correction from pure frustration" in messages[0]["content"]
    assert "identical internal spaces, case, and punctuation" in messages[1]["content"]
    assert "task_profile_evidence never authorizes mutation" in messages[1]["content"]
    assert "correction-to-a-new-research-direction" in messages[1]["content"]
    assert "诊断 Rust 服务在高并发下的内存泄漏" in messages[1]["content"]
    prompt_text = "\n".join(message["content"] for message in messages[:2])
    assert "不对啊，我真的想做 AI infra、AI 算子优化、底层的" not in prompt_text
    assert "AI Infra 与算子优化研究" not in prompt_text
    schema = ConversationRouteDecision.model_json_schema()
    assert "turn_gate" not in schema["properties"]
    assert "contract_mutation_request" in schema["properties"]
    assert "confirmation_request" in schema["properties"]


def test_orthogonal_route_keeps_source_mutation_and_confirmation_dimensions():
    user_input = "登记这个仓库，同时把任务改成复现排序基准，随后让我在弹窗确认。"
    payload = {
        "source_action_plan": {
            "actions": [{
                "action_type": "register_github_repo",
                "target": "https://github.com/example/library-a",
                "source_url": "https://github.com/example/library-a",
                "source_kind": "github_repo",
                "confidence": 0.95,
            }],
            "confidence": 0.95,
            "reason": "The user supplied a repository.",
        },
        "conversation_intents": ["source_request", "research_planning"],
        "contract_mutation_request": {
            "requested": True,
            "full_turn_mutation_evidence": user_input,
            "confidence": 0.95,
            "rationale": "The user changed the research task.",
        },
        "confirmation_request": {
            "requested": True,
            "action": "request_pending",
            "full_turn_mutation_evidence": user_input,
            "confidence": 0.9,
            "rationale": "The user requested the approval UI.",
        },
        "task_identity_proposal": {
            "suggested_title": "Library-A 排序基准复现",
            "suggested_summary": "复现排序基准。",
        },
        "task_profile_proposal": "empirical_model_research",
        "task_profile_evidence": "复现排序基准",
        "requires_need_discovery_enrichment": True,
    }

    decision, errors, recovery = _validate_route_payload(
        payload,
        user_input=user_input,
        transcript_tail=[],
        deterministic_source_plan=None,
        repository_hints=[],
    )

    assert errors == []
    assert recovery == []
    assert decision is not None
    assert [action.action_type for action in decision.source_action_plan.actions] == ["register_github_repo"]
    assert decision.contract_mutation_request.requested is True
    assert decision.confirmation_request.action == "request_pending"
    assert decision.turn_gate.contract_action == "update_contract"


def test_deterministic_source_plan_cannot_be_replaced_by_router_payload():
    deterministic = SourceActionPlan(
        actions=[
            SourceAction(
                action_type="register_webpage",
                target="https://example.test/paper",
                source_url="https://example.test/paper",
                source_kind="webpage",
                confidence=1.0,
            )
        ],
        confidence=1.0,
        reason="explicit URL",
    )
    payload = _route_payload(user_input="请把这个链接作为候选资料")
    payload["source_action_plan"] = {
        "actions": [
            {
                "action_type": "web_search",
                "query": "unrelated replacement",
                "confidence": 1.0,
            }
        ]
    }

    decision, errors, _recovery = _validate_route_payload(
        payload,
        user_input="请把这个链接作为候选资料",
        transcript_tail=[],
        deterministic_source_plan=deterministic,
        repository_hints=[],
    )

    assert errors == []
    assert decision is not None
    assert decision.source_action_plan == deterministic


def test_router_top_level_task_profile_is_authoritative_and_evidence_validated():
    user_input = "我想优化这个 CUDA 算子"
    payload = _route_payload(user_input=user_input, contract_action="update_contract")
    payload.update({
        "task_profile_proposal": "systems_optimization",
        "task_profile_evidence": "CUDA 算子",
        "requires_need_discovery_enrichment": True,
    })
    payload["turn_gate"].update({
        "task_profile_proposal": "empirical_model_research",
        "task_profile_evidence": "不存在的模型证据",
    })

    decision, errors, _recovery = _validate_route_payload(
        payload,
        user_input=user_input,
        transcript_tail=[],
        deterministic_source_plan=None,
        repository_hints=[],
    )

    assert errors == []
    assert decision is not None
    assert decision.task_profile_proposal == "systems_optimization"
    assert decision.turn_gate.task_profile_proposal == "systems_optimization"
    assert decision.turn_gate.task_profile_evidence == "CUDA 算子"
    assert decision.requires_need_discovery_enrichment is True


def test_router_rejects_joke_misrouted_as_contract_update_without_mutation_evidence():
    user_input = "你是 PatchCore 战神哈哈哈"
    payload = _route_payload(user_input=user_input, contract_action="update_contract")
    payload["turn_gate"].update({
        "task_profile_proposal": "empirical_model_research",
        "task_profile_evidence": "PatchCore",
        "mutation_evidence_from_current_turn": None,
    })
    payload.update({
        "task_profile_proposal": "empirical_model_research",
        "task_profile_evidence": "PatchCore",
        "suggested_task_title": "PatchCore 研究",
        "suggested_task_summary": "研究 PatchCore。",
    })

    decision, errors, recovery = _validate_route_payload(
        payload,
        user_input=user_input,
        transcript_tail=[],
        deterministic_source_plan=None,
        repository_hints=[],
    )

    assert errors == []
    assert decision is not None
    assert decision.turn_gate.contract_action == "answer_without_contract_update"
    assert decision.task_profile_proposal == "general_research"
    assert decision.suggested_task_title is None
    assert recovery == ["missing_exact_mutation_evidence"]


def test_router_rejects_profile_name_as_contract_mutation_authorization():
    user_input = "PatchCore"
    payload = _route_payload(user_input=user_input, contract_action="update_contract")
    payload["turn_gate"].update({
        "task_profile_proposal": "empirical_model_research",
        "task_profile_evidence": "PatchCore",
        "mutation_evidence_from_current_turn": None,
    })
    payload.update({
        "task_profile_proposal": "empirical_model_research",
        "task_profile_evidence": "PatchCore",
    })

    decision, errors, recovery = _validate_route_payload(
        payload,
        user_input=user_input,
        transcript_tail=[],
        deterministic_source_plan=None,
        repository_hints=[],
    )

    assert errors == []
    assert decision is not None
    assert decision.turn_gate.contract_action == "answer_without_contract_update"
    assert recovery == ["missing_exact_mutation_evidence"]


def test_misrouted_patchcore_joke_updates_neither_contract_nor_task_title(monkeypatch, tmp_path: Path):
    user_input = "你是 PatchCore 战神哈哈哈"
    unsafe_payload = _route_payload(user_input=user_input, contract_action="update_contract")
    unsafe_payload["turn_gate"].update({
        "task_profile_proposal": "empirical_model_research",
        "task_profile_evidence": "PatchCore",
        "mutation_evidence_from_current_turn": None,
    })
    unsafe_payload.update({
        "task_profile_proposal": "empirical_model_research",
        "task_profile_evidence": "PatchCore",
        "suggested_task_title": "PatchCore 研究",
        "suggested_task_summary": "研究 PatchCore。",
    })

    def fake_call(api_key, provider_base_url, messages, **kwargs):
        if "ConversationRouter" in messages[0]["content"]:
            return {"reply": json.dumps(unsafe_payload, ensure_ascii=False), "error": ""}
        return {
            "reply": json.dumps({
                "reply_to_user": "哈哈，先不改研究任务。",
                "contract_updates": {},
                "new_user_confirmed_fields": [],
                "missing_required_fields": [],
                "primary_metrics": [],
                "secondary_metrics": [],
                "metric_priority": None,
                "optional_hints_detected": {},
                "next_question": "",
                "ready_for_confirmation": False,
                "ready_for_experiment_agents": False,
            }, ensure_ascii=False),
            "error": "",
        }

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    run_dir = tmp_path / "run_profile_evidence_boundary"
    run_dir.mkdir()

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input=user_input,
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert result.intent_contract == {}
    assert result.task_update == {}
    assert load_contract_draft(run_dir) is None
    assert load_task_profile(run_dir) is None


def test_router_calls_provider_once_and_records_registered_trace(monkeypatch, tmp_path: Path):
    calls = 0

    def fake_call(api_key, provider_base_url, messages, **kwargs):
        nonlocal calls
        calls += 1
        assert "ConversationRouter" in messages[0]["content"]
        assert kwargs["priority"] == "routing"
        assert kwargs["response_format_json"] is True
        return {"reply": json.dumps(_route_payload(user_input="随便聊聊"), ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    run_dir = tmp_path / "run_router_trace"
    run_dir.mkdir()

    decision = route_conversation_with_llm(
        run_dir=run_dir,
        user_input="随便聊聊",
        transcript_tail=[],
        existing_contract_draft=None,
        source_registry=[],
        pending_jobs=[],
        created_sources=[],
        created_jobs=[],
        answerability={},
        api_key="sk-test",
        provider_url="https://user:sk-secret@example.test/v1?api_key=sk-query-secret",
    )

    assert calls == 1
    assert decision.turn_gate.contract_action == "answer_without_contract_update"
    trace_path = run_dir / TRACE_DIR / TRACE_INDEX
    trace = json.loads(trace_path.read_text(encoding="utf-8").splitlines()[0])
    assert trace["call_site"] == "conversation_router"
    assert trace["prompt_id"] == "assistant.v2.conversation_route.v1"
    assert trace["schema_validation"] == "ok"
    assert trace["provider_url_host"] == "example.test"
    raw_trace = trace_path.read_text(encoding="utf-8")
    assert "sk-secret" not in raw_trace
    assert "sk-query-secret" not in raw_trace


def test_invalid_router_output_fails_closed_without_repair(monkeypatch, tmp_path: Path):
    calls = 0

    def fake_call(api_key, provider_base_url, messages, **kwargs):
        nonlocal calls
        calls += 1
        return {"reply": '{"turn_gate":{"turn_type":"research"}}', "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    run_dir = tmp_path / "run_router_invalid"
    run_dir.mkdir()

    decision = route_conversation_with_llm(
        run_dir=run_dir,
        user_input="继续",
        transcript_tail=[],
        existing_contract_draft=None,
        source_registry=[],
        pending_jobs=[],
        created_sources=[],
        created_jobs=[],
        answerability={},
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert calls == 1
    assert decision.turn_gate.contract_action == "answer_without_contract_update"
    assert decision.turn_gate.contract_update_allowed is False


def test_orchestrator_uses_one_router_and_no_legacy_semantic_planners(monkeypatch, tmp_path: Path):
    call_sites: list[str] = []

    def fake_call(api_key, provider_base_url, messages, **kwargs):
        system = messages[0]["content"]
        if "ConversationRouter" in system:
            call_sites.append("conversation_router")
            return {"reply": json.dumps(_route_payload(user_input="今天随便聊聊"), ensure_ascii=False), "error": ""}
        if "SourceActionPlanner" in system:
            call_sites.append("legacy_source_action_planner")
            raise AssertionError("legacy SourceActionPlanner must not run from the Orchestrator")
        if "HF-2 Turn Gate" in system:
            call_sites.append("legacy_turn_gate")
            raise AssertionError("legacy Turn Gate must not run from the Orchestrator")
        call_sites.append("reply_planner")
        return {
            "reply": json.dumps({
                "reply_to_user": "可以，想聊什么都行。",
                "contract_updates": {},
                "new_user_confirmed_fields": [],
                "missing_required_fields": [],
                "primary_metrics": [],
                "secondary_metrics": [],
                "metric_priority": None,
                "optional_hints_detected": {},
                "next_question": "",
                "ready_for_confirmation": False,
                "ready_for_experiment_agents": False,
            }, ensure_ascii=False),
            "error": "",
        }

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    run_dir = tmp_path / "run_structural_dedup"
    run_dir.mkdir()

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="今天随便聊聊",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert result.reply == "可以，想聊什么都行。"
    assert call_sites.count("conversation_router") == 1
    assert "legacy_source_action_planner" not in call_sites
    assert "legacy_turn_gate" not in call_sites
    assert "reply_planner" in call_sites


def test_pure_url_and_attachment_bypass_router_even_when_model_is_configured(monkeypatch, tmp_path: Path):
    def unexpected_call(*args, **kwargs):
        raise AssertionError("structured source ingress must not call a semantic model")

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", unexpected_call)

    url_run = tmp_path / "run_pure_url"
    url_run.mkdir()
    url_result = ResearchOrchestratorV2.handle(
        url_run,
        user_input="https://example.test/paper?lang=zh#methods",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    attachment_run = tmp_path / "run_attachment"
    attachment_run.mkdir()
    attachment_result = ResearchOrchestratorV2.handle(
        attachment_run,
        user_input="请读取附件",
        attachments=["paper.pdf"],
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert url_result.reply_kind == "source_intake"
    assert attachment_result.reply_kind == "source_intake"


def test_url_with_natural_language_registers_first_then_routes_once(monkeypatch, tmp_path: Path):
    router_calls = 0

    def fake_call(api_key, provider_base_url, messages, **kwargs):
        nonlocal router_calls
        assert "ConversationRouter" in messages[0]["content"]
        router_calls += 1
        context = json.loads(messages[2]["content"].removeprefix("Context JSON:\n"))
        assert context["deterministic_source_plan"]["actions"][0]["source_url"] == "https://example.test/paper"
        assert context["created_sources"]
        return {
            "reply": json.dumps(
                _route_payload(user_input="请登记 https://example.test/paper 但不要把内容写入研究合同"),
                ensure_ascii=False,
            ),
            "error": "",
        }

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    run_dir = tmp_path / "run_url_binding"
    run_dir.mkdir()

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="请登记 https://example.test/paper 但不要把内容写入研究合同",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert router_calls == 1
    assert result.reply_kind == "source_intake"
    assert result.intent_contract == {}
    assert load_source_registry(run_dir)["sources"][0]["user_label"] == "https://example.test/paper"
