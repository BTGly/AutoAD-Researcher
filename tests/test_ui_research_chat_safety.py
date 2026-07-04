"""Tests for research_chat.py safety boundaries."""

import json
from pathlib import Path

from autoad_researcher.ui.chat_prompts import INTENT_CLARIFICATION_PROMPT
from autoad_researcher.ui.intent_draft import (
    ResearchIntentDraft,
    save_clarification_input,
    save_intent_confirmation,
    save_intent_draft,
)
from autoad_researcher.ui.research_chat import (
    _MODE_LABELS,
    _SAFETY_WARNING,
    _extract_intent_draft,
    build_developer_info_payload,
    build_pipeline_input_action,
    build_research_assistant_overview,
    build_user_flow_steps,
    render_intent_draft_markdown,
)
from autoad_researcher.ui.task_profile import TaskProfile, save_task_profile


def test_safety_warning_not_empty():
    assert len(_SAFETY_WARNING) > 10
    assert "不会修改" in _SAFETY_WARNING or "不" in _SAFETY_WARNING


def test_mode_labels_have_all_modes():
    assert set(_MODE_LABELS.keys()) == {
        "intent_clarification", "run_explanation", "next_experiment",
    }


def test_mode_labels_are_chinese():
    for label in _MODE_LABELS.values():
        assert any('\u4e00' <= c <= '\u9fff' for c in label)


def test_extract_intent_draft_parses_research_goal():
    text = """研究目标：降低 PatchCore 运行时峰值显存。

优化指标：
- peak_gpu_memory_mb
- wall_time_seconds

禁止修改范围：
- configs/
- tests/
"""
    draft = _extract_intent_draft(text)
    assert "PatchCore" in draft["research_goal"]
    assert "peak_gpu_memory_mb" in draft["primary_metrics"]
    assert "configs/" in draft["forbidden_change_scope"]


def test_extract_intent_draft_empty_for_none():
    text = "hello world"
    draft = _extract_intent_draft(text)
    assert draft["research_goal"] == ""
    assert draft["primary_metrics"] == []


def test_extract_intent_draft_always_returns_all_keys():
    draft = _extract_intent_draft("")
    expected = {"research_goal", "primary_metrics", "guardrail_metrics",
                "allowed_change_scope", "forbidden_change_scope",
                "success_criteria", "constraints", "user_idea"}
    assert set(draft.keys()) == expected


def _draft(run_id: str = "run_20260704_0250_ee23") -> ResearchIntentDraft:
    return ResearchIntentDraft(
        run_id=run_id,
        research_goal="在 MVTec AD bottle 类别上复现 PatchCore baseline。",
        problem_type="accuracy_improvement",
        primary_metrics=["instance_auroc", "full_pixel_auroc"],
        guardrail_metrics=["anomaly_pixel_auroc"],
        allowed_change_scope=["PatchCore sampler / coreset 相关逻辑", "训练与推理脚本"],
        forbidden_change_scope=["测试代码", "评估器"],
        benchmark_scope={"dataset": "MVTec AD bottle", "baseline": "PatchCore"},
        success_criteria="运行完整、日志可追溯，指标与 baseline 结果进行对比。",
        risks=[],
        open_questions=[],
    )


def test_transcript_display_helper_does_not_require_mode_label():
    # The UI renderer now writes chat content directly; mode remains stored only in transcript metadata.
    source = Path("src/autoad_researcher/ui/research_chat.py").read_text(encoding="utf-8")
    assert "[{entry_mode}]" not in source
    assert "[{mode}]" not in source


def test_user_friendly_intent_draft_markdown_has_no_json_indices():
    markdown = render_intent_draft_markdown(_draft())

    assert "0:" not in markdown
    assert "1:" not in markdown
    assert "图像级 AUROC：instance_auroc" in markdown
    assert "像素级 AUROC：full_pixel_auroc" in markdown
    assert "ui_chat/intent_draft.json" not in markdown


def test_pipeline_input_action_hides_artifact_status_table(tmp_path: Path):
    run_dir = tmp_path / "run_20260704_0250_ee23"

    action = build_pipeline_input_action(run_dir)

    assert action["button_enabled"] is False
    message = action["message"]
    assert "请先确认研究目标" in message
    assert "clarification_input.json" not in message
    assert "intent_confirmation.json" not in message
    assert "input_task.yaml" not in message


def test_pipeline_input_action_enables_after_confirmed_intent(tmp_path: Path):
    run_dir = tmp_path / "run_20260704_0250_ee23"
    draft = _draft(run_id=run_dir.name)
    save_intent_draft(run_dir, draft)
    save_clarification_input(run_dir, draft)
    save_intent_confirmation(run_dir, decision="approved")

    action = build_pipeline_input_action(run_dir)

    assert action["button_enabled"] is True
    assert action["message"] == "研究目标已确认。下一步可以生成实验输入。"


def test_user_flow_hides_raw_gate_stage_names(tmp_path: Path):
    run_dir = tmp_path / "run_20260704_0250_ee23"

    steps = build_user_flow_steps(run_dir)
    text = json.dumps(steps, ensure_ascii=False)

    assert "patch_planner" not in text
    assert "runner_execute" not in text
    assert steps[0]["label"] == "确认研究目标"
    assert steps[0]["state"] == "current"


def test_overview_hides_raw_ids_but_developer_info_keeps_them(tmp_path: Path):
    run_dir = tmp_path / "run_20260704_0250_ee23"
    save_task_profile(
        run_dir,
        TaskProfile(
            run_id=run_dir.name,
            task_title="PatchCore 基线复现",
            task_summary="在 MVTec AD bottle 上复现 PatchCore baseline。",
            source="manual",
        ),
    )
    context = {"available_stages": ["stage3_acceptance"]}

    overview = build_research_assistant_overview(
        run_dir,
        dataset_root="/root/autodl-tmp/mvtec",
        provider_url="https://api.deepseek.com",
        context_data=context,
    )
    public_text = json.dumps({k: v for k, v in overview.items() if k != "developer"}, ensure_ascii=False)
    developer = build_developer_info_payload(
        run_dir,
        overview=overview,
        provider_url="https://api.deepseek.com",
        dataset_root="/root/autodl-tmp/mvtec",
        context_data=context,
    )

    assert "run_20260704_0250_ee23" not in public_text
    assert "/root/autodl-tmp/mvtec" not in public_text
    assert "stage3_acceptance" not in public_text
    assert overview["task_title"] == "PatchCore 基线复现"
    assert overview["dataset_status"] == "已配置"
    assert developer["run_id"] == "run_20260704_0250_ee23"
    assert "ui_chat/intent_draft.json" in developer["raw_artifacts"]
    assert "approval_gate_report.json" in developer["raw_artifacts"]


def test_missing_patch_approval_request_raw_warning_removed():
    source = Path("src/autoad_researcher/ui/research_chat.py").read_text(encoding="utf-8")
    assert "尚无 patch_planner_approval_request.json" not in source
    assert "尚无 patch_runner_handoff.json" not in source


def test_intent_prompt_discourages_unsupported_hard_thresholds():
    assert "不要无依据地给出硬阈值" in INTENT_CLARIFICATION_PROMPT
    assert "instance_auroc ≥ 0.98" in INTENT_CLARIFICATION_PROMPT
    assert "与 baseline/论文报告结果进行对比" in INTENT_CLARIFICATION_PROMPT
