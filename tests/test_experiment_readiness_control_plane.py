from __future__ import annotations

import json
from pathlib import Path

import pytest

from autoad_researcher.assistant.v2.intent_contract import (
    ResearchIntentContract,
    save_confirmed_contract,
)
from autoad_researcher.core.control_plane import (
    CorruptAuthoritativeStore,
    PipelineJobStore,
    ReadinessFact,
    ReadinessStaleError,
    ResolverSnapshot,
)
from autoad_researcher.core.control_plane.io import atomic_write_json
from autoad_researcher.core.control_plane.readiness import (
    ResolverReadContext,
    assert_readiness_current,
    ensure_experiment_session,
    load_experiment_readiness,
    load_experiment_session,
    materialize_claimed_experiment_prepare,
    repair_experiment_session_projection,
)
from autoad_researcher.worker.main import _process_pending_jobs


def _run_dir(tmp_path: Path) -> Path:
    run_dir = tmp_path / "run_experiment"
    run_dir.mkdir()
    return run_dir


def _contract(run_dir: Path, *, goal: str = "Improve PatchCore") -> ResearchIntentContract:
    return ResearchIntentContract(
        run_id=run_dir.name,
        research_goal=goal,
        baseline="PatchCore",
        dataset="MVTec AD",
        primary_metrics=["image_level_auroc"],
        success_criteria="improve image-level AUROC under the same protocol",
        execution_mode="approve_each_step",
    )


def _prepare(run_dir: Path):
    save_confirmed_contract(run_dir, _contract(run_dir))
    session = ensure_experiment_session(run_dir)
    claimed = PipelineJobStore(run_dir).claim_next(worker_id="worker_test")
    assert claimed is not None and claimed.claim_token is not None
    return session, claimed


class _VerifiedResolver:
    resolver_id = "configured_snapshot"
    schema_version = "configured_snapshot:v1"

    def resolve(self, context: ResolverReadContext) -> ResolverSnapshot:
        context.check_deadline()
        return ResolverSnapshot(
            resolver_id=self.resolver_id,
            schema_version=self.schema_version,
            layers=["implementation", "execution"],
            facts=[ReadinessFact(name="configured_fact", status="verified", value="ready")],
        )


class _ChangingResolver:
    resolver_id = "changing_snapshot"
    schema_version = "changing_snapshot:v1"

    def __init__(self) -> None:
        self.calls = 0

    def resolve(self, context: ResolverReadContext) -> ResolverSnapshot:
        context.check_deadline()
        self.calls += 1
        return ResolverSnapshot(
            resolver_id=self.resolver_id,
            schema_version=self.schema_version,
            layers=["implementation"],
            facts=[ReadinessFact(name="generation", status="verified", value=self.calls)],
        )


class _FileResolver:
    resolver_id = "producer_snapshot"
    schema_version = "producer_snapshot:v1"

    def resolve(self, context: ResolverReadContext) -> ResolverSnapshot:
        value = context.read_bytes("producer/value.txt", max_bytes=64).decode("utf-8")
        return ResolverSnapshot(
            resolver_id=self.resolver_id,
            schema_version=self.schema_version,
            layers=["implementation"],
            facts=[ReadinessFact(name="producer_value", status="verified", value=value)],
        )


def test_ensure_reuses_one_session_and_prepare_job_and_rejects_contract_replacement(tmp_path: Path):
    run_dir = _run_dir(tmp_path)
    save_confirmed_contract(run_dir, _contract(run_dir))

    first = ensure_experiment_session(run_dir)
    replay = ensure_experiment_session(run_dir)

    assert replay == first
    assert len(PipelineJobStore(run_dir).list()) == 1
    assert PipelineJobStore(run_dir).get(first.prepare_job_id) is not None

    atomic_write_json(
        run_dir / "research_intent_contract.json",
        _contract(run_dir, goal="Replace authorization").model_dump(mode="json"),
    )
    with pytest.raises(CorruptAuthoritativeStore, match="cannot replace"):
        ensure_experiment_session(run_dir)
    assert len(PipelineJobStore(run_dir).list()) == 1


def test_worker_materializes_planning_and_fails_closed_without_resolvers(tmp_path: Path):
    run_dir = _run_dir(tmp_path)
    save_confirmed_contract(run_dir, _contract(run_dir))
    session = ensure_experiment_session(run_dir)

    assert _process_pending_jobs(run_dir, worker_id="worker_test") == 1

    readiness = load_experiment_readiness(run_dir)
    updated_session = load_experiment_session(run_dir)
    job = PipelineJobStore(run_dir).get(session.prepare_job_id)
    assert readiness is not None
    assert readiness.planning_readiness.ready is True
    assert readiness.implementation_readiness.ready is False
    assert readiness.implementation_readiness.blocking_reasons == [
        "no_implementation_resolver_configured"
    ]
    assert readiness.execution_readiness.ready is False
    assert readiness.execution_authorization.authorized is False
    assert updated_session is not None and updated_session.status == "materialized"
    assert job is not None and job.status == "completed"
    attempts = list((run_dir / "experiment_agents" / "attempts" / job.job_id).glob("attempt_*"))
    result = json.loads((attempts[0] / "attempt_result.json").read_text(encoding="utf-8"))
    assert result["status"] == "published"


def test_explicit_local_resolver_can_verify_implementation_and_execution(tmp_path: Path):
    run_dir = _run_dir(tmp_path)
    session, claimed = _prepare(run_dir)

    outcome = materialize_claimed_experiment_prepare(
        run_dir,
        claimed,
        [_VerifiedResolver()],
    )

    readiness = load_experiment_readiness(run_dir)
    assert outcome.status == "published"
    assert outcome.job_status == "completed"
    assert readiness is not None
    assert readiness.session_id == session.session_id
    assert readiness.implementation_readiness.ready is True
    assert readiness.execution_readiness.ready is True
    assert readiness.execution_authorization.authorized is False


def test_changed_input_is_fenced_and_candidate_does_not_replace_canonical(tmp_path: Path):
    run_dir = _run_dir(tmp_path)
    session, claimed = _prepare(run_dir)

    outcome = materialize_claimed_experiment_prepare(
        run_dir,
        claimed,
        [_ChangingResolver()],
    )

    job = PipelineJobStore(run_dir).get(session.prepare_job_id)
    updated_session = load_experiment_session(run_dir)
    assert outcome.status == "stale_input"
    assert outcome.job_status == "queued"
    assert load_experiment_readiness(run_dir) is None
    assert job is not None and job.status == "queued"
    assert updated_session is not None and updated_session.status == "queued"
    attempt_dir = (
        run_dir
        / "experiment_agents"
        / "attempts"
        / claimed.job_id
        / f"attempt_{claimed.attempt_count}_{claimed.claim_token}"
    )
    assert (attempt_dir / "readiness.json").is_file()
    result = json.loads((attempt_dir / "attempt_result.json").read_text(encoding="utf-8"))
    assert result["status"] == "stale_input"
    assert result["input_sha256"] != result["publication_check_input_sha256"]


def test_assert_readiness_current_detects_producer_snapshot_change(tmp_path: Path):
    run_dir = _run_dir(tmp_path)
    producer = run_dir / "producer" / "value.txt"
    producer.parent.mkdir()
    producer.write_text("v1", encoding="utf-8")
    _, claimed = _prepare(run_dir)
    resolver = _FileResolver()
    materialize_claimed_experiment_prepare(run_dir, claimed, [resolver])

    assert assert_readiness_current(run_dir, resolvers=[resolver]).implementation_readiness.ready
    producer.write_text("v2", encoding="utf-8")
    with pytest.raises(ReadinessStaleError, match="rematerialization"):
        assert_readiness_current(run_dir, resolvers=[resolver])


def test_session_projection_repairs_from_running_job(tmp_path: Path):
    run_dir = _run_dir(tmp_path)
    session, _claimed = _prepare(run_dir)
    damaged = session.model_copy(update={"status": "queued"})
    atomic_write_json(
        run_dir / "experiment_agents" / "session.json",
        damaged.model_dump(mode="json", exclude_none=True),
    )

    repaired = repair_experiment_session_projection(run_dir)

    assert repaired is not None and repaired.status == "preparing"
