from __future__ import annotations

import json
from pathlib import Path

from autoad_researcher.assistant.v2.intent_contract import (
    CONTRACT_DRAFT_FILE,
    CONTRACT_FILE,
    DEFAULT_FORBIDDEN_CHANGE_SCOPE,
    ResearchIntentContract,
    build_contract_from_context,
    contract_fields_from_need_spec,
    format_contract_for_user,
    load_confirmed_contract,
    merge_contract_draft,
    save_contract_draft,
)
from autoad_researcher.assistant.v2.need_discovery import RequiredNeedSpec
from autoad_researcher.assistant.v2.orchestrator import ResearchOrchestratorV2
from autoad_researcher.assistant.v2.reply_planner import plan_reply


def test_research_intent_contract_defaults_do_not_require_method_or_target_module():
    contract = ResearchIntentContract(run_id="run_contract")

    assert contract.task_domain == "anomaly_detection"
    assert contract.execution_mode == "plan_only"
    assert contract.user_improvement_hints == []
    assert contract.user_target_module_hints == []
    assert "modify_test_labels" in contract.forbidden_change_scope
    assert "change_metric_definition" in contract.forbidden_change_scope
    assert set(DEFAULT_FORBIDDEN_CHANGE_SCOPE).issubset(set(contract.forbidden_change_scope))


def test_build_contract_ready_for_plan_without_improvement_or_target_module(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()
    llm_context = {
        "confirmed_from_user": {
            "baseline": "PatchCore",
            "dataset": "MVTec AD",
            "metrics": ["image_level_auroc"],
        },
        "usable_evidence": [],
    }

    contract = build_contract_from_context(
        run_dir=run_dir,
        user_input="我的目标是提升指标效果，保持 baseline 原始评价协议。",
        llm_context=llm_context,
    )

    assert contract.research_goal == "提升 baseline 在目标数据集上的表现"
    assert contract.baseline == "PatchCore"
    assert contract.dataset == "MVTec AD"
    assert contract.primary_metrics == ["image_level_auroc"]
    assert contract.primary_metric == "image_level_auroc"
    assert contract.success_criteria == "improve selected metrics under the same evaluation protocol"
    assert contract.user_improvement_hints == []
    assert contract.user_target_module_hints == []
    assert contract.ready_for_plan is True
    assert contract.ready_for_experiment_agents is False
    assert contract.missing_required_fields == []


def test_build_contract_keeps_repo_analysis_readiness_separate(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    sources = run_dir / "sources"
    sources.mkdir(parents=True)
    (sources / "source_references.json").write_text(
        json.dumps({
            "schema_version": 1,
            "sources": [
                {
                    "source_id": "src_repo",
                    "kind": "github_repo",
                    "user_label": "https://github.com/example/repo",
                    "status": "user_provided_not_ingested",
                }
            ],
        }),
        encoding="utf-8",
    )

    contract = build_contract_from_context(
        run_dir=run_dir,
        user_input="我想提升 PatchCore 在 MVTec AD 上的 image AUROC。",
        llm_context={"confirmed_from_user": {}},
    )

    assert contract.ready_for_plan is True
    assert contract.ready_for_repo_analysis is True
    assert contract.ready_for_experiment_agents is False
    assert contract.baseline_repo == "https://github.com/example/repo"


def test_orchestrator_writes_draft_then_confirms_existing_contract(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()
    transcript_tail = [
        {"role": "user", "content": "baseline 是 PatchCore，数据集 MVTec AD，指标 image AUROC"},
    ]

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="我的目标是提升指标效果，保持 baseline 原始评价协议。",
        transcript_tail=transcript_tail,
    )

    assert result.reply_kind == "intent_contract_confirmation"
    assert result.intent_contract["ready_for_plan"] is True
    assert (run_dir / CONTRACT_DRAFT_FILE).is_file()
    assert not (run_dir / CONTRACT_FILE).exists()
    assert "如果以上正确，请回复“确认”" in result.reply

    confirmed = ResearchOrchestratorV2.handle(run_dir, user_input="确认")

    assert confirmed.reply_kind == "intent_contract_confirmed"
    assert confirmed.intent_contract_confirmed is True
    assert (run_dir / CONTRACT_FILE).is_file()
    loaded = load_confirmed_contract(run_dir)
    assert loaded is not None
    assert loaded.ready_for_plan is True


def test_format_contract_does_not_pressure_user_for_method_or_module():
    contract = ResearchIntentContract(
        run_id="run_contract",
        research_goal="提升 baseline 在目标数据集上的表现",
        baseline="PatchCore",
        dataset="MVTec AD",
        primary_metrics=["image_level_auroc"],
        primary_metric="image_level_auroc",
        success_criteria="improve image_level_auroc under the same evaluation protocol",
        ready_for_plan=True,
    )

    text = format_contract_for_user(contract)

    assert "未提供；这不阻塞" in text
    assert "后续 experiment agents 会自动探索" in text
    assert "后续 repo/experiment agents 会定位" in text
    assert "你想怎么改" not in text
    assert "你要改哪个模块" not in text


def test_reply_planner_fallback_asks_goal_not_method():
    _kind, reply = plan_reply(
        {
            "answerability": {"blocking_next_step": "intake"},
            "usable_evidence": [],
            "unparsed_sources": [],
            "readable_summaries": [],
        },
        "我想做异常检测",
    )

    assert "不需要你先设计具体方法" in reply
    assert "主要目标" in reply
    assert "你想怎么改" not in reply
    assert "你要改哪个模块" not in reply


def test_reply_planner_llm_prompt_requires_structured_json(monkeypatch):
    captured: dict[str, object] = {}

    def fake_call(api_key, provider_base_url, messages, **kwargs):
        captured["messages"] = messages
        return {
            "reply": json.dumps({
                "reply_to_user": "请确认主要目标。",
                "contract_updates": {},
                "new_user_confirmed_fields": [],
                "missing_required_fields": ["primary_metrics"],
                "optional_hints_detected": {},
                "next_question": "你主要想优化什么？",
                "ready_for_confirmation": False,
                "ready_for_experiment_agents": False,
            }, ensure_ascii=False),
            "error": None,
        }

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    kind, reply = plan_reply(
        {
            "answerability": {"blocking_next_step": "intake"},
            "confirmed_from_user": {},
            "usable_evidence": [],
            "readable_summaries": [],
            "research_intent_contract": {"run_id": "run_contract", "execution_mode": "plan_only"},
        },
        "我想做异常检测",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    system_text = "\n".join(m["content"] for m in captured["messages"] if m["role"] == "system")
    assert kind == "answer"
    assert "请确认主要目标。" in reply
    assert "你主要想优化什么？" in reply
    assert "reply_to_user" not in reply
    assert "contract_updates" not in reply
    assert "missing_required_fields" not in reply
    assert "每轮必须输出 JSON object" in system_text
    assert "improvement_idea、target_module 只能作为 optional hints" in system_text
    assert "不要问'你想怎么改'" in system_text


def test_hf2_reply_does_not_expose_raw_json(monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
        return {
            "reply": json.dumps({
                "reply_to_user": "我已记录 baseline 和数据集。",
                "contract_updates": {"dataset": "MVTec AD"},
                "missing_required_fields": ["dataset"],
                "next_question": "请确认主要指标。",
            }, ensure_ascii=False),
            "error": None,
        }

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    _kind, reply = plan_reply(
        {
            "answerability": {"blocking_next_step": "intent"},
            "confirmed_from_user": {},
            "usable_evidence": [],
            "readable_summaries": [],
            "research_intent_contract": {"run_id": "run_contract"},
        },
        "我的数据集是 MVTec AD",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert reply == "我已记录 baseline 和数据集。\n\n请确认主要指标。"
    assert "{" not in reply
    assert "contract_updates" not in reply
    assert "missing_required_fields" not in reply


def test_hf2_contract_preserves_dataset_across_turns(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()

    first = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="我想基于 PatchCore 做异常检测改进，主要想提升 MVTec AD 上的效果，先不要自动改代码，先帮我整理方案。",
    )
    assert first.intent_contract["dataset"] == "MVTec AD"

    second = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="主要看 image AUROC 和 pixel AUROC，成功标准是比原始 PatchCore 有提升，评价流程不能作弊，不能改测试集和指标定义。",
    )

    assert second.intent_contract["baseline"] == "PatchCore"
    assert second.intent_contract["dataset"] == "MVTec AD"
    assert second.intent_contract["primary_metrics"] == ["image_level_auroc", "pixel_level_auroc"]
    assert second.intent_contract["primary_metric"] is None
    assert second.intent_contract["secondary_metrics"] == []
    assert second.intent_contract["metric_priority"] == "co_primary"


def test_hf2_contract_ready_after_metric_and_success(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()

    existing = ResearchIntentContract(
        run_id="run_contract",
        research_goal="提升 baseline 在目标数据集上的表现",
        baseline="PatchCore",
        dataset="MVTec AD",
        execution_mode="plan_only",
    )
    update = build_contract_from_context(
        run_dir=run_dir,
        user_input="主要看 image AUROC，成功标准是保持原始评价协议并提升指标。",
        llm_context={"confirmed_from_user": {}},
    )

    merged = merge_contract_draft(existing, update)

    assert merged.ready_for_plan is True
    assert merged.missing_required_fields == []
    assert merged.primary_metrics == ["image_level_auroc"]
    assert merged.primary_metric == "image_level_auroc"
    assert merged.success_criteria == "主要看 image AUROC，成功标准是保持原始评价协议并提升指标。"


def test_hf2_metric_extraction_image_and_pixel_co_primary(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()

    image_primary = build_contract_from_context(
        run_dir=run_dir,
        user_input="主要看 image AUROC 和 pixel AUROC。",
        llm_context={"confirmed_from_user": {}},
    )
    reversed_order = build_contract_from_context(
        run_dir=run_dir,
        user_input="主要看 pixel AUROC 和 image AUROC。",
        llm_context={"confirmed_from_user": {}},
    )

    assert image_primary.primary_metrics == ["image_level_auroc", "pixel_level_auroc"]
    assert image_primary.primary_metric is None
    assert image_primary.secondary_metrics == []
    assert image_primary.metric_priority == "co_primary"
    assert reversed_order.primary_metrics == ["pixel_level_auroc", "image_level_auroc"]
    assert reversed_order.primary_metric is None
    assert reversed_order.secondary_metrics == []
    assert reversed_order.metric_priority == "co_primary"


def test_hf2_metric_extraction_explicit_primary_and_secondary(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()

    image_primary = build_contract_from_context(
        run_dir=run_dir,
        user_input="image AUROC 为主，pixel AUROC 参考。",
        llm_context={"confirmed_from_user": {}},
    )
    pixel_primary = build_contract_from_context(
        run_dir=run_dir,
        user_input="pixel AUROC 为主，image AUROC 参考。",
        llm_context={"confirmed_from_user": {}},
    )

    assert image_primary.primary_metrics == ["image_level_auroc"]
    assert image_primary.primary_metric == "image_level_auroc"
    assert image_primary.secondary_metrics == ["pixel_level_auroc"]
    assert image_primary.metric_priority == "image_level_auroc_first"
    assert pixel_primary.primary_metrics == ["pixel_level_auroc"]
    assert pixel_primary.primary_metric == "pixel_level_auroc"
    assert pixel_primary.secondary_metrics == ["image_level_auroc"]
    assert pixel_primary.metric_priority == "pixel_level_auroc_first"


def test_hf2_llm_missing_fields_not_authoritative(monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
        return {
            "reply": json.dumps({
                "reply_to_user": "已记录。",
                "missing_required_fields": ["dataset", "primary_metrics"],
                "next_question": "还需要确认成功标准。",
            }, ensure_ascii=False),
            "error": None,
        }

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    _kind, reply = plan_reply(
        {
            "answerability": {"blocking_next_step": "intent"},
            "confirmed_from_user": {},
            "usable_evidence": [],
            "readable_summaries": [],
            "research_intent_contract": {
                "run_id": "run_contract",
                "dataset": "MVTec AD",
                "primary_metrics": ["image_level_auroc"],
                "missing_required_fields": ["success_criteria"],
            },
        },
        "继续",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert "dataset" not in reply
    assert "primary_metrics" not in reply
    assert "missing_required_fields" not in reply


def test_need_spec_maps_non_hardcoded_baseline_into_contract(tmp_path: Path, monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
        return {"reply": json.dumps(_need_spec_payload(
            baseline="EfficientAD",
            dataset="VisA",
            metrics=["image_level_auroc", "pixel_level_auroc"],
        ), ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()

    contract = build_contract_from_context(
        run_dir=run_dir,
        user_input="我要做一个视觉异常检测改进任务。",
        llm_context={"confirmed_from_user": {}},
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert contract.baseline == "EfficientAD"
    assert contract.dataset == "VisA"
    assert contract.primary_metrics == ["image_level_auroc", "pixel_level_auroc"]
    assert contract.metric_priority == "co_primary"


def test_hf2_identity_question_does_not_chase_dataset(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()
    save_contract_draft(
        run_dir,
        ResearchIntentContract(
            run_id=run_dir.name,
            research_goal="提升 baseline 在目标数据集上的表现",
            baseline="PatchCore",
            primary_metrics=["image_level_auroc"],
            success_criteria="improve selected metrics",
            ready_for_plan=False,
            missing_required_fields=["dataset"],
        ),
    )

    result = ResearchOrchestratorV2.handle(run_dir, user_input="你是谁？")

    assert result.reply_kind == "answer"
    assert "dataset" not in result.reply
    assert "数据集" in result.reply
    assert "请补充" not in result.reply
    assert result.intent_contract["missing_required_fields"] == ["dataset"]


def test_hf2_user_identity_does_not_update_contract(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()
    save_contract_draft(
        run_dir,
        ResearchIntentContract(
            run_id=run_dir.name,
            baseline="PatchCore",
            missing_required_fields=["dataset"],
        ),
    )

    result = ResearchOrchestratorV2.handle(run_dir, user_input="我是人类！")

    assert result.intent_contract["baseline"] == "PatchCore"
    assert result.intent_contract["dataset"] is None
    assert "请补充" not in result.reply


def test_hf2_playful_message_not_contract_update(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()

    result = ResearchOrchestratorV2.handle(run_dir, user_input="你是无敌美少女")

    assert result.intent_contract == {}
    assert "研究合同" in result.reply
    assert "dataset" not in result.reply


def test_hf2_frustration_not_contract_update(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()

    result = ResearchOrchestratorV2.handle(run_dir, user_input="我草泥马")

    assert result.intent_contract == {}
    assert "请补充" not in result.reply
    assert "dataset" not in result.reply


def test_hf2_research_keyword_joke_without_api_is_unknown_not_contract_update(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()

    result = ResearchOrchestratorV2.handle(run_dir, user_input="你是 PatchCore 战神")

    assert result.intent_contract == {}
    assert "请补充" not in result.reply
    assert "dataset" not in result.reply


def test_hf2_unknown_turn_with_api_can_be_classified_by_need_discovery(tmp_path: Path, monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
        if "Need Discovery" in messages[0]["content"]:
            return {"reply": json.dumps(_need_spec_payload(
                baseline="PatchCore",
                dataset="MVTec AD",
                metrics=["image_level_auroc"],
            ), ensure_ascii=False), "error": ""}
        return {"reply": json.dumps({
            "reply_to_user": "已按刚才的设置整理。",
            "contract_updates": {},
            "new_user_confirmed_fields": [],
            "missing_required_fields": [],
            "optional_hints_detected": {},
            "next_question": "",
            "ready_for_confirmation": True,
            "ready_for_experiment_agents": False,
        }, ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="那就按刚刚那个来吧",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert result.intent_contract["baseline"] == "PatchCore"
    assert result.intent_contract["dataset"] == "MVTec AD"
    assert result.intent_contract["primary_metrics"] == ["image_level_auroc"]


def test_hf2_multi_metric_update_replaces_old_single_primary(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()
    save_contract_draft(
        run_dir,
        ResearchIntentContract(
            run_id=run_dir.name,
            research_goal="提升 baseline 在目标数据集上的表现",
            baseline="PatchCore",
            dataset="MVTec AD",
            primary_metrics=["pixel_level_auroc"],
            primary_metric="pixel_level_auroc",
            metric_priority="single_primary",
            success_criteria="improve pixel_level_auroc",
            execution_mode="plan_only",
        ),
    )

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="主要看 image AUROC 和 pixel AUROC，成功标准是比原始 PatchCore 有提升，评价流程不能作弊，不能改测试集和指标定义。",
    )

    assert result.intent_contract["primary_metrics"] == ["image_level_auroc", "pixel_level_auroc"]
    assert result.intent_contract["primary_metric"] is None
    assert result.intent_contract["metric_priority"] == "co_primary"
    assert result.intent_contract["success_criteria"]
    assert result.intent_contract["evaluation_protocol"] == "keep baseline/original evaluation protocol; no test split or metric changes"


def test_hf2_contract_related_turn_still_asks_missing_fields(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="我想基于 PatchCore 做异常检测改进。",
    )

    assert result.reply_kind == "answer"
    assert "主要目标" in result.reply or "指标" in result.reply
    assert result.intent_contract.get("missing_required_fields")


def test_need_spec_contract_mapping_prefers_source_priority():
    spec = RequiredNeedSpec.model_validate({
        "task_summary": "test",
        "inferred_task_type": "image_anomaly_detection_improvement",
        "current_stage_goal": "generate_plan",
        "needs": [
            {
                "name": "baseline",
                "category": "experiment_object",
                "required_for": "plan",
                "necessity": "required_now",
                "current_value": "EfficientAD",
                "source": "llm_inferred",
                "confidence": 0.6,
                "blocking": False,
                "question_to_user": None,
            },
            {
                "name": "baseline",
                "category": "experiment_object",
                "required_for": "plan",
                "necessity": "required_now",
                "current_value": "PatchCore",
                "source": "user",
                "confidence": 0.95,
                "blocking": False,
                "question_to_user": None,
            },
        ],
        "blocking_needs": [],
        "next_best_question": None,
        "ready_for_plan": True,
        "ready_for_repo_analysis": False,
        "ready_for_experiment_design": True,
        "ready_for_patch": False,
        "ready_for_run": False,
    })

    assert contract_fields_from_need_spec(spec)["baseline"] == "PatchCore"


def test_no_api_key_contract_fallback_still_recognizes_patchcore_mvtec(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()

    contract = build_contract_from_context(
        run_dir=run_dir,
        user_input="我想基于 PatchCore 提升 MVTec AD，主要看 image AUROC，成功标准是比原始 baseline 提升。",
        llm_context={"confirmed_from_user": {}},
    )

    assert contract.baseline == "PatchCore"
    assert contract.dataset == "MVTec AD"
    assert contract.primary_metrics == ["image_level_auroc"]


def test_user_confirmed_field_not_overwritten_by_llm_inferred(tmp_path: Path, monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
        return {"reply": json.dumps(_need_spec_payload(
            baseline="EfficientAD",
            dataset="VisA",
            metrics=["image_level_auroc"],
        ), ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()

    contract = build_contract_from_context(
        run_dir=run_dir,
        user_input="继续这个视觉异常检测任务",
        llm_context={"confirmed_from_user": {"baseline": "PatchCore"}},
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert contract.baseline == "PatchCore"
    assert contract.dataset == "VisA"


def _need_spec_payload(*, baseline: str, dataset: str, metrics: list[str]) -> dict:
    return {
        "task_summary": f"基于 {baseline} 在 {dataset} 上做改进",
        "inferred_task_type": "image_anomaly_detection_improvement",
        "current_stage_goal": "generate_plan",
        "needs": [
            {
                "name": "research_goal",
                "category": "intent",
                "required_for": "plan",
                "necessity": "required_now",
                "current_value": "提升 baseline 在目标数据集上的表现",
                "source": "llm_inferred",
                "confidence": 0.8,
                "blocking": False,
                "question_to_user": None,
            },
            {
                "name": "baseline",
                "category": "experiment_object",
                "required_for": "plan",
                "necessity": "required_now",
                "current_value": baseline,
                "source": "llm_inferred",
                "confidence": 0.8,
                "blocking": False,
                "question_to_user": None,
            },
            {
                "name": "dataset",
                "category": "experiment_object",
                "required_for": "plan",
                "necessity": "required_now",
                "current_value": dataset,
                "source": "llm_inferred",
                "confidence": 0.8,
                "blocking": False,
                "question_to_user": None,
            },
            {
                "name": "metrics",
                "category": "evaluation",
                "required_for": "plan",
                "necessity": "required_now",
                "current_value": metrics,
                "source": "llm_inferred",
                "confidence": 0.8,
                "blocking": False,
                "question_to_user": None,
            },
            {
                "name": "success_criteria",
                "category": "evaluation",
                "required_for": "plan",
                "necessity": "required_now",
                "current_value": "improve selected metrics under the same evaluation protocol",
                "source": "llm_inferred",
                "confidence": 0.8,
                "blocking": False,
                "question_to_user": None,
            },
            {
                "name": "execution_mode",
                "category": "execution",
                "required_for": "plan",
                "necessity": "required_now",
                "current_value": "plan_only",
                "source": "default",
                "confidence": 0.8,
                "blocking": False,
                "question_to_user": None,
            },
        ],
        "blocking_needs": [],
        "next_best_question": None,
        "ready_for_plan": True,
        "ready_for_repo_analysis": False,
        "ready_for_experiment_design": True,
        "ready_for_patch": False,
        "ready_for_run": False,
    }
