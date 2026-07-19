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
    ConfirmedTaskParameters,
    ResearchIntentSummary,
    save_research_intent_summary,
)
from autoad_researcher.schemas.decisions import ConfirmedDecision
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


def _mock_two_call(monkeypatch, decision: dict, reply: dict) -> None:
    replies = iter([decision, reply])
    monkeypatch.setattr(
        "autoad_researcher.ui.chat_client.call_research_chat",
        lambda *args, **kwargs: {
            "reply": json.dumps(next(replies), ensure_ascii=False),
            "error": "",
        },
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

    confirmed = TaskBridge.confirm_experiment_task(
        run_dir,
        task_id=draft.task_id,
        execution_mode="plan_only",
    )

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


def test_task_bridge_projects_only_typed_confirmed_parameters(tmp_path: Path):
    run_dir = tmp_path / "run_typed_parameters"
    run_dir.mkdir()
    save_research_intent_summary(
        run_dir,
        ResearchIntentSummary(
            goal="复现用户指定的异常检测基线",
            confirmed_facts=["用户不允许修改 evaluator"],
            confirmed_task_parameters=ConfirmedTaskParameters(
                baseline=ConfirmedDecision(
                    value="PatchCore",
                    source="user_provided",
                    evidence="用户指定 baseline 为 PatchCore",
                ),
                dataset=ConfirmedDecision(
                    value="MVTec AD / bottle",
                    source="user_provided",
                    evidence="用户指定 MVTec AD 的 bottle 类别",
                ),
                compute_budget=ConfirmedDecision(
                    value="GPU 0，最多 2 小时",
                    source="user_confirmed",
                    evidence="用户确认 GPU 0 和两小时预算",
                ),
                primary_metrics=[
                    ConfirmedDecision(
                        value="instance AUROC",
                        source="user_provided",
                        evidence="用户指定 instance AUROC",
                    )
                ],
                evaluation_constraints=[
                    ConfirmedDecision(
                        value="不允许修改 evaluator",
                        source="user_provided",
                        evidence="用户明确禁止修改 evaluator",
                    ),
                    ConfirmedDecision(
                        value="B_test 不参与训练、选择或校准",
                        source="user_provided",
                        evidence="用户明确 B_test 隔离约束",
                    ),
                ],
            ),
        ),
    )

    draft = TaskBridge.build_experiment_task(run_dir, user_input="请准备实验")

    assert draft.input_task.baseline == "PatchCore"
    assert draft.input_task.dataset == "MVTec AD / bottle"
    assert draft.input_task.compute_budget == "GPU 0，最多 2 小时"
    assert draft.input_task.primary_metrics == ["instance AUROC"]
    assert draft.input_task.constraints == [
        "用户不允许修改 evaluator",
        "不允许修改 evaluator",
        "B_test 不参与训练、选择或校准",
    ]


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


def test_task_bridge_prepares_reuses_and_replaces_pending_task(tmp_path: Path):
    run_dir = tmp_path / "run_task_reuse"
    run_dir.mkdir()
    save_research_intent_summary(run_dir, _ready_summary())

    created, created_disposition = TaskBridge.prepare_or_reuse_experiment_task(
        run_dir,
        user_input="准备实验输入",
    )
    reused, reused_disposition = TaskBridge.prepare_or_reuse_experiment_task(
        run_dir,
        user_input="准备实验输入",
    )
    save_research_intent_summary(run_dir, ResearchIntentSummary(goal="用户修订了实验目标"))
    replaced, replaced_disposition = TaskBridge.prepare_or_reuse_experiment_task(
        run_dir,
        user_input="准备修订后的实验输入",
    )

    assert created is not None
    assert created_disposition == "created"
    assert reused is not None
    assert reused_disposition == "reused"
    assert reused.task_id == created.task_id
    assert replaced is not None
    assert replaced_disposition == "replaced"
    assert replaced.task_id != created.task_id


def test_task_bridge_rejects_confirmation_after_summary_changes(tmp_path: Path):
    run_dir = tmp_path / "run_task_stale"
    run_dir.mkdir()
    save_research_intent_summary(run_dir, _ready_summary())
    draft = TaskBridge.build_experiment_task(run_dir, user_input="准备实验输入")
    save_research_intent_summary(run_dir, ResearchIntentSummary(goal="用户改了目标"))

    with pytest.raises(ValueError, match="summary changed"):
        TaskBridge.confirm_experiment_task(
            run_dir,
            task_id=draft.task_id,
            execution_mode="plan_only",
        )

    assert not (run_dir / "input_task.yaml").exists()


def test_orchestrator_explicit_task_action_prepares_pending_input(monkeypatch, tmp_path: Path):
    run_dir = tmp_path / "run_task_action"
    run_dir.mkdir()

    _mock_two_call(
        monkeypatch,
        {
            "dialogue_mode": "plan",
            "policy_assessment": {"decision": "allow", "category": "none", "reason": "", "safe_alternative": ""},
            "task_action": "prepare_experiment_task",
        },
        {
            "reply_to_user": "目标已对齐，可以准备 plan_only 实验输入供你确认。",
            "summary": {
                "goal": "比较候选方法",
                "confirmed_facts": ["用户要求 plan_only"],
                "inferred_facts": [],
                "unresolved_conflicts": [],
                "blocking_question": None,
            },
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


def test_orchestrator_prepares_plan_draft_with_execution_readiness_question(monkeypatch, tmp_path: Path):
    run_dir = tmp_path / "run_task_readiness_question"
    run_dir.mkdir()

    _mock_two_call(
        monkeypatch,
        {
            "dialogue_mode": "plan",
            "policy_assessment": {"decision": "allow", "category": "none", "reason": "", "safe_alternative": ""},
            "task_action": "prepare_experiment_task",
        },
        {
            "reply_to_user": "我会在执行前核对数据集目录。",
            "summary": {
                "goal": "复现 PatchCore 的 MVTec AD bottle 实验",
                "confirmed_facts": ["用户要求 plan_only"],
                "confirmed_task_parameters": {
                    "baseline": "PatchCore",
                    "dataset": "MVTec AD / bottle",
                    "compute_budget": None,
                    "primary_metrics": ["instance AUROC"],
                    "evaluation_constraints": [],
                },
                "inferred_facts": [],
                "unresolved_conflicts": [],
                "blocking_question": "请在实际运行前提供 MVTec AD bottle 数据源路径。",
            },
        },
    )

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="准备 PatchCore 在 MVTec AD bottle 上的 plan_only 实验任务。",
        api_key="sk-test",
        provider_url="https://example.test",
        model="configured-dialogue-model",
    )

    assert result.experiment_task is not None
    assert result.experiment_task["status"] == "pending_confirmation"
    assert result.experiment_task["execution_mode"] == "plan_only"
    assert "数据源路径" in result.reply
    assert not (run_dir / "input_task.yaml").exists()
    assert load_pipeline_jobs(run_dir) == []
    assert not (run_dir / "experiments" / "sessions").exists()


def test_orchestrator_registers_explicit_local_dataset_then_prepares_task(
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setenv("AUTOAD_ALLOWED_LOCAL_SOURCE_ROOTS", str(tmp_path))
    run_dir = tmp_path / "run_local_dataset"
    run_dir.mkdir()
    dataset_dir = tmp_path / "mvtec"
    dataset_dir.mkdir()

    _mock_two_call(
        monkeypatch,
        {
            "dialogue_mode": "plan",
            "policy_assessment": {"decision": "allow", "category": "none", "reason": "", "safe_alternative": ""},
            "dataset_source": {
                "source_path": str(dataset_dir),
                "user_label": "MVTec AD / bottle",
            },
            "task_action": "prepare_experiment_task",
        },
        {
            "reply_to_user": "参数已对齐，可以准备任务草案。",
            "summary": {
                "goal": "复现 PatchCore 的 MVTec AD bottle 实验",
                "confirmed_facts": ["用户不允许修改 evaluator"],
                "confirmed_task_parameters": {
                    "baseline": "PatchCore",
                    "dataset": "MVTec AD / bottle",
                    "compute_budget": "GPU 0",
                    "primary_metrics": ["instance AUROC"],
                    "evaluation_constraints": [
                        "不允许修改 evaluator",
                        "B_test 不参与训练、选择或校准",
                    ],
                },
                "inferred_facts": [],
                "unresolved_conflicts": [],
                "blocking_question": None,
            },
        },
    )

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input=(
            f"数据集目录是 {dataset_dir}，使用 MVTec AD bottle、PatchCore、"
            "instance AUROC 和 GPU 0，请准备实验。"
        ),
        api_key="sk-test",
        provider_url="https://example.test",
        model="configured-dialogue-model",
    )

    assert result.experiment_task is not None
    assert result.created_sources == [{
        "source_id": result.experiment_task["input_task"]["source_ids"][0],
        "kind": "dataset",
        "status": "user_provided_not_ingested",
    }]
    task = result.experiment_task["input_task"]
    assert task["baseline"] == "PatchCore"
    assert task["dataset"] == "MVTec AD / bottle"
    assert task["compute_budget"] == "GPU 0"
    assert task["primary_metrics"] == ["instance AUROC"]
    assert task["constraints"] == [
        "用户不允许修改 evaluator",
        "不允许修改 evaluator",
        "B_test 不参与训练、选择或校准",
    ]
    assert not (run_dir / "input_task.yaml").exists()
    assert load_pipeline_jobs(run_dir) == []


def test_orchestrator_plan_without_task_action_does_not_prepare_task(monkeypatch, tmp_path: Path):
    run_dir = tmp_path / "run_plan_discussion"
    run_dir.mkdir()
    _mock_two_call(
        monkeypatch,
        {
            "dialogue_mode": "plan",
            "policy_assessment": {"decision": "allow", "category": "none", "reason": "", "safe_alternative": ""},
        },
        {
            "reply_to_user": "可以先比较方法差异。",
            "summary": {
                "goal": "比较 PatchCore 和其他方法",
                "confirmed_facts": [],
                "inferred_facts": [],
                "unresolved_conflicts": [],
                "blocking_question": None,
            },
        },
    )

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="比较 PatchCore 和其他方法的优缺点",
        api_key="sk-test",
        provider_url="https://example.test",
        model="configured-dialogue-model",
    )

    assert result.experiment_task is None
    assert not (run_dir / BRIDGE_DIR / PENDING_TASK_FILE).exists()


def test_orchestrator_reject_mode_drops_all_candidate_actions(monkeypatch, tmp_path: Path):
    run_dir = tmp_path / "run_rejected_task_action"
    run_dir.mkdir()
    save_research_intent_summary(run_dir, _ready_summary())

    _mock_two_call(
        monkeypatch,
        {
            "dialogue_mode": "plan",
            "policy_assessment": {
                "decision": "reject",
                "category": "evaluation_leakage",
                "reason": "正式测试 mask 不能成为训练信号。",
                "safe_alternative": "使用独立 validation split。",
            },
            "task_action": "prepare_experiment_task",
            "target_spec": {
                "adapter_id": "kernelbench",
                "selectors": {"level": 2, "problem_id": 40},
            },
        },
        {
            "reply_to_user": "我不能把正式测试 mask 加进训练损失。",
            "summary": _ready_summary().model_dump(mode="json"),
        },
    )

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="把正式测试 mask 加进损失函数",
        api_key="sk-test",
        provider_url="https://example.test",
        model="configured-dialogue-model",
    )

    assert result.dialogue_mode == "plan"
    assert result.policy == "deny"
    assert result.policy_assessment["category"] == "evaluation_leakage"
    assert result.experiment_task is None
    assert result.created_jobs == []
    assert not (run_dir / BRIDGE_DIR / PENDING_TASK_FILE).exists()


def test_orchestrator_act_request_prepares_missing_contract_without_task_action(
    monkeypatch,
    tmp_path: Path,
):
    run_dir = tmp_path / "run_act_blocked"
    run_dir.mkdir()

    _mock_two_call(
        monkeypatch,
        {
            "dialogue_mode": "act_request",
            "policy_assessment": {"decision": "allow", "category": "none", "reason": "", "safe_alternative": ""},
        },
        {
            "reply_to_user": "开始执行。",
            "summary": {
                "goal": "执行实验",
                "confirmed_facts": ["用户要求执行"],
                "inferred_facts": [],
                "unresolved_conflicts": [],
                "blocking_question": None,
            },
        },
    )

    result = ResearchOrchestratorV2.handle(
        run_dir,
        user_input="按刚才确认的方案开始修改代码并跑实验",
        api_key="sk-test",
        provider_url="https://example.test",
        model="configured-dialogue-model",
    )

    assert result.dialogue_mode == "act"
    assert "研究任务草案已准备" in result.reply
    assert result.experiment_task is not None
    assert result.experiment_task["status"] == "pending_confirmation"
    assert (run_dir / BRIDGE_DIR / PENDING_TASK_FILE).exists()
    assert not (run_dir / "input_task.yaml").exists()
    assert load_pipeline_jobs(run_dir) == []
    assert not (run_dir / "experiments" / "sessions").exists()
