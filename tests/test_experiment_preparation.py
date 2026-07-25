from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from autoad_researcher.assistant.v2.experiment_projection import build_projection
from autoad_researcher.experiment.preparation import (
    ExperimentPreparation,
    PreparationAsset,
    PreparationDecision,
    PreparationEvidence,
    PreparationRepository,
    PreparationStage,
    PreparationStore,
    require_preparation_stage_if_declared,
)
from autoad_researcher.experiment.preparation_coordinator import (
    ExperimentPreparationCoordinator,
    PreparationInvestigationRequest,
)
from autoad_researcher.server.routes import experiment_config as experiment_config_route


def _preparation(run_id: str) -> ExperimentPreparation:
    return ExperimentPreparation(
        run_id=run_id,
        status="investigating",
        current_stage="official_calibration",
        repositories=[
            PreparationRepository(
                repository_id="repo_baseline",
                role="baseline",
                display_name="官方 baseline",
                path="/work/baseline",
                resolved_commit="a" * 40,
                investigation_status="complete",
                evidence_ids=["ev_cli"],
            )
        ],
        assets=[
            PreparationAsset(
                asset_id="mvtec_ad",
                display_name="MVTec AD",
                kind="dataset",
                status="verified",
                path="/datasets/mvtec",
                stages=["mvtec_training", "official_calibration", "candidate"],
                evidence_ids=["ev_dataset"],
            ),
            PreparationAsset(
                asset_id="mpdd",
                display_name="MPDD",
                kind="dataset",
                status="awaiting_user",
                stages=["mpdd_b_dev", "mpdd_b_test"],
                user_action_id="action_mpdd",
            ),
        ],
        evidence=[
            PreparationEvidence(
                evidence_id="ev_cli",
                kind="verified",
                summary="真实执行 --help 成功",
                repository_ref="/work/baseline",
                repository_commit="a" * 40,
                file_path="train.py",
                command=["python", "train.py", "--help"],
                exit_code=0,
                output="usage: train.py ...",
            ),
            PreparationEvidence(
                evidence_id="ev_dataset",
                kind="observed",
                summary="数据目录存在并包含已确认类别",
                file_path="/datasets/mvtec",
            ),
        ],
        user_decisions=[
            PreparationDecision(
                decision_id="decision_mpdd_path",
                question="请提供 MPDD 数据目录路径。",
                impact="只影响 MPDD B_dev/B_test 评价。",
            )
        ],
        stages=[
            PreparationStage(
                stage_id="mvtec_training",
                display_name="MVTec 训练",
                required_asset_ids=["mvtec_ad"],
                evidence_ids=["ev_dataset"],
            ),
            PreparationStage(
                stage_id="official_calibration",
                display_name="官方方法校准",
                required_asset_ids=["mvtec_ad"],
                evidence_ids=["ev_cli", "ev_dataset"],
            ),
            PreparationStage(
                stage_id="candidate",
                display_name="AutoAD candidate",
                required_asset_ids=["mvtec_ad"],
                depends_on_stage_ids=["official_calibration"],
            ),
            PreparationStage(
                stage_id="mpdd_b_dev",
                display_name="MPDD B_dev",
                required_asset_ids=["mpdd"],
            ),
            PreparationStage(
                stage_id="mpdd_b_test",
                display_name="MPDD B_test",
                required_asset_ids=["mpdd"],
                approval_required=True,
            ),
        ],
        actions=[
            {
                "action_id": "action_mpdd",
                "action_type": "provide_path",
                "label": "提供 MPDD 路径",
                "target_id": "mpdd",
                "requires_user": True,
            }
        ],
    )


def test_readiness_is_stage_scoped_and_preserves_provenance():
    preparation = _preparation("run_preparation")

    refreshed = preparation.refreshed()

    assert refreshed.status == "partially_ready"
    assert refreshed.runnable_stage_ids == ["mvtec_training", "official_calibration"]
    assert refreshed.stages[3].status == "blocked"
    assert refreshed.stages[4].status == "blocked"
    assert refreshed.stages[3].blockers == ["MPDD: awaiting_user"]
    assert refreshed.evidence[0].command == ["python", "train.py", "--help"]
    assert refreshed.user_decisions[0].status == "pending"


def test_store_normalizes_and_round_trips_preparation(tmp_path: Path):
    run_dir = tmp_path / "run_preparation"
    saved = PreparationStore().save(run_dir, _preparation(run_dir.name))

    loaded = PreparationStore().load(run_dir)

    assert loaded is not None
    assert loaded.model_dump(mode="json") == saved.model_dump(mode="json")
    assert (run_dir / "experiments" / "preparation.json").is_file()


def test_declared_stage_gate_blocks_only_the_declared_stage(tmp_path: Path):
    run_dir = tmp_path / "run_preparation"
    PreparationStore().save(run_dir, _preparation(run_dir.name))

    require_preparation_stage_if_declared(run_dir, "mvtec_training")
    with pytest.raises(ValueError, match="preparation_stage_blocked:mpdd_b_test"):
        require_preparation_stage_if_declared(run_dir, "mpdd_b_test")


def test_projection_exposes_preparation_even_without_a_session(tmp_path: Path):
    PreparationStore().save(tmp_path, _preparation(tmp_path.name))

    projection = build_projection(tmp_path)

    assert projection.selection_status == "no_session"
    assert projection.preparation is not None
    assert projection.preparation.runnable_stage_ids == ["mvtec_training", "official_calibration"]


def test_coordinator_reuses_repository_intelligence_and_keeps_missing_asset_scoped(tmp_path: Path):
    repository = tmp_path / "baseline"
    repository.mkdir()
    (repository / "README.md").write_text("baseline\n", encoding="utf-8")
    (repository / "pyproject.toml").write_text("[project]\nname='baseline'\n", encoding="utf-8")
    (repository / "run.py").write_text("print('ok')\n", encoding="utf-8")
    (repository / "evaluate.py").write_text("", encoding="utf-8")
    (repository / "autoad_executor_adapter.json").write_text(
        '{"adapter_id":"generic_python","entrypoint":"run.py","smoke_argv":["python","run.py"],"metrics_output":"metrics.json","allowed_paths":["run.py"],"protected_paths":["evaluate.py"]}',
        encoding="utf-8",
    )
    run_dir = tmp_path / "run_coordinator"
    run_dir.mkdir()
    request = PreparationInvestigationRequest(
        user_goal="验证 baseline",
        repositories=[PreparationRepository(repository_id="repo_baseline", role="baseline", display_name="baseline", path=str(repository))],
        assets=[
            PreparationAsset(asset_id="mvtec", display_name="MVTec AD", kind="dataset", status="verified", stages=["mvtec_training"]),
            PreparationAsset(asset_id="mpdd", display_name="MPDD", kind="dataset", status="awaiting_user", stages=["mpdd_b_dev"]),
        ],
        stages=[
            PreparationStage(stage_id="mvtec_training", display_name="MVTec 训练", required_asset_ids=["mvtec"]),
            PreparationStage(stage_id="mpdd_b_dev", display_name="MPDD B_dev", required_asset_ids=["mpdd"]),
        ],
    )

    result = ExperimentPreparationCoordinator().investigate(run_dir, request)

    assert result.preparation.repositories[0].investigation_status == "complete"
    assert result.preparation.stages[0].status == "ready"
    assert result.preparation.stages[1].status == "blocked"
    assert result.preparation.user_decisions[0].decision_id == "decision_mpdd_path"
    assert result.repository_artifact_refs["repo_baseline"]


@pytest.mark.asyncio
async def test_preparation_route_returns_empty_contract_and_persists_typed_record(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(experiment_config_route, "RUNS_ROOT", str(tmp_path))
    run_dir = tmp_path / "run_preparation"
    run_dir.mkdir()

    empty = await experiment_config_route.get_experiment_preparation(run_dir.name)
    assert empty.run_id == run_dir.name
    assert empty.status == "unresolved"

    saved = await experiment_config_route.save_experiment_preparation(run_dir.name, _preparation(run_dir.name))
    assert saved.status == "partially_ready"
    assert (await experiment_config_route.get_experiment_preparation(run_dir.name)).status == "partially_ready"

    with pytest.raises(ValueError, match="does not match URL"):
        await experiment_config_route.save_experiment_preparation("different_run", _preparation(run_dir.name))
