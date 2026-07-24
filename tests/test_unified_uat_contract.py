from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from autoad_researcher.experiment.evaluation_contract import (
    EvaluationContract,
    EvaluationMetric,
    EvaluationResourceBudget,
)
from autoad_researcher.experiment.gpu import GpuAllocator, GpuDevice
from autoad_researcher.experiment.promotion import DecisionEngine
from autoad_researcher.experiment.scientific_assessment import EffectiveScientificAssessment
from autoad_researcher.experiment.validity import ImplementationEvidence, scientific_effect


SOURCE_BASELINE = "4449f7f40fd195b61ef57b3bc5854187401a1122"
INTEGRATION_BRANCH = "integration/final-candidate-advanced-uat-2026-07-24"


def _contract(metric_name: str, direction: str) -> EvaluationContract:
    return EvaluationContract(
        contract_id="evaluation_contract_000001",
        session_id="session_unified_uat",
        revision=0,
        baseline_commit="a" * 40,
        dataset_identity="unified-fixture-v1",
        split_identity="held-out-v1",
        b_dev_ref="splits/b_dev.json",
        b_test_ref="splits/b_test.json",
        category_set=[],
        metrics=[
            EvaluationMetric(
                name=metric_name,
                direction=direction,
                implementation_ref=f"metrics/{metric_name}.py",
            )
        ],
        primary_metric=metric_name,
        aggregation="mean",
        seeds=[7],
        checkpoint_selection="fixed",
        resource_budget=EvaluationResourceBudget(max_wall_seconds=60, max_gpu_seconds=60),
        protected_paths=[f"metrics/{metric_name}.py"],
    )


def _assessment(
    *,
    effect: str | None = "IMPROVEMENT",
    delta: float | None = 0.1,
    execution_status: str = "COMPLETED",
    reproducibility_status: str = "reproducible",
    guardrail_deltas: dict[str, float] | None = None,
) -> EffectiveScientificAssessment:
    return EffectiveScientificAssessment(
        attempt_id="attempt_000001",
        outcome_card_ref="attempts/attempt_000001/outcome_card.json",
        outcome_card_sha256="a" * 64,
        scientific_assessment_ref="attempts/attempt_000001/scientific_assessment.json",
        scientific_assessment_sha256="b" * 64,
        execution_status=execution_status,
        attempt_category=(
            "scientifically_evaluable" if execution_status == "COMPLETED" else "run_failed"
        ),
        protocol_intact=True,
        metrics_parsed=True,
        patch_applied=True,
        smoke_passed=True,
        evaluation_status="COMPARABLE",
        scientific_effect=effect,
        primary_delta=delta,
        guardrail_deltas=guardrail_deltas or {},
        reproducibility_status=reproducibility_status,
        evidence_refs=["attempts/attempt_000001/execution_result.json"],
    )


@pytest.mark.parametrize(
    ("metric_name", "direction", "baseline", "candidate"),
    [
        ("accuracy", "maximize", 0.4125, 1.0),
        ("rmse", "minimize", 78.1507, 56.2619),
        ("silhouette", "maximize", 0.6028, 0.7337),
    ],
)
def test_unified_scientific_gate_accepts_all_supported_task_directions(
    metric_name: str,
    direction: str,
    baseline: float,
    candidate: float,
):
    effect, delta, guardrails = scientific_effect(
        candidate_metrics={metric_name: candidate, "split": "b_dev", "seed": 7},
        baseline_metrics={metric_name: baseline},
        contract=_contract(metric_name, direction),
        evaluation_status="COMPARABLE",
        implementation_evidence=ImplementationEvidence(
            patch_applied=True,
            smoke_passed=True,
        ),
        metrics_parsed=True,
        protocol_intact=True,
    )

    assert effect == "IMPROVEMENT"
    assert delta is not None and delta > 0
    assert guardrails == {}

    assessment = _assessment(effect=effect, delta=delta)
    assert (
        DecisionEngine()
        .decide(assessment=assessment, phase="b_dev", noise_threshold=0.01)
        .action
        == "candidate"
    )
    assert (
        DecisionEngine()
        .decide(assessment=assessment, phase="b_test", noise_threshold=0.01)
        .action
        == "ready_for_promotion"
    )


def test_unified_gate_preserves_failure_reproducibility_and_no_effect_boundaries():
    engine = DecisionEngine()

    assert (
        engine.decide(
            assessment=_assessment(execution_status="CRASHED", effect=None, delta=None),
            phase="b_dev",
            noise_threshold=0.01,
        ).action
        == "run_failed"
    )
    assert (
        engine.decide(
            assessment=_assessment(reproducibility_status="not_reproducible"),
            phase="b_dev",
            noise_threshold=0.01,
        ).action
        == "confirm_seed"
    )
    assert (
        engine.decide(
            assessment=_assessment(effect="NO_EFFECT", delta=0.0),
            phase="b_test",
            noise_threshold=0.01,
        ).action
        == "no_effect"
    )
    assert (
        engine.decide(
            assessment=_assessment(guardrail_deltas={"latency": -0.01}),
            phase="b_test",
            noise_threshold=0.01,
        ).action
        == "no_promote"
    )


def test_gpu_resource_fusion_does_not_change_scientific_decisions_or_create_champions(
    tmp_path: Path,
):
    devices = [
        GpuDevice(device_id="0", total_vram_mb=24_000, used_vram_mb=1_000),
        GpuDevice(device_id="1", total_vram_mb=24_000, used_vram_mb=1_000),
    ]
    allocator = GpuAllocator(probe=lambda: devices, resource_root=tmp_path)
    closeout_run = tmp_path / "closeout_run"
    cross_task_run = tmp_path / "cross_task_run"

    closeout_lease = allocator.allocate(
        closeout_run,
        attempt_id="attempt_000001",
        worker_id="worker_closeout",
        required_device_count=1,
        required_vram_mb=20_000,
    )
    cross_task_lease = allocator.allocate(
        cross_task_run,
        attempt_id="attempt_000001",
        worker_id="worker_cross_task",
        required_device_count=1,
        required_vram_mb=20_000,
    )

    assert closeout_lease.device_ids == ["0"]
    assert cross_task_lease.device_ids == ["1"]
    assert (
        DecisionEngine()
        .decide(
            assessment=_assessment(effect="NO_EFFECT", delta=0.0),
            phase="b_test",
            noise_threshold=0.01,
        )
        .action
        == "no_effect"
    )

    allocator.release_after_attempt_terminal(
        closeout_run,
        lease_id=closeout_lease.lease_id,
        attempt_id="attempt_000001",
    )
    allocator.release_after_attempt_terminal(
        cross_task_run,
        lease_id=cross_task_lease.lease_id,
        attempt_id="attempt_000001",
    )

    ledger = json.loads(
        (tmp_path / "experiments/resource_leases.json").read_text(encoding="utf-8")
    )
    assert {item["status"] for item in ledger} == {"released"}
    assert not (closeout_run / "experiments/champions").exists()
    assert not (cross_task_run / "experiments/champions").exists()


def test_unified_evidence_contract_binds_both_uats_without_blurring_runtime_readiness():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (
            root
            / "notes/uat/AutoAD_联合UAT封板_2026-07-24/unified_baseline.json"
        ).read_text(encoding="utf-8")
    )
    assert manifest["source_baseline_commit"] == SOURCE_BASELINE
    assert manifest["integration_branch"] == INTEGRATION_BRANCH
    assert set(manifest["uat_packages"]) == {
        "AutoAD_UAT_收尾验收包_2026-07-23",
        "AutoAD_ML_DL_跨任务UAT扩展包_2026-07-23",
    }

    closeout = (
        root
        / "notes/uat/AutoAD_UAT_收尾验收包_2026-07-23/测试结果.md"
    ).read_text(encoding="utf-8")
    cross_task = (
        root
        / "notes/uat/AutoAD_ML_DL_跨任务UAT扩展包_2026-07-23/测试结果.md"
    ).read_text(encoding="utf-8")
    assert "分支：`feat/production-candidate-entry`" in closeout
    assert "分支：`feat/production-candidate-entry`" in cross_task

    evidence_path = (
        root
        / "notes/uat/AutoAD_ML_DL_跨任务UAT扩展包_2026-07-23/GPU运行时观察.csv"
    )
    with evidence_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    runtime_only = next(
        row for row in rows if row["step_id"] == "runtime_only_worker_gpu_smoke"
    )
    assert runtime_only["result"] == "PASS"
    assert runtime_only["runtime_status"] == "COMPLETED"
    assert runtime_only["formal_readiness_status"] == "blocked"
