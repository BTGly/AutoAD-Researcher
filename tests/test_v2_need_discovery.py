from __future__ import annotations

import json

from autoad_researcher.assistant.v2.need_discovery import (
    canonicalize_metrics,
    discover_required_needs,
    discover_required_needs_with_llm,
)


def _need(spec, name: str):
    for need in spec.needs:
        if need.name == name:
            return need
    raise AssertionError(f"missing need: {name}")


def test_need_discovery_patchcore_mvtec_plan_only():
    spec = discover_required_needs(
        user_input="我想基于 PatchCore 做异常检测改进，主要想提升 MVTec AD 上的效果，先不要自动改代码，先帮我整理方案。",
    )

    assert spec.inferred_task_type == "image_anomaly_detection_improvement"
    assert _need(spec, "baseline").current_value == "PatchCore"
    assert _need(spec, "dataset").current_value == "MVTec AD"
    assert _need(spec, "execution_mode").current_value == "plan_only"
    assert "improvement_idea" not in spec.blocking_needs
    assert "target_module" not in spec.blocking_needs
    assert set(spec.blocking_needs).issubset({"metrics", "success_criteria"})


def test_need_discovery_does_not_require_improvement_idea():
    spec = discover_required_needs(
        user_input="我想基于 PatchCore 提升 MVTec AD，主要看 image AUROC，成功标准是比原始 baseline 提升。",
    )

    improvement = _need(spec, "improvement_idea")
    assert improvement.necessity == "optional"
    assert improvement.blocking is False
    assert "improvement_idea" not in spec.blocking_needs


def test_need_discovery_does_not_require_target_module():
    spec = discover_required_needs(
        user_input="我想基于 PatchCore 提升 MVTec AD，主要看 image AUROC，成功标准是比原始 baseline 提升。",
    )

    target_module = _need(spec, "target_module")
    assert target_module.necessity == "optional"
    assert target_module.blocking is False
    assert "target_module" not in spec.blocking_needs


def test_directional_metric_improvement_is_sufficient_plan_success_criteria():
    spec = discover_required_needs(
        user_input=(
            "我想基于 PatchCore 改进异常检测，在 MVTec AD 上测试，主要指标看 image-level AUROC，"
            "目标是在相同评估协议下提升指标。"
        ),
    )

    success = _need(spec, "success_criteria")
    assert success.current_value
    assert success.blocking is False
    assert "success_criteria" not in spec.blocking_needs
    assert spec.ready_for_plan is True


def test_llm_cannot_block_plan_on_missing_numeric_improvement_target(monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
        return {"reply": json.dumps(_base_llm_spec([
            {
                "name": "success_criteria",
                "category": "evaluation",
                "required_for": "plan",
                "necessity": "required_now",
                "current_value": None,
                "source": "unknown",
                "confidence": 0.0,
                "blocking": True,
                "question_to_user": "具体要提升多少 AUROC？",
            }
        ]), ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    spec = discover_required_needs_with_llm(
        user_input="目标是在相同评估协议下提升 image-level AUROC。",
        current_stage_goal="generate_plan",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    success = _need(spec, "success_criteria")
    assert success.current_value == "improve selected metrics under the same evaluation protocol"
    assert success.blocking is False
    assert success.question_to_user is None
    assert spec.blocking_needs == []
    assert spec.ready_for_plan is True


def test_need_discovery_metrics_co_primary():
    spec = discover_required_needs(
        user_input="主要看 image AUROC 和 pixel AUROC，成功标准是都比原始 PatchCore 有提升。",
        transcript_tail=[
            {"role": "user", "content": "baseline 是 PatchCore，数据集 MVTec AD。"},
        ],
    )

    assert _need(spec, "metrics").current_value == ["image_level_auroc", "pixel_level_auroc"]
    assert "metrics" not in spec.blocking_needs


def test_need_discovery_preserves_existing_values():
    spec = discover_required_needs(
        user_input="主要看 image AUROC，成功标准是比原始 baseline 提升。",
        existing_contract_draft={
            "research_goal": "提升 baseline 在目标数据集上的表现",
            "baseline": "PatchCore",
            "dataset": "MVTec AD",
            "execution_mode": "plan_only",
        },
    )

    assert _need(spec, "dataset").current_value == "MVTec AD"
    assert "dataset" not in spec.blocking_needs


def test_need_discovery_stage_sensitive():
    plan_spec = discover_required_needs(
        user_input="我想基于 PatchCore 提升 MVTec AD，主要看 image AUROC，成功标准是比原始 baseline 提升。",
        current_stage_goal="generate_plan",
    )
    run_spec = discover_required_needs(
        user_input="我想基于 PatchCore 提升 MVTec AD，主要看 image AUROC，成功标准是比原始 baseline 提升。",
        current_stage_goal="run_experiment",
    )

    assert "dataset_path" not in plan_spec.blocking_needs
    assert "python_env" not in plan_spec.blocking_needs
    assert "time_budget" not in plan_spec.blocking_needs
    assert "dataset_path" in run_spec.blocking_needs
    assert "python_env" in run_spec.blocking_needs
    assert "time_budget" in run_spec.blocking_needs


def test_metric_canonicalization_keeps_generic_auc_compat_without_pixel_leakage():
    assert canonicalize_metrics("看 AUROC") == ["image_level_auroc"]
    assert canonicalize_metrics("看 pixel AUROC") == ["pixel_level_auroc"]


def test_llm_need_discovery_can_omit_dataset_without_system_forcing_it(monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
        return {
            "reply": json.dumps({
                "task_summary": "诊断代码报错",
                "inferred_task_type": "code_diagnosis",
                "current_stage_goal": "clarify_intent",
                "needs": [
                    {
                        "name": "error_log",
                        "category": "material",
                        "required_for": "chat",
                        "necessity": "required_now",
                        "current_value": None,
                        "source": "unknown",
                        "confidence": 0.0,
                        "blocking": True,
                        "question_to_user": "请贴出完整报错栈。",
                    }
                ],
                "blocking_needs": ["error_log"],
                "next_best_question": "请贴出完整报错栈。",
                "ready_for_plan": False,
                "ready_for_repo_analysis": False,
                "ready_for_experiment_design": False,
                "ready_for_patch": False,
                "ready_for_run": False,
            }, ensure_ascii=False),
            "error": "",
        }

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    spec = discover_required_needs_with_llm(
        user_input="这里报错了，帮我看",
        current_stage_goal="clarify_intent",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert spec.inferred_task_type == "code_diagnosis"
    assert "dataset" not in [need.name for need in spec.needs]
    assert spec.blocking_needs == ["error_log"]


def test_llm_need_discovery_validator_downgrades_improvement_idea_blocking(monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
        return {"reply": json.dumps(_base_llm_spec([
            {
                "name": "improvement_idea",
                "category": "intent",
                "required_for": "plan",
                "necessity": "required_now",
                "current_value": None,
                "source": "unknown",
                "confidence": 0.0,
                "blocking": True,
                "question_to_user": "你准备用什么改进方法？",
            }
        ]), ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    spec = discover_required_needs_with_llm(
        user_input="我想先整理方案",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert _need(spec, "improvement_idea").necessity == "optional"
    assert _need(spec, "improvement_idea").blocking is False
    assert spec.blocking_needs == []


def test_llm_need_discovery_validator_downgrades_plan_gpu_requirement(monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
        return {"reply": json.dumps(_base_llm_spec([
            {
                "name": "gpu",
                "category": "environment",
                "required_for": "run",
                "necessity": "required_now",
                "current_value": None,
                "source": "unknown",
                "confidence": 0.0,
                "blocking": True,
                "question_to_user": "你用什么 GPU？",
            }
        ]), ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    spec = discover_required_needs_with_llm(
        user_input="先帮我整理方案",
        current_stage_goal="generate_plan",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert _need(spec, "gpu").necessity == "required_later"
    assert _need(spec, "gpu").blocking is False
    assert "gpu" not in spec.blocking_needs


def test_llm_need_discovery_validator_blocks_missing_run_requirements(monkeypatch):
    def fake_call(api_key, provider_base_url, messages, **kwargs):
        return {"reply": json.dumps(_base_llm_spec([]) | {
            "current_stage_goal": "run_experiment",
        }, ensure_ascii=False), "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)

    spec = discover_required_needs_with_llm(
        user_input="现在开始跑实验",
        current_stage_goal="run_experiment",
        api_key="sk-test",
        provider_url="https://example.test",
    )

    assert "dataset_path" in spec.blocking_needs
    assert "python_env" in spec.blocking_needs
    assert "time_budget" in spec.blocking_needs


def test_need_discovery_without_api_key_uses_deterministic_fallback():
    spec = discover_required_needs_with_llm(
        user_input="我想基于 PatchCore 提升 MVTec AD，主要看 image AUROC，成功标准是比原始 baseline 提升。",
    )

    assert spec.inferred_task_type == "image_anomaly_detection_improvement"
    assert _need(spec, "baseline").current_value == "PatchCore"
    assert _need(spec, "dataset").current_value == "MVTec AD"


def _base_llm_spec(needs):
    return {
        "task_summary": "测试任务",
        "inferred_task_type": "general_research",
        "current_stage_goal": "generate_plan",
        "needs": needs,
        "blocking_needs": [need["name"] for need in needs if need.get("blocking")],
        "next_best_question": None,
        "ready_for_plan": False,
        "ready_for_repo_analysis": False,
        "ready_for_experiment_design": False,
        "ready_for_patch": False,
        "ready_for_run": False,
    }
