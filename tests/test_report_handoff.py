from pathlib import Path
from datetime import datetime, timezone

import pytest

from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.experiment.attempt import ExperimentAttempt
from autoad_researcher.experiment.attempt_store import ExperimentAttemptStore
from autoad_researcher.assistant.v2.experiment.baseline_repair import BaselineRepairInput, BaselineRepairResult
from autoad_researcher.assistant.v2.experiment.candidate_control import (
    CandidateLaunchInput,
    CandidateLaunchResult,
)
from autoad_researcher.assistant.v2.research_intent_summary import ResearchIntentSummary
from autoad_researcher.assistant.v2.task_bridge import TaskBridge
from autoad_researcher.experiment.executor_agent import ExecutorProposal
from autoad_researcher.experiment.executor_contracts import InterventionContract
from autoad_researcher.runner import ExperimentCommandPlan, ExperimentInputRefs
from autoad_researcher.experiment.cost_summary import CognitiveCostSummaryBuilder
from autoad_researcher.experiment.evaluation_contract import (
    EvaluationContract,
    EvaluationContractStore,
    EvaluationMetric,
    EvaluationResourceBudget,
)
from autoad_researcher.reporting.review import (
    PivotTaskContext,
    ProposalBudgetEstimate,
    confirm_proposal,
    create_proposal,
    reject_proposal,
)
from autoad_researcher.reporting.service import ReportRequestService
from autoad_researcher.worker.main import _process_pending_jobs


def _ready_report(tmp_path: Path, *, with_budget: bool = False) -> tuple[Path, str]:
    run_dir = tmp_path / "run_report_handoff"
    run_dir.mkdir()
    session, _ = ExperimentSessionStore().create_or_get(
        run_dir,
        task_ref="tasks/task.json",
        task_hash="e" * 64,
        execution_mode="approve_each_step",
    )
    if with_budget:
        contract = EvaluationContract(
            contract_id="evaluation_contract_000001",
            session_id=session.session_id,
            revision=0,
            baseline_commit="a" * 40,
            dataset_identity="fixture",
            split_identity="fixture-split",
            b_dev_ref="splits/b_dev.json",
            b_test_ref="splits/b_test.json",
            category_set=["bottle"],
            metrics=[EvaluationMetric(name="auroc", direction="maximize", implementation_ref="eval.py")],
            primary_metric="auroc",
            aggregation="mean",
            seeds=[1],
            checkpoint_selection="best",
            resource_budget=EvaluationResourceBudget(max_wall_seconds=60, max_gpu_seconds=40),
            protected_paths=["eval.py"],
        )
        frozen = EvaluationContractStore().freeze(run_dir, contract=contract)
        ExperimentSessionStore().bind_evaluation_contract(
            run_dir,
            session_id=session.session_id,
            evaluation_contract_ref=frozen.ref,
            evaluation_contract_sha256=frozen.sha256,
            evaluation_contract_revision=frozen.contract.revision,
        )
        CognitiveCostSummaryBuilder().build_and_persist(run_dir, session_id=session.session_id)
    result, _ = ReportRequestService().request(run_dir, session_id=session.session_id)
    for _ in range(5):
        _process_pending_jobs(run_dir)
    return run_dir, result["manifest"].report_id


def test_human_proposal_only_handoffs_after_confirmation_and_replays(tmp_path: Path):
    run_dir, report_id = _ready_report(tmp_path)
    proposal = create_proposal(
        run_dir,
        report_id=report_id,
        proposal_type="REQUEST_HUMAN",
        rationale="需要人工决定是否追加实验",
    )
    assert proposal.status == "READY_FOR_CONFIRMATION"
    first = confirm_proposal(run_dir, report_id=report_id, proposal_id=proposal.proposal_id)
    second = confirm_proposal(run_dir, report_id=report_id, proposal_id=proposal.proposal_id)
    assert first.status == second.status == "HANDED_OFF"
    assert first.handoff == second.handoff == {"kind": "human_queue", "proposal_id": proposal.proposal_id}


def test_child_report_requires_a_handed_off_explicit_proposal(tmp_path: Path):
    run_dir, report_id = _ready_report(tmp_path)
    proposal = create_proposal(
        run_dir,
        report_id=report_id,
        proposal_type="REQUEST_HUMAN",
        rationale="记录新报告的显式父 Proposal",
    )
    with pytest.raises(ValueError, match="must be handed off"):
        ReportRequestService().request(run_dir, session_id=proposal.source_session_id, source_proposal_id=proposal.proposal_id)

    confirm_proposal(run_dir, report_id=report_id, proposal_id=proposal.proposal_id)
    child, created = ReportRequestService().request(
        run_dir,
        session_id=proposal.source_session_id,
        source_proposal_id=proposal.proposal_id,
    )

    assert created is True
    assert child["manifest"].previous_report_id == report_id
    assert child["manifest"].parent_report_id == report_id
    assert child["manifest"].source_proposal_id == proposal.proposal_id


def test_rejected_proposal_cannot_handoff(tmp_path: Path):
    run_dir, report_id = _ready_report(tmp_path)
    proposal = create_proposal(
        run_dir,
        report_id=report_id,
        proposal_type="REQUEST_HUMAN",
        rationale="请人工复核",
    )
    assert reject_proposal(run_dir, report_id=report_id, proposal_id=proposal.proposal_id).status == "REJECTED"
    with pytest.raises(ValueError, match="rejected proposal"):
        confirm_proposal(run_dir, report_id=report_id, proposal_id=proposal.proposal_id)


def _refine_input() -> CandidateLaunchInput:
    return CandidateLaunchInput(
        intervention_contract=InterventionContract(
            idea_id="idea_000001",
            mechanism="reduce memory pressure",
            hypothesis="a smaller projection preserves the primary metric",
            target_modules=["model.py"],
            allowed_paths=["model.py"],
            time_budget=30,
        ),
        approved_proposal=ExecutorProposal(confidence=0.8),
        comparison_seed=7,
        idempotency_key="caller-supplied-but-replaced",
    )


def _budgeted_report(tmp_path: Path) -> tuple[Path, str]:
    return _ready_report(tmp_path, with_budget=True)


def test_refine_requires_reviewed_input_then_delegates_to_candidate_control(tmp_path: Path, monkeypatch):
    run_dir, report_id = _budgeted_report(tmp_path)
    missing = create_proposal(
        run_dir,
        report_id=report_id,
        proposal_type="REFINE_CURRENT",
        rationale="基于当前结果缩小干预范围",
        requested_changes=["仅调整 model.py 中的投影层"],
    )
    assert missing.status == "DRAFT"
    assert "separately reviewed candidate launch input" in missing.validation_errors[0]

    captured = {}

    def fake_start(self, root, *, session_id, value):
        captured.update({"root": root, "session_id": session_id, "value": value})
        return CandidateLaunchResult(
            status="queued",
            attempt={"attempt_id": "attempt_000123"},
            pipeline_job={"job_id": "job_000123"},
        )

    monkeypatch.setattr(
        "autoad_researcher.reporting.review.CandidateControlService.start",
        fake_start,
    )
    proposal = create_proposal(
        run_dir,
        report_id=report_id,
        proposal_type="REFINE_CURRENT",
        rationale="基于当前结果缩小干预范围",
        requested_changes=["仅调整 model.py 中的投影层"],
        refine_input=_refine_input(),
        estimated_budget=ProposalBudgetEstimate(max_wall_seconds=30, max_gpu_seconds=20),
    )
    handed_off = confirm_proposal(run_dir, report_id=report_id, proposal_id=proposal.proposal_id)
    assert handed_off.handoff == {
        "kind": "refine",
        "attempt_id": "attempt_000123",
        "pipeline_job_id": "job_000123",
    }
    assert captured["session_id"] == proposal.source_session_id
    assert captured["value"].idempotency_key == f"report-proposal:{proposal.proposal_id}"


def test_execution_proposal_budget_must_fit_frozen_contract(tmp_path: Path):
    run_dir, report_id = _budgeted_report(tmp_path)
    proposal = create_proposal(
        run_dir,
        report_id=report_id,
        proposal_type="REFINE_CURRENT",
        rationale="受冻结资源上限约束的修改",
        requested_changes=["仅调整 model.py 中的投影层"],
        refine_input=_refine_input(),
        estimated_budget=ProposalBudgetEstimate(max_wall_seconds=61, max_gpu_seconds=20),
    )

    assert proposal.status == "DRAFT"
    assert "proposal wall-time estimate exceeds the frozen EvaluationContract budget" in proposal.validation_errors


def test_failed_report_can_confirm_a_structured_baseline_repair_proposal(tmp_path: Path, monkeypatch):
    run_dir, report_id = _budgeted_report(tmp_path)
    session = ExperimentSessionStore().load(run_dir, "session_" + "e" * 16)
    assert session is not None
    now = datetime.now(timezone.utc).isoformat()
    failed = ExperimentAttempt(
        attempt_id="attempt_000001",
        run_id=run_dir.name,
        session_id=session.session_id,
        idempotency_key="baseline:failed",
        job_type="experiment_baseline",
        attempt_purpose="baseline",
        command_plan=ExperimentCommandPlan(
            schema_version=1,
            command_id="fixture",
            program="python",
            args=["run.py"],
            cwd="experiments/executor_worktrees/fixture",
            expected_outputs=["metrics.json"],
            timeout_seconds=30,
            network=False,
        ),
        input_refs=ExperimentInputRefs(
            repository_fingerprint="repository",
            environment_sha256="a" * 64,
            dataset_manifest_sha256="b" * 64,
            asset_manifest_sha256="c" * 64,
            command_sha256="d" * 64,
        ),
        job_timeout_sec=30,
        runtime_status="FAILED",
        failure_code="RUN_COMMAND_FAILED",
        retry_exhausted=True,
        created_at=now,
        updated_at=now,
    )
    ExperimentAttemptStore().create_or_get(run_dir, failed)
    repair_input = BaselineRepairInput(
        failed_attempt_id=failed.attempt_id,
        intervention_contract=InterventionContract(
            idea_id="repair_baseline",
            mechanism="restore baseline execution",
            hypothesis="the bounded model-only repair removes the recorded crash",
            target_modules=["model.py"],
            allowed_paths=["model.py"],
            time_budget=30,
        ),
        approved_proposal=ExecutorProposal(confidence=0.9),
        idempotency_key="caller-key",
    )
    captured = {}

    def fake_start(self, root, *, session_id, value):
        captured.update({"root": root, "session_id": session_id, "value": value})
        return BaselineRepairResult(
            status="queued",
            attempt={"attempt_id": "attempt_000002"},
            pipeline_job={"job_id": "job_000002"},
        )

    monkeypatch.setattr(
        "autoad_researcher.reporting.review.BaselineRepairService.start",
        fake_start,
    )
    proposal = create_proposal(
        run_dir,
        report_id=report_id,
        proposal_type="RETRY_FAILED",
        rationale="仅修复失败 baseline 的 model.py，保持评价合同不变",
        target_attempt_id=failed.attempt_id,
        repair_input=repair_input,
        estimated_budget=ProposalBudgetEstimate(max_wall_seconds=30, max_gpu_seconds=20),
    )
    assert proposal.status == "READY_FOR_CONFIRMATION"
    handed_off = confirm_proposal(run_dir, report_id=report_id, proposal_id=proposal.proposal_id)
    assert handed_off.handoff == {
        "kind": "baseline_repair",
        "attempt_id": "attempt_000002",
        "pipeline_job_id": "job_000002",
    }
    assert captured["session_id"] == proposal.source_session_id
    assert captured["value"].idempotency_key == f"report-proposal:{proposal.proposal_id}"


def test_pivot_creates_an_isolated_pending_task_with_report_lineage(tmp_path: Path):
    run_dir, report_id = _ready_report(tmp_path)
    proposal = create_proposal(
        run_dir,
        report_id=report_id,
        proposal_type="PIVOT",
        rationale="当前方向的证据不足，需要改为新的研究目标",
        requested_changes=["改为比较新的候选机制"],
        pivot_context=PivotTaskContext(
            task_title="新的候选机制",
            user_request="围绕新的候选机制准备独立实验任务。",
            research_summary=ResearchIntentSummary(goal="比较新的候选机制与当前基线"),
        ),
    )
    assert proposal.status == "READY_FOR_CONFIRMATION"
    first = confirm_proposal(run_dir, report_id=report_id, proposal_id=proposal.proposal_id)
    second = confirm_proposal(run_dir, report_id=report_id, proposal_id=proposal.proposal_id)
    assert first.handoff == second.handoff
    assert first.handoff is not None
    new_run_dir = tmp_path / first.handoff["run_id"]
    draft = TaskBridge.load_pending_experiment_task(new_run_dir)
    assert draft.status == "pending_confirmation"
    assert not (new_run_dir / "input_task.yaml").exists()
    lineage = (new_run_dir / "report_pivot_lineage.json").read_text(encoding="utf-8")
    assert report_id in lineage
    assert proposal.source_session_id in lineage
