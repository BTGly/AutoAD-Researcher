from pathlib import Path

import pytest

from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.assistant.v2.experiment.candidate_control import (
    CandidateLaunchInput,
    CandidateLaunchResult,
)
from autoad_researcher.assistant.v2.research_intent_summary import ResearchIntentSummary
from autoad_researcher.assistant.v2.task_bridge import TaskBridge
from autoad_researcher.experiment.executor_agent import ExecutorProposal
from autoad_researcher.experiment.executor_contracts import InterventionContract
from autoad_researcher.reporting.review import (
    PivotTaskContext,
    confirm_proposal,
    create_proposal,
    reject_proposal,
)
from autoad_researcher.reporting.service import ReportRequestService
from autoad_researcher.worker.main import _process_pending_jobs


def _ready_report(tmp_path: Path) -> tuple[Path, str]:
    run_dir = tmp_path / "run_report_handoff"
    run_dir.mkdir()
    session, _ = ExperimentSessionStore().create_or_get(
        run_dir,
        task_ref="tasks/task.json",
        task_hash="e" * 64,
        execution_mode="approve_each_step",
    )
    result, _ = ReportRequestService().request(run_dir, session_id=session.session_id)
    for _ in range(3):
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


def test_refine_requires_reviewed_input_then_delegates_to_candidate_control(tmp_path: Path, monkeypatch):
    run_dir, report_id = _ready_report(tmp_path)
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
    )
    handed_off = confirm_proposal(run_dir, report_id=report_id, proposal_id=proposal.proposal_id)
    assert handed_off.handoff == {
        "kind": "refine",
        "attempt_id": "attempt_000123",
        "pipeline_job_id": "job_000123",
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
