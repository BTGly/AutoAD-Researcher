from __future__ import annotations

from autoad_researcher.assistant.v2.need_discovery import canonicalize_metrics, discover_required_needs


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
