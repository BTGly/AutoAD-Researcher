"""ExperimentSession and freshness-fenced readiness materialization."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Iterable, Protocol

from pydantic import ValidationError

from autoad_researcher.assistant.v2.contract_hashing import confirmed_contract_sha256
from autoad_researcher.assistant.v2.intent_contract import (
    CONTRACT_FILE,
    CORE_REQUIRED_FIELDS,
    ResearchIntentContract,
    missing_contract_planning_fields,
)
from autoad_researcher.core.control_plane.errors import (
    CorruptAuthoritativeStore,
    ReadinessStaleError,
)
from autoad_researcher.core.control_plane.experiment_state import (
    READINESS_RELATIVE_PATH,
    load_session_unlocked,
    transition_session_if_present_unlocked,
    write_session_unlocked,
)
from autoad_researcher.core.control_plane.hashing import domain_sha256
from autoad_researcher.core.control_plane.io import (
    atomic_write_json,
    write_json_exclusive_durable,
)
from autoad_researcher.core.control_plane.job_store import (
    EXPERIMENT_PREPARE_JOB_TYPE,
    PipelineJobStore,
)
from autoad_researcher.core.control_plane.lock import RunMutationLock
from autoad_researcher.core.control_plane.materialization_requests import MaterializationRequestStore
from autoad_researcher.core.control_plane.models import (
    ExecutionAuthorization,
    ExperimentReadiness,
    ExperimentSession,
    MaterializationInputSnapshot,
    MaterializationOutcome,
    PipelineJob,
    ReadinessFact,
    ReadinessLayer,
    ResolverSnapshot,
)
from autoad_researcher.core.control_plane.paths import resolve_control_plane_path
from autoad_researcher.core.control_plane.unit_of_work import ControlPlaneUnitOfWork


MATERIALIZATION_DEADLINE_SECONDS = 5.0
MAX_RESOLVER_READ_BYTES = 4 * 1024 * 1024
_PASSING_FACT_STATUSES = {"verified", "not_applicable"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LocalReadinessResolver(Protocol):
    """Explicitly configured, deterministic reader of bounded local snapshots."""

    resolver_id: str
    schema_version: str

    def resolve(self, context: "ResolverReadContext") -> ResolverSnapshot: ...


class ResolverReadContext:
    """Only the bounded run-relative read capability supplied to resolvers."""

    def __init__(self, run_dir: Path, *, deadline: float) -> None:
        self.run_dir = run_dir
        self.deadline = deadline

    def check_deadline(self) -> None:
        if monotonic() > self.deadline:
            raise TimeoutError("readiness resolver collection exceeded 5 second deadline")

    def read_bytes(self, artifact_path: str, *, max_bytes: int = MAX_RESOLVER_READ_BYTES) -> bytes:
        if max_bytes <= 0 or max_bytes > MAX_RESOLVER_READ_BYTES:
            raise ValueError(f"resolver max_bytes must be in 1..{MAX_RESOLVER_READ_BYTES}")
        self.check_deadline()
        path = resolve_control_plane_path(self.run_dir, artifact_path, require_exists=True)
        if not path.is_file():
            raise ValueError(f"resolver artifact is not a regular file: {artifact_path}")
        size = path.stat().st_size
        if size > max_bytes:
            raise ValueError(f"resolver artifact exceeds {max_bytes} bytes: {artifact_path}")
        data = path.read_bytes()
        self.check_deadline()
        return data


def load_confirmed_contract_strict_unlocked(run_dir: Path) -> ResearchIntentContract:
    path = run_dir / CONTRACT_FILE
    try:
        contract = ResearchIntentContract.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise CorruptAuthoritativeStore(f"missing or invalid confirmed contract: {path}") from exc
    if contract.run_id != run_dir.name:
        raise CorruptAuthoritativeStore(f"confirmed contract run_id mismatch: {path}")
    return contract


def load_readiness_unlocked(run_dir: Path) -> ExperimentReadiness | None:
    path = run_dir / READINESS_RELATIVE_PATH
    if not path.is_file():
        return None
    try:
        return ExperimentReadiness.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise CorruptAuthoritativeStore(f"invalid experiment readiness: {path}") from exc


def load_experiment_session(run_dir: Path) -> ExperimentSession | None:
    with RunMutationLock(run_dir, mode="shared"):
        return load_session_unlocked(run_dir)


def load_experiment_readiness(run_dir: Path) -> ExperimentReadiness | None:
    with RunMutationLock(run_dir, mode="shared"):
        return load_readiness_unlocked(run_dir)


def ensure_experiment_session(run_dir: Path, *, now: datetime | None = None) -> ExperimentSession:
    current = now or _utcnow()
    with ControlPlaneUnitOfWork(run_dir) as uow:
        return ensure_experiment_session_unlocked(run_dir, uow, now=current)


def ensure_experiment_session_unlocked(
    run_dir: Path,
    uow: ControlPlaneUnitOfWork,
    *,
    now: datetime,
) -> ExperimentSession:
    contract = load_confirmed_contract_strict_unlocked(run_dir)
    contract_hash = confirmed_contract_sha256(contract)
    session_id = f"experiment_session_{contract_hash[:16]}"
    job_store = uow.jobs
    jobs = job_store._load_unlocked()
    job, created = job_store._enqueue_unlocked(
        jobs,
        source_id=f"experiment:{session_id}",
        job_type=EXPERIMENT_PREPARE_JOB_TYPE,
        evidence_role="experiment_readiness",
        payload={"session_id": session_id, "contract_sha256": contract_hash},
        idempotency_key=f"experiment_prepare:{contract_hash}",
    )
    existing = load_session_unlocked(run_dir)
    if existing is not None:
        if existing.contract_sha256 != contract_hash:
            raise CorruptAuthoritativeStore("one run cannot replace its confirmed experiment contract")
        if existing.session_id != session_id or existing.prepare_job_id != job.job_id:
            raise CorruptAuthoritativeStore("experiment session identity does not match canonical job")
        if created:
            raise CorruptAuthoritativeStore("existing session unexpectedly created a second prepare job")
        return existing

    if created:
        job_store._write_unlocked(jobs)
    status = _session_status_for_job(run_dir, job, contract_hash)
    session = ExperimentSession(
        session_id=session_id,
        run_id=run_dir.name,
        contract_sha256=contract_hash,
        prepare_job_id=job.job_id,
        status=status,
        created_at=now,
        updated_at=now,
        error=str(job.error) if job.status == "failed" and job.error is not None else None,
    )
    write_session_unlocked(run_dir, session)
    return session


def collect_materialization_input_unlocked(
    run_dir: Path,
    resolvers: Iterable[LocalReadinessResolver] = (),
    *,
    deadline_seconds: float = MATERIALIZATION_DEADLINE_SECONDS,
) -> MaterializationInputSnapshot:
    if deadline_seconds <= 0 or deadline_seconds > MATERIALIZATION_DEADLINE_SECONDS:
        raise ValueError(f"deadline_seconds must be in (0, {MATERIALIZATION_DEADLINE_SECONDS}]")
    contract = load_confirmed_contract_strict_unlocked(run_dir)
    deadline = monotonic() + deadline_seconds
    by_id: dict[str, LocalReadinessResolver] = {}
    for resolver in resolvers:
        resolver_id = resolver.resolver_id
        if not resolver_id or resolver_id in by_id:
            raise ValueError(f"resolver_id must be non-empty and unique: {resolver_id!r}")
        if not resolver.schema_version:
            raise ValueError(f"resolver {resolver_id!r} has an empty schema_version")
        by_id[resolver_id] = resolver

    components: dict[str, ResolverSnapshot] = {}
    schema_versions: dict[str, str] = {}
    for resolver_id in sorted(by_id):
        context = ResolverReadContext(run_dir, deadline=deadline)
        context.check_deadline()
        resolver = by_id[resolver_id]
        try:
            snapshot = ResolverSnapshot.model_validate(resolver.resolve(context))
        except ValidationError as exc:
            raise ValueError(f"resolver {resolver_id!r} returned an invalid snapshot") from exc
        context.check_deadline()
        if snapshot.resolver_id != resolver_id or snapshot.schema_version != resolver.schema_version:
            raise ValueError(f"resolver identity/schema mismatch for {resolver_id!r}")
        if len(set(snapshot.layers)) != len(snapshot.layers):
            raise ValueError(f"resolver {resolver_id!r} returned duplicate layers")
        _validate_snapshot_trust(snapshot)
        _validate_snapshot_evidence(run_dir, snapshot)
        components[resolver_id] = snapshot
        schema_versions[resolver_id] = resolver.schema_version

    return MaterializationInputSnapshot(
        resolver_schema_versions=schema_versions,
        contract_sha256=confirmed_contract_sha256(contract),
        components=components,
    )


def materialization_input_sha256(snapshot: MaterializationInputSnapshot) -> str:
    return domain_sha256("autoad:materialization_input:v1", snapshot)


def materialize_claimed_experiment_prepare(
    run_dir: Path,
    claimed_job: PipelineJob,
    resolvers: Iterable[LocalReadinessResolver] = (),
    *,
    force: bool = False,
    now: datetime | None = None,
) -> MaterializationOutcome:
    if claimed_job.job_type != EXPERIMENT_PREPARE_JOB_TYPE:
        raise ValueError("readiness materialization requires an experiment_prepare job")
    if claimed_job.claim_token is None:
        raise CorruptAuthoritativeStore("claimed experiment_prepare job has no claim token")
    effective_force = force
    if claimed_job.active_control_request_id is not None:
        request_record = MaterializationRequestStore(run_dir).get(
            claimed_job.active_control_request_id
        )
        if request_record is None or request_record.status != "scheduled":
            raise CorruptAuthoritativeStore(
                "active materialization request is missing or is not scheduled"
            )
        effective_force = effective_force or request_record.force
    resolver_tuple = tuple(resolvers)
    initial_input = collect_materialization_input_unlocked(run_dir, resolver_tuple)
    initial_hash = materialization_input_sha256(initial_input)
    current = now or _utcnow()
    with ControlPlaneUnitOfWork(run_dir) as uow:
        job_store = uow.jobs
        jobs = job_store._load_unlocked()
        index, active_job = job_store._find_job(jobs, claimed_job.job_id)
        job_store._validate_fence(
            active_job,
            claimed_job.claim_token,
            claimed_job.attempt_count,
            current,
        )
        session = load_session_unlocked(run_dir)
        if session is None or session.prepare_job_id != active_job.job_id:
            raise CorruptAuthoritativeStore("experiment_prepare job has no matching ExperimentSession")
        if session.contract_sha256 != initial_input.contract_sha256:
            raise CorruptAuthoritativeStore("ExperimentSession contract hash does not match materialization input")
        claim, attempt_dir = job_store._load_active_claim_unlocked(active_job)
        canonical = load_readiness_unlocked(run_dir)
        revision = 1 if canonical is None else canonical.revision + 1
        candidate = _build_readiness(
            session=session,
            contract=load_confirmed_contract_strict_unlocked(run_dir),
            input_snapshot=initial_input,
            input_hash=initial_hash,
            revision=revision,
            materialized_at=current,
        )
        _write_immutable_json(attempt_dir / "input_snapshot.json", initial_input)
        _write_immutable_json(attempt_dir / "readiness.json", candidate)
        candidate_hash = domain_sha256("autoad:experiment_readiness_candidate:v1", candidate)

    publication_input = collect_materialization_input_unlocked(run_dir, resolver_tuple)
    publication_hash = materialization_input_sha256(publication_input)
    publication_time = now or _utcnow()
    with ControlPlaneUnitOfWork(run_dir) as uow:
        job_store = uow.jobs
        jobs = job_store._load_unlocked()
        index, active_job = job_store._find_job(jobs, claimed_job.job_id)
        job_store._validate_fence(
            active_job,
            claimed_job.claim_token,
            claimed_job.attempt_count,
            publication_time,
        )
        claim, attempt_dir = job_store._load_active_claim_unlocked(active_job)
        _validate_materialization_input_references_unlocked(run_dir, publication_input)
        if initial_hash != publication_hash:
            return _finish_stale_unlocked(
                run_dir,
                job_store,
                jobs,
                index,
                active_job,
                claim,
                attempt_dir,
                input_sha256=initial_hash,
                publication_check_input_sha256=publication_hash,
                candidate_sha256=candidate_hash,
                now=publication_time,
            )

        canonical = load_readiness_unlocked(run_dir)
        no_op = bool(
            not effective_force
            and canonical is not None
            and canonical.materialization_input_sha256 == initial_hash
            and _readiness_semantic_sha256(canonical) == _readiness_semantic_sha256(candidate)
        )
        if no_op:
            published = canonical
            result_status = "no_op"
        else:
            published = candidate
            atomic_write_json(
                run_dir / READINESS_RELATIVE_PATH,
                published.model_dump(mode="json", exclude_none=True),
            )
            result_status = "published"

        transition_session_if_present_unlocked(
            run_dir,
            prepare_job_id=active_job.job_id,
            status="materialized",
            now=publication_time,
        )
        canonical_hash = domain_sha256("autoad:experiment_readiness_artifact:v1", published)
        job_store._ensure_attempt_result_unlocked(
            attempt_dir,
            claim,
            status=result_status,
            finished_at=publication_time,
            input_sha256=initial_hash,
            publication_check_input_sha256=publication_hash,
            candidate_sha256=candidate_hash,
            canonical_readiness_sha256=canonical_hash,
        )
        completed = active_job.model_copy(update={
            "status": "completed",
            "completed_at": publication_time,
            "outputs": [READINESS_RELATIVE_PATH],
            "error": None,
            "claimed_by": None,
            "claim_token": None,
            "attempt_started_at": None,
            "lease_expires_at": None,
            "next_eligible_at": None,
            "active_control_request_id": None,
            "consecutive_stale_count": 0,
            "consecutive_lease_expiry_count": 0,
        })
        jobs[index] = completed
        job_store._write_unlocked(jobs)
        if active_job.active_control_request_id is not None:
            MaterializationRequestStore(run_dir).mark_terminal_unlocked(
                active_job.active_control_request_id,
                status="completed",
                now=publication_time,
            )
        return MaterializationOutcome(
            status=result_status,
            job_status="completed",
            readiness_path=READINESS_RELATIVE_PATH,
            materialization_input_sha256=initial_hash,
            publication_check_input_sha256=publication_hash,
        )


def assert_readiness_current(
    run_dir: Path,
    readiness: ExperimentReadiness | None = None,
    resolvers: Iterable[LocalReadinessResolver] = (),
) -> ExperimentReadiness:
    current_input = collect_materialization_input_unlocked(run_dir, tuple(resolvers))
    current_hash = materialization_input_sha256(current_input)
    with RunMutationLock(run_dir, mode="shared"):
        canonical = readiness or load_readiness_unlocked(run_dir)
        if canonical is None:
            raise ReadinessStaleError("experiment readiness has not been materialized")
        _validate_materialization_input_references_unlocked(run_dir, current_input)
        if canonical.materialization_input_sha256 != current_hash:
            raise ReadinessStaleError(
                "experiment readiness input is stale; rematerialization is required"
            )
        return canonical


def repair_experiment_session_projection(run_dir: Path, *, now: datetime | None = None) -> ExperimentSession | None:
    current = now or _utcnow()
    with ControlPlaneUnitOfWork(run_dir) as uow:
        session = load_session_unlocked(run_dir)
        if session is None:
            return None
        jobs = uow.jobs._load_unlocked()
        job = next((item for item in jobs if item.job_id == session.prepare_job_id), None)
        if job is None or job.job_type != EXPERIMENT_PREPARE_JOB_TYPE:
            raise CorruptAuthoritativeStore("ExperimentSession prepare job is missing or has wrong type")
        expected = _session_status_for_job(run_dir, job, session.contract_sha256)
        error = str(job.error) if expected == "failed" and job.error is not None else None
        if session.status == expected and session.error == error:
            return session
        repaired = session.model_copy(update={"status": expected, "updated_at": current, "error": error})
        write_session_unlocked(run_dir, repaired)
        return repaired


def _build_readiness(
    *,
    session: ExperimentSession,
    contract: ResearchIntentContract,
    input_snapshot: MaterializationInputSnapshot,
    input_hash: str,
    revision: int,
    materialized_at: datetime,
) -> ExperimentReadiness:
    missing = missing_contract_planning_fields(contract)
    missing_set = set(missing)
    planning_facts: list[ReadinessFact] = []
    for field in CORE_REQUIRED_FIELDS:
        value = contract.primary_metrics if field == "primary_metrics" else getattr(contract, field)
        planning_facts.append(ReadinessFact(
            name=field,
            status="missing" if field in missing_set or value in (None, "", [], {}) else "verified",
            value=value,
        ))
    for field in missing:
        if field not in CORE_REQUIRED_FIELDS:
            planning_facts.append(ReadinessFact(name=field, status="missing"))
    planning = ReadinessLayer(
        layer="planning",
        ready=not missing,
        facts=planning_facts,
        blocking_reasons=[f"missing_contract_field:{field}" for field in missing],
    )
    implementation = _resolver_layer(input_snapshot, "implementation")
    execution_specific = _resolver_layer(input_snapshot, "execution")
    execution_blockers = [
        *(["implementation_not_ready"] if not implementation.ready else []),
        *execution_specific.blocking_reasons,
    ]
    execution = ReadinessLayer(
        layer="execution",
        ready=implementation.ready and execution_specific.ready,
        facts=execution_specific.facts,
        blocking_reasons=execution_blockers,
    )
    authorization = ExecutionAuthorization(
        execution_mode=contract.execution_mode,
        authorized=False,
        reason=(
            "execution_mode_plan_only"
            if contract.execution_mode == "plan_only"
            else "execution_approval_required"
        ),
    )
    return ExperimentReadiness(
        revision=revision,
        session_id=session.session_id,
        contract_sha256=input_snapshot.contract_sha256,
        materialization_input_sha256=input_hash,
        planning_readiness=planning,
        implementation_readiness=implementation,
        execution_readiness=execution,
        execution_authorization=authorization,
        materialized_at=materialized_at,
    )


def _resolver_layer(
    input_snapshot: MaterializationInputSnapshot,
    layer: str,
) -> ReadinessLayer:
    relevant = [
        snapshot
        for snapshot in input_snapshot.components.values()
        if layer in snapshot.layers
    ]
    if not relevant:
        return ReadinessLayer(
            layer=layer,
            ready=False,
            blocking_reasons=[f"no_{layer}_resolver_configured"],
        )
    facts: list[ReadinessFact] = []
    blockers: list[str] = []
    for snapshot in relevant:
        if not snapshot.facts:
            blockers.append(f"{snapshot.resolver_id}:no_facts")
        for fact in snapshot.facts:
            facts.append(fact)
            if fact.status not in _PASSING_FACT_STATUSES:
                blockers.append(f"{snapshot.resolver_id}:{fact.name}:{fact.status}")
    return ReadinessLayer(layer=layer, ready=not blockers, facts=facts, blocking_reasons=blockers)


def _finish_stale_unlocked(
    run_dir: Path,
    job_store: PipelineJobStore,
    jobs: list[PipelineJob],
    index: int,
    job: PipelineJob,
    claim,
    attempt_dir: Path,
    *,
    input_sha256: str,
    publication_check_input_sha256: str,
    candidate_sha256: str,
    now: datetime,
) -> MaterializationOutcome:
    updated = job_store._stale_input_transition(job, finished_at=now)
    transition_session_if_present_unlocked(
        run_dir,
        prepare_job_id=job.job_id,
        status="queued" if updated.status == "queued" else "failed",
        now=now,
        error=updated.error,
    )
    job_store._ensure_attempt_result_unlocked(
        attempt_dir,
        claim,
        status="stale_input",
        finished_at=now,
        error="materialization input changed before publication",
        input_sha256=input_sha256,
        publication_check_input_sha256=publication_check_input_sha256,
        candidate_sha256=candidate_sha256,
    )
    jobs[index] = updated
    job_store._write_unlocked(jobs)
    if updated.status == "failed" and job.active_control_request_id is not None:
        MaterializationRequestStore(run_dir).mark_terminal_unlocked(
            job.active_control_request_id,
            status="failed",
            now=now,
            error="input_unstable",
        )
    return MaterializationOutcome(
        status="stale_input",
        job_status=updated.status,
        materialization_input_sha256=input_sha256,
        publication_check_input_sha256=publication_check_input_sha256,
    )


def _session_status_for_job(run_dir: Path, job: PipelineJob, contract_sha256: str) -> str:
    if job.status == "queued":
        return "queued"
    if job.status == "running":
        return "preparing"
    if job.status == "failed":
        return "failed"
    readiness = load_readiness_unlocked(run_dir)
    if readiness is None or readiness.contract_sha256 != contract_sha256:
        raise CorruptAuthoritativeStore("completed experiment_prepare job has no current readiness")
    return "materialized"


def _validate_snapshot_evidence(run_dir: Path, snapshot: ResolverSnapshot) -> None:
    references = [
        *snapshot.observed_inputs,
        *(evidence for fact in snapshot.facts for evidence in fact.evidence),
    ]
    for evidence in references:
        path = resolve_control_plane_path(run_dir, evidence.artifact_path, require_exists=True)
        if not path.is_file():
            raise ValueError(f"readiness evidence is not a regular file: {evidence.artifact_path}")
        if path.stat().st_size > MAX_RESOLVER_READ_BYTES:
            raise ValueError(
                f"readiness evidence exceeds {MAX_RESOLVER_READ_BYTES} bytes: "
                f"{evidence.artifact_path}"
            )
        if evidence.sha256 is not None:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != evidence.sha256:
                raise ValueError(f"readiness evidence hash mismatch: {evidence.artifact_path}")


def _validate_snapshot_trust(snapshot: ResolverSnapshot) -> None:
    if not snapshot.observed_inputs:
        raise ValueError(
            f"resolver {snapshot.resolver_id!r} must declare observed_inputs"
        )
    observed_keys: set[tuple[str, str]] = set()
    for evidence in snapshot.observed_inputs:
        if evidence.sha256 is None:
            raise ValueError(
                f"resolver {snapshot.resolver_id!r} observed input has no sha256"
            )
        key = (evidence.artifact_path, evidence.sha256)
        if key in observed_keys:
            raise ValueError(
                f"resolver {snapshot.resolver_id!r} returned duplicate observed inputs"
            )
        observed_keys.add(key)

    for fact in snapshot.facts:
        if fact.status == "verified":
            if not fact.evidence:
                raise ValueError(
                    f"verified resolver fact {snapshot.resolver_id}:{fact.name} has no evidence"
                )
            if any(evidence.sha256 is None for evidence in fact.evidence):
                raise ValueError(
                    f"verified resolver fact {snapshot.resolver_id}:{fact.name} has unhashed evidence"
                )
        if fact.status == "conflict":
            keys = {
                (evidence.artifact_path, evidence.sha256)
                for evidence in fact.evidence
                if evidence.sha256 is not None
            }
            if len(keys) < 2 or len(keys) != len(fact.evidence):
                raise ValueError(
                    f"conflict resolver fact {snapshot.resolver_id}:{fact.name} "
                    "requires two distinct hashed evidence references"
                )
        if fact.status == "unavailable_due_to_dependency" and fact.value is not None:
            raise ValueError(
                f"dependency-unavailable fact {snapshot.resolver_id}:{fact.name} cannot have a value"
            )


def _validate_materialization_input_references_unlocked(
    run_dir: Path,
    snapshot: MaterializationInputSnapshot,
) -> None:
    contract = load_confirmed_contract_strict_unlocked(run_dir)
    if confirmed_contract_sha256(contract) != snapshot.contract_sha256:
        raise ReadinessStaleError("confirmed contract changed during readiness materialization")
    for component in snapshot.components.values():
        _validate_snapshot_trust(component)
        _validate_snapshot_evidence(run_dir, component)


def _readiness_semantic_sha256(readiness: ExperimentReadiness) -> str:
    return domain_sha256(
        "autoad:experiment_readiness_semantics:v1",
        {
            "session_id": readiness.session_id,
            "contract_sha256": readiness.contract_sha256,
            "materialization_input_sha256": readiness.materialization_input_sha256,
            "planning_readiness": readiness.planning_readiness.model_dump(mode="json"),
            "implementation_readiness": readiness.implementation_readiness.model_dump(mode="json"),
            "execution_readiness": readiness.execution_readiness.model_dump(mode="json"),
            "execution_authorization": readiness.execution_authorization.model_dump(mode="json"),
        },
    )


def _write_immutable_json(path: Path, model) -> None:
    try:
        write_json_exclusive_durable(path, model.model_dump(mode="json", exclude_none=True))
    except ValueError as exc:
        raise CorruptAuthoritativeStore(str(exc)) from exc
