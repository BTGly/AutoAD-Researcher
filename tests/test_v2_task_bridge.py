from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from autoad_researcher.assistant.v2.evidence_service import append_artifact_evidence
from autoad_researcher.assistant.v2.job_service import load_pipeline_jobs
from autoad_researcher.assistant.v2.orchestrator import ResearchOrchestratorV2
from autoad_researcher.assistant.v2.research_intent_summary import (
    BasedStatement,
    ResearchIntentSummary,
    save_research_intent_summary,
)
from autoad_researcher.assistant.v2.task_bridge import (
    BRIDGE_DIR,
    PENDING_TASK_FILE,
    TASK_REPORT_FILE,
    TaskBridge,
)


def _write_source_registry(run_dir: Path) -> None:
    path = run_dir / "sources" / "source_references.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "sources": [
            {
                "source_id": "src_repo",
                "kind": "github_repo",
                "user_label": "https://github.com/example/repo",
                "status": "repo_acquired",
                "created_at": "2026-07-15T00:00:00+00:00",
            }
        ],
    }), encoding="utf-8")


def _ready_summary() -> ResearchIntentSummary:
    return ResearchIntentSummary(
        goal="比较一个候选方法与现有系统",
        confirmed_facts=["用户要求只做 plan_only", "用户使用 RTX 4090"],
        inferred_facts=[
            BasedStatement(
                statement="材料提到了 PatchCore 和 MVTec AD",
                basis="source_id=src_repo",
            )
        ],
        blocking_question=None,
    )


def test_task_bridge_prepares_then_confirms_exact_pipeline_input(tmp_path: Path):
    run_dir = tmp_path / "run_task_bridge"
    run_dir.mkdir()
    _write_source_registry(run_dir)
    save_research_intent_summary(run_dir, _ready_summary())
    attestation = run_dir / "repo_acquisition" / "src_repo" / "repository_attestation.json"
    attestation.parent.mkdir(parents=True)
    attestation.write_text("{}\n", encoding="utf-8")
    append_artifact_evidence(
        run_dir,
        source_id="src_repo",
        artifact_path="repo/artifacts/repo_summary.json",
        evidence_type="repo_summary",
        parser_name="repository_intelligence",
        summary="PatchCore on MVTec AD",
    )

    draft = TaskBridge.build_experiment_task(
        run_dir,
        user_input="开始准备实验计划",
        transcript_tail=[
            {"role": "user", "content": "我想比较一个候选方法"},
            {"role": "assistant", "content": "先核对材料"},
        ],
    )

    assert draft.status == "pending_confirmation"
    assert draft.execution_mode == "plan_only"
    assert draft.input_task.request == "我想比较一个候选方法\n\n开始准备实验计划"
    assert draft.input_task.user_idea == "比较一个候选方法与现有系统"
    assert draft.input_task.source_ids == ["src_repo"]
    assert draft.input_task.constraints == ["用户要求只做 plan_only", "用户使用 RTX 4090"]
    assert draft.input_task.baseline is None
    assert draft.input_task.dataset is None
    assert draft.evidence_refs == ["repo/artifacts/repo_summary.json"]
    assert not (run_dir / "input_task.yaml").exists()

    confirmed = TaskBridge.confirm_experiment_task(run_dir, task_id=draft.task_id)

    assert confirmed.status == "confirmed"
    data = yaml.safe_load((run_dir / "input_task.yaml").read_text(encoding="utf-8"))
    assert data["source_ids"] == ["src_repo"]
    assert "baseline" not in data
    assert "dataset" not in data
    report = json.loads((run_dir / BRIDGE_DIR / TASK_REPORT_FILE).read_text(encoding="utf-8"))
    assert report["source"] == "summary.json"
    assert report["evidence_refs"] == ["repo/artifacts/repo_summary.json"]
    assert load_pipeline_jobs(run_dir) == []
    assert not (run_dir / "experiments" / "sessions").exists()


def test_task_bridge_blocks_unresolved_question(tmp_path: Path):
    run_dir = tmp_path / "run_task_blocked"
    run_dir.mkdir()
    save_research_intent_summary(
        run_dir,
        ResearchIntentSummary(goal="比较方法", blocking_question="使用哪个数据集？"),
    )

    with pytest.raises(ValueError, match="blocking question"):
        TaskBridge.build_experiment_task(run_dir, user_input="开始实验")

    assert not (run_dir / BRIDGE_DIR / PENDING_TASK_FILE).exists()


def test_task_bridge_rejects_confirmation_after_summary_changes(tmp_path: Path):
    run_dir = tmp_path / "run_task_stale"
    run_dir.mkdir()
    save_research_intent_summary(run_dir, _ready_summary())
    draft = TaskBridge.build_experiment_task(run_dir, user_input="准备实验输入")
    save_research_intent_summary(run_dir, ResearchIntentSummary(goal="用户改了目标"))

    with pytest.raises(ValueError, match="summary changed"):
        TaskBridge.confirm_experiment_task(run_dir, task_id=draft.task_id)

    assert not (run_dir / "input_task.yaml").exists()


def test_orchestrator_typed_task_action_only_prepares_plan_only_input(monkeypatch, tmp_path: Path):
    run_dir = tmp_path / "run_task_action"
    run_dir.mkdir()

    monkeypatch.setattr(
        "autoad_researcher.ui.chat_client.call_research_chat",
        lambda *args, **kwargs: {
            "reply": json.dumps({
                "dialogue_mode": "plan",
                "reply_to_user": "目标已对齐，可以准备 plan_only 实验输入供你确认。",
                "summary": {
                    "goal": "比较候选方法",
                    "confirmed_facts": ["用户要求 plan_only"],
                    "inferred_facts": [],
                    "unresolved_conflicts": [],
                    "blocking_question": None,
                },
                "source_action": None,
                "task_action": {"action": "prepare_experiment_task"},
            }, ensure_ascii=False),
            "error": "",
        },
    )

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="开始准备实验计划",
        transcript_tail=[{"role": "user", "content": "只做 plan_only"}],
        api_key="sk-test",
        provider_url="https://example.test",
        model="configured-dialogue-model",
    )

    assert result.experiment_task is not None
    assert result.experiment_task["execution_mode"] == "plan_only"
    assert not (run_dir / "input_task.yaml").exists()
    assert load_pipeline_jobs(run_dir) == []
    assert not (run_dir / "experiments" / "sessions").exists()


def test_orchestrator_reject_mode_drops_all_candidate_actions(monkeypatch, tmp_path: Path):
    run_dir = tmp_path / "run_rejected_task_action"
    run_dir.mkdir()
    save_research_intent_summary(run_dir, _ready_summary())

    monkeypatch.setattr(
        "autoad_researcher.ui.chat_client.call_research_chat",
        lambda *args, **kwargs: {
            "reply": json.dumps({
                "dialogue_mode": "reject",
                "policy_assessment": {
                    "decision": "reject",
                    "category": "evaluation_leakage",
                    "reason": "正式测试 mask 不能成为训练信号。",
                    "safe_alternative": "使用独立 validation split。",
                },
                "reply_to_user": "我不能把正式测试 mask 加进训练损失。",
                "summary": _ready_summary().model_dump(mode="json"),
                "task_action": {"action": "prepare_experiment_task"},
                "target_spec": {
                    "adapter_id": "kernelbench",
                    "selectors": {"level": 2, "problem_id": 40},
                },
            }, ensure_ascii=False),
            "error": "",
        },
    )

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="把正式测试 mask 加进损失函数",
        api_key="sk-test",
        provider_url="https://example.test",
        model="configured-dialogue-model",
    )

    assert result.dialogue_mode == "reject"
    assert result.policy_assessment["category"] == "evaluation_leakage"
    assert result.experiment_task is None
    assert result.created_jobs == []
    assert not (run_dir / BRIDGE_DIR / PENDING_TASK_FILE).exists()


def test_orchestrator_act_request_is_explicitly_blocked_without_confirmed_task(
    monkeypatch,
    tmp_path: Path,
):
    run_dir = tmp_path / "run_act_blocked"
    run_dir.mkdir()

    monkeypatch.setattr(
        "autoad_researcher.ui.chat_client.call_research_chat",
        lambda *args, **kwargs: {
            "reply": json.dumps({
                "dialogue_mode": "act_request",
                "reply_to_user": "开始执行。",
                "summary": {
                    "goal": "执行实验",
                    "confirmed_facts": ["用户要求执行"],
                    "inferred_facts": [],
                    "unresolved_conflicts": [],
                    "blocking_question": None,
                },
                "task_action": {"action": "prepare_experiment_task"},
            }, ensure_ascii=False),
            "error": "",
        },
    )

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="按刚才确认的方案开始修改代码并跑实验",
        api_key="sk-test",
        provider_url="https://example.test",
        model="configured-dialogue-model",
    )

    assert result.dialogue_mode == "act_request"
    assert "没有已确认的 input_task.yaml" in result.reply
    assert result.experiment_task is None
    assert not (run_dir / BRIDGE_DIR / PENDING_TASK_FILE).exists()
