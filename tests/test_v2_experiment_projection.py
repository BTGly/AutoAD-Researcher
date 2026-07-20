from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.assistant.v2.experiment_projection import build_projection
from autoad_researcher.experiment.attempt import ExperimentAttempt
from autoad_researcher.experiment.attempt_store import ExperimentAttemptStore
from autoad_researcher.experiment.promotion import CandidateRegistry, CandidateSnapshot
from autoad_researcher.experiment.session import ExperimentAuthorization, ExperimentSession
from autoad_researcher.runner.models import ExperimentCommandPlan, ExperimentInputRefs
from autoad_researcher.server.routes import experiment_projection as projection_route


NOW = "2026-07-20T00:00:00+00:00"


def _session(run_dir: Path, session_id: str = "session_aaaaaaaaaaaaaaaa") -> ExperimentSession:
    return ExperimentSession(
        session_id=session_id,
        run_id=run_dir.name,
        task_ref="input_task.yaml",
        task_hash="a" * 64,
        authorization=ExperimentAuthorization(
            execution_mode="approve_each_step",
            confirmed_at=NOW,
        ),
        created_at=NOW,
        updated_at=NOW,
    )


def _write_session(run_dir: Path, session: ExperimentSession, *, file_stem: str | None = None) -> None:
    directory = run_dir / "experiments" / "sessions"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{file_stem or session.session_id}.json").write_text(
        session.model_dump_json(), encoding="utf-8"
    )


def _attempt(run_dir: Path, session_id: str) -> ExperimentAttempt:
    command = ExperimentCommandPlan(
        schema_version=1,
        command_id="projection_fixture",
        program="python",
        args=["-c", "print('ok')"],
        cwd="attempts/attempt_000001",
        environment={},
        timeout_seconds=30,
        network=False,
        expected_outputs=["metrics.json"],
    )
    refs = ExperimentInputRefs(
        repository_fingerprint="fixture",
        environment_sha256="a" * 64,
        dataset_manifest_sha256="b" * 64,
        asset_manifest_sha256="c" * 64,
        command_sha256="d" * 64,
    )
    return ExperimentAttempt(
        attempt_id="attempt_000000",
        run_id=run_dir.name,
        session_id=session_id,
        idempotency_key="projection-fixture",
        job_type="experiment_attempt",
        attempt_purpose="exploration",
        command_plan=command,
        input_refs=refs,
        job_timeout_sec=30,
        created_at=NOW,
        updated_at=NOW,
    )


def test_projection_without_session_is_an_explicit_empty_state(tmp_path: Path):
    projection = build_projection(tmp_path)

    assert projection.selection_status == "no_session"
    assert projection.session is None
    assert projection.activity == []


def test_projection_uses_the_validated_input_task_not_task_ref(tmp_path: Path):
    session = _session(tmp_path)
    _write_session(tmp_path, session)
    (tmp_path / "input_task.yaml").write_text(
        yaml.safe_dump({
            "run_id": tmp_path.name,
            "request": "验证异常检测基线",
            "user_idea": "用轻量特征改善瓶子异常检测",
            "baseline": "PatchCore",
            "dataset": "MVTec bottle",
            "primary_metrics": ["image AUROC"],
            "constraints": ["固定评估合同"],
        }, allow_unicode=True),
        encoding="utf-8",
    )

    projection = build_projection(tmp_path)

    assert projection.selection_status == "selected"
    assert projection.session is not None
    assert projection.session.execution_mode == "approve_each_step"
    assert projection.input_task is not None
    assert projection.input_task.user_idea == "用轻量特征改善瓶子异常检测"


def test_multiple_sessions_stay_ambiguous_and_external_selection_is_exact(tmp_path: Path):
    first = _session(tmp_path, "session_aaaaaaaaaaaaaaaa")
    second = _session(tmp_path, "session_bbbbbbbbbbbbbbbb").model_copy(
        update={"task_hash": "b" * 64}
    )
    _write_session(tmp_path, first)
    _write_session(tmp_path, second)

    ambiguous = build_projection(tmp_path)
    selected = build_projection(tmp_path, session_id=second.session_id)

    assert ambiguous.selection_status == "ambiguous"
    assert [item.session_id for item in ambiguous.session_candidates] == [first.session_id, second.session_id]
    assert selected.session is not None and selected.session.session_id == second.session_id
    with pytest.raises(FileNotFoundError):
        build_projection(tmp_path, session_id="../outside")


def test_projection_does_not_materialize_missing_scientific_assessment(tmp_path: Path):
    session = _session(tmp_path)
    _write_session(tmp_path, session)
    attempt, _ = ExperimentAttemptStore().create_or_get(tmp_path, _attempt(tmp_path, session.session_id))
    attempt_dir = tmp_path / "attempts" / attempt.attempt_id
    attempt_dir.mkdir(parents=True)
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))

    projection = build_projection(tmp_path)

    assert projection.attempts[0].scientific_assessment_status == "not_materialized"
    assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*")) == before


def test_projection_exposes_only_durable_candidate_action_facts(tmp_path: Path):
    session = _session(tmp_path)
    _write_session(tmp_path, session)
    CandidateRegistry().create_candidate(
        tmp_path,
        CandidateSnapshot(
            candidate_id="candidate_000001",
            session_id=session.session_id,
            evaluation_contract_hash="a" * 64,
            idea_id="idea_000001",
            attempt_id="attempt_000001",
            source_branch="executor/fixture",
            source_commit="b" * 40,
            patch_sha256="c" * 64,
            metrics_ref="attempts/attempt_000001/metrics.json",
            resource_ref="attempts/attempt_000001/execution_result.json",
            b_dev_evidence_ref="attempts/attempt_000001/scientific_assessment.json",
            b_test_evidence_ref="attempts/attempt_000002/scientific_assessment.json",
            b_test_passed=True,
            guardrails_passed=True,
            created_at=NOW,
        ),
    )

    projection = build_projection(tmp_path)

    assert [item.candidate_id for item in projection.candidates] == ["candidate_000001"]
    assert projection.candidates[0].b_test_passed is True


def test_activity_is_session_bound_and_bounded(tmp_path: Path):
    session = _session(tmp_path)
    _write_session(tmp_path, session)
    for index in range(105):
        append_event(
            tmp_path,
            "experiment.idea_tree.mutated",
            {"session_id": session.session_id, "tree_revision": index},
        )
    append_event(tmp_path, "experiment.unknown", {"session_id": session.session_id})

    projection = build_projection(tmp_path)

    assert len(projection.activity) == 100
    assert projection.activity_truncated is True
    assert projection.activity[0].event_id > projection.activity[-1].event_id
    assert all(card.event_type == "experiment.idea_tree.mutated" for card in projection.activity)


@pytest.mark.asyncio
async def test_projection_route_uses_configured_root_and_maps_missing_session(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "run_projection"
    run_dir.mkdir()
    monkeypatch.setattr(projection_route, "RUNS_ROOT", str(tmp_path))

    empty = await projection_route.get_experiment_projection(run_dir.name, session_id=None)

    assert empty.selection_status == "no_session"
    with pytest.raises(HTTPException) as excinfo:
        await projection_route.get_experiment_projection(run_dir.name, session_id="session_missing")
    assert excinfo.value.status_code == 404
