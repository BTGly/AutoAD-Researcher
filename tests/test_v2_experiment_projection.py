from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi import HTTPException

from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.assistant.v2 import experiment_projection
from autoad_researcher.assistant.v2.experiment_projection import build_projection
from autoad_researcher.benchmarks.hashing import sha256_file
from autoad_researcher.experiment.attempt import ExperimentAttempt
from autoad_researcher.experiment.attempt_store import ExperimentAttemptStore
from autoad_researcher.experiment.cognition import CognitiveCommitStore
from autoad_researcher.experiment.cognitive_budget import CognitiveBudget, CognitiveBudgetStore, CognitiveUsage
from autoad_researcher.experiment.finalizer import OutcomeCard
from autoad_researcher.experiment.idea_tree import IdeaTreeStore
from autoad_researcher.experiment.promotion import CandidateRegistry, CandidateSnapshot, ChampionPointer
from autoad_researcher.experiment.scientific_assessment import AssessmentReconciliation, ScientificAssessment
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


def _write_attempt(run_dir: Path, attempt: ExperimentAttempt) -> None:
    path = run_dir / "experiments" / "attempts" / f"{attempt.attempt_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(attempt.model_dump_json(), encoding="utf-8")


def _write_assessment_artifacts(run_dir: Path, attempt_id: str, *, valid: bool = True) -> None:
    directory = run_dir / "attempts" / attempt_id
    directory.mkdir(parents=True, exist_ok=True)
    if not valid:
        (directory / "scientific_assessment.json").write_text("{invalid", encoding="utf-8")
        return
    outcome = OutcomeCard(
        attempt_id=attempt_id,
        runtime_status="COMPLETED",
        attempt_category="scientifically_evaluable",
        execution_result_ref=f"attempts/{attempt_id}/execution_result.json",
        metrics={"score": 0.9},
        execution_status="COMPLETED",
        metrics_parsed=True,
        protocol_intact=True,
        evaluation_status="COMPARABLE",
    )
    outcome_path = directory / "outcome_card.json"
    inputs_path = directory / "scientific_evaluation_inputs.json"
    outcome_path.write_text(outcome.model_dump_json(), encoding="utf-8")
    inputs_path.write_text("{}", encoding="utf-8")
    assessment = ScientificAssessment(
        attempt_id=attempt_id,
        outcome_card_ref=f"attempts/{attempt_id}/outcome_card.json",
        outcome_card_sha256=sha256_file(outcome_path),
        inputs_ref=f"attempts/{attempt_id}/scientific_evaluation_inputs.json",
        inputs_sha256=sha256_file(inputs_path),
        patch_applied=True,
        smoke_passed=True,
        metrics_parsed=True,
        protocol_intact=True,
        evaluation_status="COMPARABLE",
        scientific_effect="IMPROVEMENT",
        primary_delta=0.1,
    )
    assessment_path = directory / "scientific_assessment.json"
    assessment_path.write_text(assessment.model_dump_json(), encoding="utf-8")
    reconciliation = AssessmentReconciliation(
        attempt_id=attempt_id,
        outcome_card_ref=assessment.outcome_card_ref,
        outcome_card_sha256=assessment.outcome_card_sha256,
        scientific_assessment_ref=f"attempts/{attempt_id}/scientific_assessment.json",
        scientific_assessment_sha256=sha256_file(assessment_path),
        comparison_status_at_finalization="COMPARABLE",
        effective_evaluation_status="COMPARABLE",
    )
    (directory / "assessment_reconciliation.json").write_text(reconciliation.model_dump_json(), encoding="utf-8")


def _candidate(session_id: str, contract_hash: str, candidate_id: str, attempt_id: str) -> CandidateSnapshot:
    return CandidateSnapshot(
        candidate_id=candidate_id,
        session_id=session_id,
        evaluation_contract_hash=contract_hash,
        idea_id="idea_000001",
        attempt_id=attempt_id,
        source_branch=f"executor/{attempt_id}",
        source_commit="b" * 40,
        patch_sha256="c" * 64,
        metrics_ref=f"attempts/{attempt_id}/metrics.json",
        resource_ref=f"attempts/{attempt_id}/execution_result.json",
        b_dev_evidence_ref=f"attempts/{attempt_id}/scientific_assessment.json",
        b_test_evidence_ref=f"attempts/{attempt_id}/scientific_assessment.json",
        b_test_passed=True,
        guardrails_passed=True,
        created_at=NOW,
    )


def _file_snapshot(run_dir: Path) -> dict[str, tuple[bytes, int]]:
    return {
        str(path.relative_to(run_dir)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in run_dir.rglob("*")
        if path.is_file()
    }


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


def test_projection_preserves_unready_session_facts(tmp_path: Path):
    session = _session(tmp_path).model_copy(update={
        "status": "ENVIRONMENT_RUNNING",
        "readiness_status": "resolving",
        "readiness_blockers": ["environment job is still running"],
        "environment_status": "running",
        "baseline_status": "not_started",
    })
    _write_session(tmp_path, session)

    projection = build_projection(tmp_path)

    assert projection.session is not None
    assert projection.session.status == "ENVIRONMENT_RUNNING"
    assert projection.summary is not None
    assert projection.summary.readiness_status == "resolving"
    assert projection.session.readiness_blockers == ["environment job is still running"]


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


def test_selected_session_is_not_blocked_by_an_unrelated_invalid_record(tmp_path: Path):
    session = _session(tmp_path)
    _write_session(tmp_path, session)
    directory = tmp_path / "experiments" / "sessions"
    (directory / "session_bbbbbbbbbbbbbbbb.json").write_text("{invalid", encoding="utf-8")

    selected = build_projection(tmp_path, session_id=session.session_id)

    assert selected.session is not None and selected.session.session_id == session.session_id
    with pytest.raises(projection_route.SessionInventoryError):
        build_projection(tmp_path)


def test_invalid_champion_pointer_is_not_projected_as_absent(tmp_path: Path):
    session = _session(tmp_path).model_copy(update={
        "evaluation_contract_ref": "experiments/contracts/contract.json",
        "evaluation_contract_sha256": "a" * 64,
    })
    _write_session(tmp_path, session)
    directory = tmp_path / "experiments" / "champions"
    directory.mkdir(parents=True)
    (directory / "current_by_contract.json").write_text("{invalid", encoding="utf-8")

    projection = build_projection(tmp_path)

    assert projection.champion_status == "control_plane_invalid"
    assert projection.champion is None


def test_invalid_candidate_inventory_degrades_the_projection(tmp_path: Path):
    session = _session(tmp_path).model_copy(update={
        "evaluation_contract_ref": "experiments/contracts/contract.json",
        "evaluation_contract_sha256": "a" * 64,
    })
    _write_session(tmp_path, session)
    directory = tmp_path / "experiments" / "champions"
    candidates = directory / "candidates"
    candidates.mkdir(parents=True)
    (candidates / "candidate_000001.json").write_text("{invalid", encoding="utf-8")
    (directory / "current_by_contract.json").write_text(json.dumps({
        "a" * 64: {"candidate_id": "candidate_000001", "event_id": "event_000001", "trunk_commit": "b" * 40, "updated_at": NOW},
    }), encoding="utf-8")

    projection = build_projection(tmp_path)

    assert projection.candidate_inventory_status == "invalid"
    assert projection.candidates == []
    assert projection.champion_status == "control_plane_invalid"


def test_activity_scan_limit_is_distinct_from_related_activity_truncation(tmp_path: Path, monkeypatch):
    session = _session(tmp_path)
    _write_session(tmp_path, session)
    events = iter({
        "event_id": index,
        "type": "experiment.unknown",
        "created_at": NOW,
        "payload": {"session_id": "session_other"},
    } for index in range(experiment_projection.ACTIVITY_SCAN_EVENT_LIMIT + 5))
    monkeypatch.setattr(experiment_projection, "iter_events_reverse", lambda _: events)

    activity, truncated, scan_truncated = experiment_projection._activity(
        tmp_path,
        session_id=session.session_id,
        attempts={},
        commits={},
        candidate_ids=set(),
    )

    assert activity == []
    assert truncated is False
    assert scan_truncated is True


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


def test_projection_preserves_the_complete_attempt_status_matrix(tmp_path: Path):
    session = _session(tmp_path)
    _write_session(tmp_path, session)
    statuses = [
        "QUEUED",
        "STARTING",
        "RUNNING",
        "TERMINATING",
        "COMPLETED",
        "FAILED",
        "TIMED_OUT",
        "CANCELLED",
        "LOST",
    ]
    for index, status in enumerate(statuses):
        terminal_failure = status in {"FAILED", "TIMED_OUT", "LOST"}
        attempt = _attempt(tmp_path, session.session_id).model_copy(update={
            "attempt_id": f"attempt_{index:06d}",
            "idempotency_key": f"projection-status-{index}",
            "runtime_status": status,
            "failure_code": "fixture_failure" if terminal_failure else None,
            "retry_exhausted": terminal_failure,
        })
        _write_attempt(tmp_path, attempt)

    projection = build_projection(tmp_path)

    assert projection.summary is not None
    assert projection.summary.attempt_by_status == {status: 1 for status in statuses}
    assert [item.runtime_status for item in projection.attempts] == statuses


def test_projection_preserves_outcome_card_without_metrics(tmp_path: Path):
    session = _session(tmp_path)
    _write_session(tmp_path, session)
    attempt = _attempt(tmp_path, session.session_id).model_copy(update={
        "runtime_status": "FAILED",
        "failure_code": "metrics_missing",
        "retry_exhausted": True,
    })
    _write_attempt(tmp_path, attempt)
    directory = tmp_path / "attempts" / attempt.attempt_id
    directory.mkdir(parents=True)
    card = OutcomeCard(
        attempt_id=attempt.attempt_id,
        runtime_status="FAILED",
        attempt_category="run_failed",
        execution_result_ref=f"attempts/{attempt.attempt_id}/execution_result.json",
        metrics=None,
        protocol_valid=True,
        execution_status="CRASHED",
        metrics_parsed=False,
        protocol_intact=True,
        evaluation_status="NON_COMPARABLE",
    )
    (directory / "outcome_card.json").write_text(card.model_dump_json(), encoding="utf-8")

    projection = build_projection(tmp_path)

    outcome = projection.attempts[0].execution_outcome
    assert outcome is not None
    assert outcome.metrics is None
    assert outcome.evaluation_status == "NON_COMPARABLE"
    assert projection.attempts[0].scientific_assessment_status == "not_materialized"


def test_projection_reads_existing_scientific_assessment_and_reconciliation(tmp_path: Path):
    session = _session(tmp_path)
    _write_session(tmp_path, session)
    attempt = _attempt(tmp_path, session.session_id)
    _write_attempt(tmp_path, attempt)
    _write_assessment_artifacts(tmp_path, attempt.attempt_id)

    projection = build_projection(tmp_path)

    view = projection.attempts[0]
    assert view.scientific_assessment_status == "available"
    assert view.scientific_assessment is not None
    assert view.scientific_assessment.scientific_effect == "IMPROVEMENT"
    assert view.assessment_reconciliation is not None
    assert view.assessment_reconciliation.scientific_assessment_ref == f"attempts/{attempt.attempt_id}/scientific_assessment.json"


def test_projection_marks_invalid_existing_scientific_assessment(tmp_path: Path):
    session = _session(tmp_path)
    _write_session(tmp_path, session)
    attempt = _attempt(tmp_path, session.session_id)
    _write_attempt(tmp_path, attempt)
    _write_assessment_artifacts(tmp_path, attempt.attempt_id, valid=False)

    projection = build_projection(tmp_path)

    assert projection.attempts[0].scientific_assessment_status == "invalid"
    assert projection.attempts[0].scientific_assessment is None
    assert projection.attempts[0].assessment_reconciliation is None


@pytest.mark.parametrize("artifact", ["outcome_card_sha256", "inputs_sha256"])
def test_projection_rejects_assessment_hash_mismatch(tmp_path: Path, artifact: str):
    session = _session(tmp_path)
    _write_session(tmp_path, session)
    attempt = _attempt(tmp_path, session.session_id)
    _write_attempt(tmp_path, attempt)
    _write_assessment_artifacts(tmp_path, attempt.attempt_id)
    directory = tmp_path / "attempts" / attempt.attempt_id
    assessment = ScientificAssessment.model_validate_json((directory / "scientific_assessment.json").read_text(encoding="utf-8"))
    (directory / "scientific_assessment.json").write_text(
        assessment.model_copy(update={artifact: "f" * 64}).model_dump_json(), encoding="utf-8"
    )

    projection = build_projection(tmp_path)

    assert projection.attempts[0].scientific_assessment_status == "invalid"


@pytest.mark.parametrize(
    ("artifact", "ref"),
    [
        ("outcome_card_ref", "attempts/attempt_000999/outcome_card.json"),
        ("inputs_ref", "attempts/attempt_000999/scientific_evaluation_inputs.json"),
    ],
)
def test_projection_rejects_cross_attempt_assessment_reference(tmp_path: Path, artifact: str, ref: str):
    session = _session(tmp_path)
    _write_session(tmp_path, session)
    attempt = _attempt(tmp_path, session.session_id)
    _write_attempt(tmp_path, attempt)
    _write_assessment_artifacts(tmp_path, attempt.attempt_id)
    directory = tmp_path / "attempts" / attempt.attempt_id
    assessment = ScientificAssessment.model_validate_json((directory / "scientific_assessment.json").read_text(encoding="utf-8"))
    (directory / "scientific_assessment.json").write_text(
        assessment.model_copy(update={artifact: ref}).model_dump_json(), encoding="utf-8"
    )

    projection = build_projection(tmp_path)

    assert projection.attempts[0].scientific_assessment_status == "invalid"


def test_projection_rejects_reconciliation_bound_to_another_assessment(tmp_path: Path):
    session = _session(tmp_path)
    _write_session(tmp_path, session)
    attempt = _attempt(tmp_path, session.session_id)
    _write_attempt(tmp_path, attempt)
    _write_assessment_artifacts(tmp_path, attempt.attempt_id)
    directory = tmp_path / "attempts" / attempt.attempt_id
    reconciliation = AssessmentReconciliation.model_validate_json((directory / "assessment_reconciliation.json").read_text(encoding="utf-8"))
    (directory / "assessment_reconciliation.json").write_text(
        reconciliation.model_copy(update={"scientific_assessment_ref": "attempts/attempt_000999/scientific_assessment.json"}).model_dump_json(),
        encoding="utf-8",
    )

    projection = build_projection(tmp_path)

    assert projection.attempts[0].scientific_assessment_status == "invalid"


@pytest.mark.parametrize("missing", ["scientific_assessment.json", "assessment_reconciliation.json"])
def test_projection_rejects_incomplete_assessment_sidecars(tmp_path: Path, missing: str):
    session = _session(tmp_path)
    _write_session(tmp_path, session)
    attempt = _attempt(tmp_path, session.session_id)
    _write_attempt(tmp_path, attempt)
    _write_assessment_artifacts(tmp_path, attempt.attempt_id)
    (tmp_path / "attempts" / attempt.attempt_id / missing).unlink()

    projection = build_projection(tmp_path)

    assert projection.attempts[0].scientific_assessment_status == "invalid"


def test_projection_exposes_server_owned_approval_actions(tmp_path: Path):
    contract_hash = "a" * 64
    session = _session(tmp_path).model_copy(update={
        "status": "READY",
        "baseline_status": "completed",
        "evaluation_contract_ref": "experiments/contracts/current.json",
        "evaluation_contract_sha256": contract_hash,
    })
    _write_session(tmp_path, session)
    attempt = _attempt(tmp_path, session.session_id).model_copy(update={"runtime_status": "COMPLETED"})
    _write_attempt(tmp_path, attempt)
    _write_assessment_artifacts(tmp_path, attempt.attempt_id)

    projection = build_projection(tmp_path)

    assert [item.candidate_attempt_id for item in projection.actions.candidate_confirmations] == [attempt.attempt_id]
    assert projection.actions.candidate_promotions == []
    CandidateRegistry().create_candidate(tmp_path, _candidate(session.session_id, contract_hash, "candidate_000001", attempt.attempt_id))

    projection = build_projection(tmp_path)

    assert projection.actions.candidate_confirmations == []
    assert [item.candidate_id for item in projection.actions.candidate_promotions] == ["candidate_000001"]


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


def test_projection_reads_idea_statuses_insights_and_attempt_summary(tmp_path: Path):
    session = _session(tmp_path)
    _write_session(tmp_path, session)
    attempt = _attempt(tmp_path, session.session_id)
    _write_attempt(tmp_path, attempt)
    store = IdeaTreeStore()
    tree, _ = store.create_or_get(tmp_path, session_id=session.session_id)
    node_ids = []
    for index in range(9):
        tree = store.add_node(
            tmp_path,
            session_id=session.session_id,
            expected_revision=tree.revision,
            idempotency_key=f"add-{index}",
            parent_id="idea_000000",
            mechanism=f"mechanism-{index}",
            hypothesis=f"hypothesis-{index}",
            observable="score",
            grounding=[f"grounding-{index}"],
            expected_cost="low",
        )
        node_ids.append(f"idea_{index + 1:06d}")

    def advance(node_id: str, statuses: list[str]) -> None:
        nonlocal tree
        for status in statuses:
            tree = store.mark_status(
                tmp_path,
                session_id=session.session_id,
                expected_revision=tree.revision,
                idempotency_key=f"{node_id}-{status}",
                node_id=node_id,
                status=status,
            )

    advance(node_ids[1], ["REVIEWED"])
    advance(node_ids[2], ["REVIEWED", "READY"])
    advance(node_ids[3], ["REVIEWED", "READY", "RUNNING"])
    advance(node_ids[4], ["REVIEWED", "READY", "RUNNING", "SUPPORTED"])
    advance(node_ids[5], ["REVIEWED", "READY", "RUNNING", "NOT_SUPPORTED"])
    advance(node_ids[6], ["REVIEWED", "READY", "RUNNING", "INCONCLUSIVE"])
    tree = store.request_prune(
        tmp_path,
        session_id=session.session_id,
        expected_revision=tree.revision,
        idempotency_key="prune-node",
        node_id=node_ids[7],
        reason="fixture pruning rationale",
    )
    advance(node_ids[8], ["REVIEWED", "READY", "RUNNING", "SUPPORTED", "MERGED"])
    tree = store.attach_attempt(
        tmp_path,
        session_id=session.session_id,
        expected_revision=tree.revision,
        idempotency_key="attach-attempt",
        node_id=node_ids[0],
        attempt_ref=attempt.attempt_id,
    )
    store.append_reinterpretation(
        tmp_path,
        session_id=session.session_id,
        expected_revision=tree.revision,
        idempotency_key="append-insight",
        node_id=node_ids[0],
        text="fixture observation",
        evidence_refs=["evidence/fixture.json"],
    )

    projection = build_projection(tmp_path)

    assert projection.idea_tree is not None
    by_id = {item.node_id: item for item in projection.idea_tree.nodes}
    assert {item.status for item in by_id.values()} == {
        "DRAFT", "REVIEWED", "READY", "RUNNING", "SUPPORTED", "NOT_SUPPORTED", "INCONCLUSIVE", "PRUNED", "MERGED",
    }
    assert by_id[node_ids[0]].attempt_summary == {"QUEUED": 1}
    assert by_id[node_ids[0]].insights[0]["text"] == "fixture observation"
    assert by_id[node_ids[0]].insights[0]["evidence_refs"] == ["evidence/fixture.json"]
    assert by_id[node_ids[0]].insights[0]["kind"] == "reinterpretation"
    assert by_id[node_ids[7]].insights[0]["text"] == "fixture pruning rationale"


def test_projection_reads_cognitive_commits_and_budget_usage(tmp_path: Path):
    session = _session(tmp_path).model_copy(update={"budget": {"max_calls": 2}})
    _write_session(tmp_path, session)
    commits = CognitiveCommitStore()
    for index in range(2):
        commits.append(
            tmp_path,
            session_id=session.session_id,
            idempotency_key=f"commit-{index}",
            tree_revision=index,
            input_outcome_refs=[f"attempts/attempt_{index:06d}/outcome_card.json"],
            observation=f"observation-{index}",
            comparison=f"comparison-{index}",
            hypothesis_verdict=f"verdict-{index}",
            keep_why=f"keep-{index}",
            failure_why=f"failure-{index}",
            mechanism_interpretation=f"mechanism-{index}",
            confidence=0.5,
            uncertainty=f"uncertainty-{index}",
            tree_mutations=[],
            next_action=f"next-{index}",
            evidence_refs=[],
            model_profile="fixture",
            prompt_version="fixture-v1",
        )
    budget = CognitiveBudget(
        max_calls=2,
        max_tokens=100,
        max_compact_cycles=2,
        max_exploratory_cycles=2,
        max_subagent_calls=2,
        max_wall_seconds=10,
    )
    usages = [
        CognitiveUsage(cycle_id="cycle-1", cycle_kind="compact", role="coordinator", input_tokens=3, output_tokens=5, wall_seconds=1.5, created_at=NOW),
        CognitiveUsage(cycle_id="cycle-2", cycle_kind="exploratory", role="idea_explorer", input_tokens=7, output_tokens=11, wall_seconds=2.5, created_at=NOW),
    ]
    for usage in usages:
        assert CognitiveBudgetStore().append(tmp_path, session_id=session.session_id, budget=budget, usage=usage).allowed

    projection = build_projection(tmp_path)

    assert [item.commit_id for item in projection.cognitive_commits] == ["commit_000001", "commit_000002"]
    assert projection.summary is not None
    assert projection.summary.budget_consumed == {
        "calls": 2,
        "input_tokens": 10,
        "output_tokens": 16,
        "wall_seconds": 4.0,
    }


def test_projection_selects_champion_for_the_current_evaluation_contract(tmp_path: Path):
    current_hash = "a" * 64
    other_hash = "d" * 64
    session = _session(tmp_path).model_copy(update={
        "evaluation_contract_ref": "experiments/contracts/current.json",
        "evaluation_contract_sha256": current_hash,
    })
    _write_session(tmp_path, session)
    registry = CandidateRegistry()
    current = _candidate(session.session_id, current_hash, "candidate_000001", "attempt_000001")
    other = _candidate(session.session_id, other_hash, "candidate_000002", "attempt_000002")
    registry.create_candidate(tmp_path, current)
    registry.create_candidate(tmp_path, other)
    registry.update_pointer(
        tmp_path,
        contract_hash=current_hash,
        pointer=ChampionPointer(
            candidate_id=current.candidate_id,
            event_id="promotion-current",
            trunk_commit="e" * 40,
            updated_at=NOW,
        ),
    )
    registry.update_pointer(
        tmp_path,
        contract_hash=other_hash,
        pointer=ChampionPointer(
            candidate_id=other.candidate_id,
            event_id="promotion-other",
            trunk_commit="f" * 40,
            updated_at=NOW,
        ),
    )
    _write_assessment_artifacts(tmp_path, current.attempt_id)
    _write_assessment_artifacts(tmp_path, other.attempt_id)

    projection = build_projection(tmp_path)

    assert projection.champion_status == "available"
    assert projection.summary is not None and projection.summary.champion_status == "available"
    assert projection.champion is not None and projection.champion.candidate_id == current.candidate_id


@pytest.mark.parametrize(("valid", "expected_status"), [(None, "assessment_missing"), (False, "assessment_invalid")])
def test_projection_preserves_registered_champion_when_assessment_degrades(
    tmp_path: Path,
    valid: bool | None,
    expected_status: str,
):
    contract_hash = "a" * 64
    session = _session(tmp_path).model_copy(update={
        "evaluation_contract_ref": "experiments/contracts/current.json",
        "evaluation_contract_sha256": contract_hash,
    })
    _write_session(tmp_path, session)
    candidate = _candidate(session.session_id, contract_hash, "candidate_000001", "attempt_000001")
    registry = CandidateRegistry()
    registry.create_candidate(tmp_path, candidate)
    registry.update_pointer(
        tmp_path,
        contract_hash=contract_hash,
        pointer=ChampionPointer(
            candidate_id=candidate.candidate_id,
            event_id="promotion-current",
            trunk_commit="e" * 40,
            updated_at=NOW,
        ),
    )
    if valid is not None:
        _write_assessment_artifacts(tmp_path, candidate.attempt_id, valid=valid)

    projection = build_projection(tmp_path)

    assert projection.champion_status == expected_status
    assert projection.champion is not None
    assert projection.champion.candidate_id == candidate.candidate_id
    assert projection.champion.scientific_assessment is None


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


@pytest.mark.asyncio
async def test_projection_route_reports_invalid_session_inventory(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "run_projection"
    run_dir.mkdir()
    directory = run_dir / "experiments" / "sessions"
    directory.mkdir(parents=True)
    (directory / "session_bad.json").write_text("{invalid", encoding="utf-8")
    monkeypatch.setattr(projection_route, "RUNS_ROOT", str(tmp_path))

    with pytest.raises(HTTPException) as excinfo:
        await projection_route.get_experiment_projection(run_dir.name, session_id=None)

    assert excinfo.value.status_code == 409


@pytest.mark.asyncio
async def test_projection_route_does_not_change_durable_file_content_or_mtime(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "run_projection"
    run_dir.mkdir()
    session = _session(run_dir)
    _write_session(run_dir, session)
    attempt = _attempt(run_dir, session.session_id)
    _write_attempt(run_dir, attempt)
    _write_assessment_artifacts(run_dir, attempt.attempt_id)
    IdeaTreeStore().create_or_get(run_dir, session_id=session.session_id)
    before = _file_snapshot(run_dir)
    monkeypatch.setattr(projection_route, "RUNS_ROOT", str(tmp_path))

    projection = await projection_route.get_experiment_projection(run_dir.name, session_id=session.session_id)

    assert projection.selection_status == "selected"
    assert _file_snapshot(run_dir) == before
