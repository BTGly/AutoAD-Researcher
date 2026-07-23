"""Read-only Experiment Observatory projection from durable run artifacts."""

from __future__ import annotations

from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.benchmarks.hashing import sha256_file
from autoad_researcher.assistant.v2.event_service import iter_events_reverse
from autoad_researcher.experiment.attempt_store import ExperimentAttemptStore
from autoad_researcher.experiment.cognition import CognitiveCommit, CognitiveCommitStore
from autoad_researcher.experiment.cognitive_budget import CognitiveUsageStore
from autoad_researcher.experiment.finalizer import OutcomeCard
from autoad_researcher.experiment.idea_tree import IdeaNode, IdeaTreeStore
from autoad_researcher.experiment.promotion import CandidateRegistry, CandidateSnapshot
from autoad_researcher.experiment.scientific_assessment import (
    AssessmentReconciliation,
    ScientificAssessment,
)
from autoad_researcher.experiment.session import ExperimentSession
from autoad_researcher.experiment.session_store import ExperimentSessionStore, SESSIONS_DIR
from autoad_researcher.schemas.intake import InputTask


ChampionStatus = Literal["absent", "available", "assessment_missing", "assessment_invalid", "control_plane_invalid"]
ACTIVITY_LIMIT = 100
ACTIVITY_SCAN_EVENT_LIMIT = 10_000


class SessionProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    task_ref: str
    task_hash: str
    status: str
    execution_mode: str
    readiness_status: str
    readiness_blockers: list[str] = Field(default_factory=list)
    environment_status: str
    baseline_status: str
    evaluation_contract_ref: str | None = None
    evaluation_contract_sha256: str | None = None
    budget: dict[str, object] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class SessionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    task_hash: str
    status: str
    created_at: str


class SessionStats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    readiness_status: str
    environment_status: str
    baseline_status: str
    idea_count: int
    idea_rooted_count: int
    attempt_by_status: dict[str, int] = Field(default_factory=dict)
    budget: dict[str, object] = Field(default_factory=dict)
    budget_consumed: dict[str, object] | None = None
    champion_status: ChampionStatus = "absent"


class IdeaNodeProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    parent_id: str | None = None
    is_root: bool
    depth: int
    mechanism: str | None = None
    hypothesis: str | None = None
    observable: str | None = None
    research_axis: str | None = None
    minimal_intervention: str | None = None
    falsification: str | None = None
    relationship_to_previous_ideas: str | None = None
    grounding: list[str] = Field(default_factory=list)
    expected_cost: str
    status: str
    attempt_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    cognitive_commit_refs: list[str] = Field(default_factory=list)
    insights: list[dict[str, object]] = Field(default_factory=list)
    children: list[str] = Field(default_factory=list)
    attempt_summary: dict[str, int] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class IdeaTreeProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    revision: int
    root_node_id: str
    nodes: list[IdeaNodeProjection] = Field(default_factory=list)


class AttemptProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_id: str
    attempt_purpose: str
    runtime_status: str
    job_type: str
    pipeline_job_id: str | None = None
    required_device_count: int
    required_vram_mb: int
    retry_of: str | None = None
    retry_count: int
    max_retries: int
    retry_exhausted: bool
    failure_code: str | None = None
    command_plan_summary: str
    execution_outcome: OutcomeCard | None = None
    scientific_assessment: ScientificAssessment | None = None
    assessment_reconciliation: AssessmentReconciliation | None = None
    scientific_assessment_status: Literal["available", "not_materialized", "invalid"]
    related_idea_ids: list[str] = Field(default_factory=list)
    pid: int | None = None
    heartbeat_at: str | None = None
    resource_lease_id: str | None = None
    created_at: str
    updated_at: str


class ChampionProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    session_id: str
    evaluation_contract_hash: str
    idea_id: str
    attempt_id: str
    scientific_assessment: ScientificAssessment | None = None
    assessment_reconciliation: AssessmentReconciliation | None = None
    assessment_error: str | None = None


class CandidateProjection(BaseModel):
    """Read-only candidate evidence available for an explicit promotion action."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    idea_id: str
    attempt_id: str
    b_test_passed: bool
    guardrails_passed: bool


class CandidateInventory(BaseModel):
    """One validated candidate read shared by every projection consumer."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["available", "invalid"] = "available"
    candidates: list[CandidateSnapshot] = Field(default_factory=list)


class CandidateConfirmationAction(BaseModel):
    """A server-owned B_test request target; the threshold remains user input."""

    model_config = ConfigDict(extra="forbid")

    candidate_attempt_id: str


class CandidatePromotionAction(BaseModel):
    """A server-owned Candidate target for the existing human promotion action."""

    model_config = ConfigDict(extra="forbid")

    candidate_id: str


class ExperimentActionsProjection(BaseModel):
    """Restricted human actions derived from the durable projection only."""

    model_config = ConfigDict(extra="forbid")

    baseline_launch_available: bool = False
    candidate_confirmations: list[CandidateConfirmationAction] = Field(default_factory=list)
    candidate_promotions: list[CandidatePromotionAction] = Field(default_factory=list)


class ActivityCard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: int
    event_type: str
    created_at: str
    title: str
    summary: str
    card_kind: str
    related_idea_id: str | None = None
    related_attempt_id: str | None = None
    related_commit_id: str | None = None
    related_outcome: dict[str, object] | None = None
    detail: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class DeveloperRefs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    session_id: str
    event_ids: list[int] = Field(default_factory=list)
    artifact_paths: list[str] = Field(default_factory=list)
    pipeline_job_ids: list[str] = Field(default_factory=list)
    event_log_path: str


class ExperimentProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    selection_status: Literal["no_session", "selected", "ambiguous"]
    session: SessionProjection | None = None
    session_candidates: list[SessionSummary] = Field(default_factory=list)
    input_task: InputTask | None = None
    summary: SessionStats | None = None
    idea_tree: IdeaTreeProjection | None = None
    attempts: list[AttemptProjection] = Field(default_factory=list)
    candidates: list[CandidateProjection] = Field(default_factory=list)
    candidate_inventory_status: Literal["available", "invalid"] = "available"
    actions: ExperimentActionsProjection = Field(default_factory=ExperimentActionsProjection)
    cognitive_commits: list[CognitiveCommit] = Field(default_factory=list)
    champion_status: ChampionStatus = "absent"
    champion: ChampionProjection | None = None
    activity: list[ActivityCard] = Field(default_factory=list)
    activity_limit: int = ACTIVITY_LIMIT
    activity_truncated: bool = False
    activity_scan_truncated: bool = False
    developer_refs: DeveloperRefs | None = None


def build_projection(run_dir: Path, session_id: str | None = None) -> ExperimentProjection:
    """Build one ephemeral view without modifying any durable run file."""

    if session_id is None:
        sessions = _discover_sessions(run_dir)
        if not sessions:
            return ExperimentProjection(selection_status="no_session")
        if len(sessions) > 1:
            return ExperimentProjection(
                selection_status="ambiguous",
                session_candidates=[_session_summary(item) for item in sessions],
            )
        session = sessions[0]
    else:
        if session_id not in _session_candidate_stems(run_dir):
            raise FileNotFoundError("experiment session not found")
        session = ExperimentSessionStore().load(run_dir, session_id)
        if session is None or session.session_id != session_id:
            raise FileNotFoundError("experiment session not found")

    attempt_store = ExperimentAttemptStore()
    attempts = attempt_store.list_for_session(run_dir, session_id=session.session_id)
    tree = IdeaTreeStore().load(run_dir, session_id=session.session_id)
    commits = CognitiveCommitStore().load(run_dir, session_id=session.session_id)
    input_task = _load_input_task(run_dir, session.task_ref)
    related_ideas = _attempt_idea_index(tree)
    attempt_views = [_attempt_projection(run_dir, item, related_ideas.get(item.attempt_id, [])) for item in attempts]
    candidate_inventory = _candidate_inventory(run_dir, session_id=session.session_id)
    champion_status, champion = _champion_projection(run_dir, session, candidate_inventory)
    candidates = [
        CandidateProjection(
            candidate_id=item.candidate_id,
            idea_id=item.idea_id,
            attempt_id=item.attempt_id,
            b_test_passed=item.b_test_passed,
            guardrails_passed=item.guardrails_passed,
        )
        for item in candidate_inventory.candidates
    ]
    actions = _actions_projection(session, attempt_views, candidate_inventory, champion)
    activity, truncated, scan_truncated = _activity(
        run_dir,
        session_id=session.session_id,
        attempts={item.attempt_id: item for item in attempts},
        commits={item.commit_id: item for item in commits},
        candidate_ids={item.candidate_id for item in candidate_inventory.candidates},
    )
    artifact_paths = _artifact_paths(attempt_views, champion)
    pipeline_job_ids = [item.pipeline_job_id for item in attempts if item.pipeline_job_id]
    return ExperimentProjection(
        selection_status="selected",
        session=_session_projection(session),
        input_task=input_task,
        summary=SessionStats(
            status=session.status,
            readiness_status=session.readiness_status,
            environment_status=session.environment_status,
            baseline_status=session.baseline_status,
            idea_count=len(tree.nodes) if tree else 0,
            idea_rooted_count=sum(1 for item in tree.nodes if item.is_root) if tree else 0,
            attempt_by_status=dict(sorted(Counter(item.runtime_status for item in attempts).items())),
            budget=dict(session.budget),
            budget_consumed=_budget_consumed(run_dir, session.session_id),
            champion_status=champion_status,
        ),
        idea_tree=_idea_tree_projection(tree, attempts),
        attempts=attempt_views,
        candidates=candidates,
        candidate_inventory_status=candidate_inventory.status,
        actions=actions,
        cognitive_commits=commits,
        champion_status=champion_status,
        champion=champion,
        activity=activity,
        activity_truncated=truncated,
        activity_scan_truncated=scan_truncated,
        developer_refs=DeveloperRefs(
            run_id=run_dir.name,
            session_id=session.session_id,
            event_ids=[item.event_id for item in activity],
            artifact_paths=artifact_paths,
            pipeline_job_ids=pipeline_job_ids,
            event_log_path="events/events.jsonl",
        ),
    )


def _discover_sessions(run_dir: Path) -> list[ExperimentSession]:
    directory = run_dir / SESSIONS_DIR
    if not directory.is_dir():
        return []
    sessions = []
    for path in sorted(directory.glob("*.json")):
        try:
            sessions.append(ExperimentSession.model_validate_json(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            raise SessionInventoryError("experiment session inventory contains an invalid durable record") from exc
    return sessions


class SessionInventoryError(ValueError):
    """A session inventory cannot be summarized without hiding corruption."""


def _session_candidate_stems(run_dir: Path) -> set[str]:
    directory = run_dir / SESSIONS_DIR
    if not directory.is_dir():
        return set()
    return {path.stem for path in directory.glob("*.json")}


def _session_projection(session: ExperimentSession) -> SessionProjection:
    return SessionProjection(
        session_id=session.session_id,
        task_ref=session.task_ref,
        task_hash=session.task_hash,
        status=session.status,
        execution_mode=session.authorization.execution_mode,
        readiness_status=session.readiness_status,
        readiness_blockers=session.readiness_blockers,
        environment_status=session.environment_status,
        baseline_status=session.baseline_status,
        evaluation_contract_ref=session.evaluation_contract_ref,
        evaluation_contract_sha256=session.evaluation_contract_sha256,
        budget=dict(session.budget),
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def _session_summary(session: ExperimentSession) -> SessionSummary:
    return SessionSummary(
        session_id=session.session_id,
        task_hash=session.task_hash,
        status=session.status,
        created_at=session.created_at,
    )


def _load_input_task(run_dir: Path, task_ref: str) -> InputTask | None:
    ref = PurePosixPath(task_ref)
    if "\\" in task_ref or ref.is_absolute() or any(part == ".." for part in ref.parts):
        return None
    path = run_dir.joinpath(*ref.parts).resolve()
    if not path.is_relative_to(run_dir.resolve()) or not path.is_file():
        return None
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return InputTask.model_validate(raw)
    except Exception:
        return None


def _budget_consumed(run_dir: Path, session_id: str) -> dict[str, object] | None:
    usages = CognitiveUsageStore().load(run_dir, session_id=session_id)
    if not usages:
        return None
    return {
        "calls": len(usages),
        "input_tokens": sum(item.input_tokens for item in usages),
        "output_tokens": sum(item.output_tokens for item in usages),
        "wall_seconds": sum(item.wall_seconds for item in usages),
    }


def _attempt_idea_index(tree: Any) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    if tree is None:
        return result
    for node in tree.nodes:
        for attempt_id in node.attempt_refs:
            result.setdefault(attempt_id, []).append(node.node_id)
    return result


def _idea_tree_projection(tree: Any, attempts: list[Any]) -> IdeaTreeProjection | None:
    if tree is None:
        return None
    by_attempt = {item.attempt_id: item for item in attempts}
    nodes = []
    for node in tree.nodes:
        summary = Counter(
            by_attempt[attempt_id].runtime_status
            for attempt_id in node.attempt_refs
            if attempt_id in by_attempt
        )
        nodes.append(_idea_node_projection(node, dict(sorted(summary.items()))))
    return IdeaTreeProjection(
        session_id=tree.session_id,
        revision=tree.revision,
        root_node_id=tree.root_node_id,
        nodes=nodes,
    )


def _idea_node_projection(node: IdeaNode, attempt_summary: dict[str, int]) -> IdeaNodeProjection:
    return IdeaNodeProjection(
        node_id=node.node_id,
        parent_id=node.parent_id,
        is_root=node.is_root,
        depth=node.depth,
        mechanism=node.mechanism,
        hypothesis=node.hypothesis,
        observable=node.observable,
        research_axis=node.research_axis,
        minimal_intervention=node.minimal_intervention,
        falsification=node.falsification,
        relationship_to_previous_ideas=node.relationship_to_previous_ideas,
        grounding=node.grounding,
        expected_cost=node.expected_cost,
        status=node.status,
        attempt_refs=node.attempt_refs,
        evidence_refs=node.evidence_refs,
        cognitive_commit_refs=node.cognitive_commit_refs,
        insights=[item.model_dump(mode="json") for item in node.insights],
        children=node.children,
        attempt_summary=attempt_summary,
        created_at=node.created_at,
        updated_at=node.updated_at,
    )


def _attempt_projection(run_dir: Path, attempt: Any, related_idea_ids: list[str]) -> AttemptProjection:
    outcome, assessment, reconciliation, assessment_status = _assessment_artifacts(run_dir, attempt.attempt_id)
    return AttemptProjection(
        attempt_id=attempt.attempt_id,
        attempt_purpose=attempt.attempt_purpose,
        runtime_status=attempt.runtime_status,
        job_type=attempt.job_type,
        pipeline_job_id=attempt.pipeline_job_id,
        required_device_count=attempt.required_device_count,
        required_vram_mb=attempt.required_vram_mb,
        retry_of=attempt.retry_of,
        retry_count=attempt.retry_count,
        max_retries=attempt.max_retries,
        retry_exhausted=attempt.retry_exhausted,
        failure_code=attempt.failure_code,
        command_plan_summary=" ".join([attempt.command_plan.program, *attempt.command_plan.args]),
        execution_outcome=outcome,
        scientific_assessment=assessment if assessment_status == "available" else None,
        assessment_reconciliation=reconciliation if assessment_status == "available" else None,
        scientific_assessment_status=assessment_status,
        related_idea_ids=related_idea_ids,
        pid=attempt.pid,
        heartbeat_at=attempt.heartbeat_at,
        resource_lease_id=attempt.resource_lease_id,
        created_at=attempt.created_at,
        updated_at=attempt.updated_at,
    )


def _assessment_artifacts(
    run_dir: Path,
    attempt_id: str,
) -> tuple[
    OutcomeCard | None,
    ScientificAssessment | None,
    AssessmentReconciliation | None,
    Literal["available", "not_materialized", "invalid"],
]:
    """Read and bind assessment sidecars without creating or repairing artifacts."""

    directory = run_dir / "attempts" / attempt_id
    outcome_path = directory / "outcome_card.json"
    inputs_path = directory / "scientific_evaluation_inputs.json"
    assessment_path = directory / "scientific_assessment.json"
    reconciliation_path = directory / "assessment_reconciliation.json"
    outcome = _read_model(outcome_path, OutcomeCard)
    assessment = _read_model(assessment_path, ScientificAssessment)
    reconciliation = _read_model(reconciliation_path, AssessmentReconciliation)
    if outcome is not None and outcome.attempt_id != attempt_id:
        outcome = None
    if assessment is not None and assessment.attempt_id != attempt_id:
        assessment = None
    if reconciliation is not None and reconciliation.attempt_id != attempt_id:
        reconciliation = None
    if not assessment_path.is_file():
        status: Literal["available", "not_materialized", "invalid"] = (
            "invalid" if reconciliation_path.is_file() else "not_materialized"
        )
        return outcome, None, None, status
    if outcome is None or assessment is None or reconciliation is None:
        return outcome, None, None, "invalid"
    expected_outcome_ref = f"attempts/{attempt_id}/outcome_card.json"
    expected_inputs_ref = f"attempts/{attempt_id}/scientific_evaluation_inputs.json"
    expected_assessment_ref = f"attempts/{attempt_id}/scientific_assessment.json"
    if (
        not inputs_path.is_file()
        or assessment.outcome_card_ref != expected_outcome_ref
        or assessment.inputs_ref != expected_inputs_ref
        or reconciliation.outcome_card_ref != expected_outcome_ref
        or reconciliation.scientific_assessment_ref != expected_assessment_ref
        or assessment.outcome_card_sha256 != sha256_file(outcome_path)
        or assessment.inputs_sha256 != sha256_file(inputs_path)
        or reconciliation.outcome_card_sha256 != assessment.outcome_card_sha256
        or reconciliation.scientific_assessment_sha256 != sha256_file(assessment_path)
    ):
        return outcome, None, None, "invalid"
    return outcome, assessment, reconciliation, "available"


def _actions_projection(
    session: ExperimentSession,
    attempts: list[AttemptProjection],
    candidate_inventory: CandidateInventory,
    champion: ChampionProjection | None,
) -> ExperimentActionsProjection:
    baseline_launch_available = (
        session.status == "READY_FOR_BASELINE" and session.baseline_status == "not_started"
    )
    if not baseline_launch_available and (
        session.authorization.execution_mode != "approve_each_step" or candidate_inventory.status != "available"
    ):
        return ExperimentActionsProjection()
    if session.authorization.execution_mode != "approve_each_step" or candidate_inventory.status != "available":
        return ExperimentActionsProjection(baseline_launch_available=baseline_launch_available)
    registered_attempts = {item.attempt_id for item in candidate_inventory.candidates}
    confirmations = [
        CandidateConfirmationAction(candidate_attempt_id=item.attempt_id)
        for item in attempts
        if (
            item.job_type == "experiment_attempt"
            and item.runtime_status == "COMPLETED"
            and item.scientific_assessment is not None
            and item.scientific_assessment.scientific_effect == "IMPROVEMENT"
            and item.attempt_id not in registered_attempts
        )
    ]
    promotions = [
        CandidatePromotionAction(candidate_id=item.candidate_id)
        for item in candidate_inventory.candidates
        if (
            item.session_id == session.session_id
            and item.evaluation_contract_hash == session.evaluation_contract_sha256
            and item.b_test_passed
            and item.guardrails_passed
            and (champion is None or item.candidate_id != champion.candidate_id)
        )
    ]
    return ExperimentActionsProjection(
        baseline_launch_available=baseline_launch_available,
        candidate_confirmations=confirmations,
        candidate_promotions=promotions,
    )


def _candidate_inventory(run_dir: Path, *, session_id: str) -> CandidateInventory:
    """Read candidates once, retaining a visible invalid-inventory state."""

    try:
        candidates = CandidateRegistry().list_candidates(run_dir, session_id=session_id)
    except (OSError, ValueError):
        return CandidateInventory(status="invalid")
    return CandidateInventory(candidates=candidates)


def _champion_projection(
    run_dir: Path,
    session: ExperimentSession,
    candidate_inventory: CandidateInventory,
) -> tuple[ChampionStatus, ChampionProjection | None]:
    contract_hash = session.evaluation_contract_sha256
    if contract_hash is None:
        return "absent", None
    if candidate_inventory.status == "invalid":
        return "control_plane_invalid", None
    registry = CandidateRegistry()
    try:
        pointer = registry.current_by_contract(run_dir).get(contract_hash)
    except ValueError:
        return "control_plane_invalid", None
    if pointer is None:
        return "absent", None
    try:
        candidate = next(item for item in candidate_inventory.candidates if item.candidate_id == pointer.candidate_id)
    except StopIteration:
        return "control_plane_invalid", None
    if candidate.session_id != session.session_id or candidate.evaluation_contract_hash != contract_hash:
        return "control_plane_invalid", None
    _, assessment, reconciliation, assessment_status = _assessment_artifacts(run_dir, candidate.attempt_id)
    base = {
        "candidate_id": candidate.candidate_id,
        "session_id": candidate.session_id,
        "evaluation_contract_hash": candidate.evaluation_contract_hash,
        "idea_id": candidate.idea_id,
        "attempt_id": candidate.attempt_id,
    }
    if assessment_status == "not_materialized":
        return "assessment_missing", ChampionProjection(**base, assessment_error="scientific assessment is not materialized")
    if assessment_status != "available":
        return "assessment_invalid", ChampionProjection(**base, assessment_error="scientific assessment artifacts are invalid")
    return "available", ChampionProjection(
        **base,
        scientific_assessment=assessment,
        assessment_reconciliation=reconciliation,
    )


def _read_model(path: Path, model: type[BaseModel]) -> Any | None:
    if not path.is_file():
        return None
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _activity(
    run_dir: Path,
    *,
    session_id: str,
    attempts: dict[str, Any],
    commits: dict[str, CognitiveCommit],
    candidate_ids: set[str],
) -> tuple[list[ActivityCard], bool, bool]:
    cards = []
    for scanned, event in enumerate(iter_events_reverse(run_dir), start=1):
        card = _activity_card(event, session_id, attempts, commits, candidate_ids)
        if card is not None:
            cards.append(card)
            if len(cards) > ACTIVITY_LIMIT:
                return cards[:ACTIVITY_LIMIT], True, False
        if scanned >= ACTIVITY_SCAN_EVENT_LIMIT:
            return cards, False, True
    return cards, False, False


def _activity_card(
    event: dict[str, Any],
    session_id: str,
    attempts: dict[str, Any],
    commits: dict[str, CognitiveCommit],
    candidate_ids: set[str],
) -> ActivityCard | None:
    event_id = event.get("event_id")
    event_type = event.get("type")
    created_at = event.get("created_at")
    payload = event.get("payload")
    if not isinstance(event_id, int) or not isinstance(event_type, str) or not isinstance(created_at, str) or not isinstance(payload, dict):
        return None
    attempt_id = payload.get("attempt_id") if isinstance(payload.get("attempt_id"), str) else None
    candidate_attempt_id = payload.get("candidate_attempt_id") if isinstance(payload.get("candidate_attempt_id"), str) else None
    confirmation_attempt_id = payload.get("confirmation_attempt_id") if isinstance(payload.get("confirmation_attempt_id"), str) else None
    commit_id = payload.get("commit_id") if isinstance(payload.get("commit_id"), str) else None
    candidate_id = payload.get("candidate_id") if isinstance(payload.get("candidate_id"), str) else None
    activity_attempt_id = attempt_id or confirmation_attempt_id or candidate_attempt_id
    related = (
        payload.get("session_id") == session_id
        or (activity_attempt_id is not None and activity_attempt_id in attempts)
        or (candidate_attempt_id is not None and candidate_attempt_id in attempts)
        or (confirmation_attempt_id is not None and confirmation_attempt_id in attempts)
        or (commit_id is not None and commit_id in commits)
        or (candidate_id is not None and candidate_id in candidate_ids)
    )
    if not related:
        return None
    title, summary, kind = _activity_text(event_type, payload)
    if title is None:
        return None
    outcome = None
    if activity_attempt_id is not None and event_type == "experiment.attempt.finalized":
        outcome = {key: value for key, value in payload.items() if key in {"runtime_status", "failure_code"}}
    return ActivityCard(
        event_id=event_id,
        event_type=event_type,
        created_at=created_at,
        title=title,
        summary=summary,
        card_kind=kind,
        related_attempt_id=activity_attempt_id,
        related_commit_id=commit_id,
        related_outcome=outcome,
    )


def _activity_text(event_type: str, payload: dict[str, Any]) -> tuple[str | None, str, str]:
    if event_type == "experiment.session.created":
        return "实验 Session 已创建", "已记录实验 Session。", "session"
    if event_type == "experiment.idea_tree.created":
        return "Idea 树已初始化", "已创建根节点。", "idea_tree"
    if event_type == "experiment.idea_tree.mutated":
        revision = payload.get("tree_revision")
        return "Idea Tree 已更新", f"树版本：{revision}" if isinstance(revision, int) else "树已更新。", "idea_tree"
    mapping = {
        "experiment.attempt.created": ("实验已创建", "attempt"),
        "experiment.attempt.queued": ("实验已排队", "attempt"),
        "experiment.attempt.running": ("实验开始运行", "attempt"),
        "experiment.attempt.finalized": ("实验已完成", "attempt"),
        "experiment.attempt.retry_queued": ("重试已排队", "attempt"),
        "experiment.cognitive_commit.appended": ("认知提交已记录", "cognitive_commit"),
        "experiment.candidate.b_test_queued": ("B_test 已排队", "candidate"),
        "experiment.candidate.registered": ("Candidate 已登记", "candidate"),
    }
    if event_type in mapping:
        title, kind = mapping[event_type]
        return title, "已记录当前实验状态。", kind
    if event_type.startswith("experiment.coordinator."):
        return "研究协调器已更新", "已记录协调周期或恢复事件。", "coordinator"
    if event_type.startswith("experiment.champion."):
        return "Champion 已更新", "当前详情以 Candidate Registry 为准。", "champion"
    return None, "", ""


def _artifact_paths(attempts: list[AttemptProjection], champion: ChampionProjection | None) -> list[str]:
    paths: list[str] = []
    for attempt in attempts:
        base = f"attempts/{attempt.attempt_id}"
        if attempt.execution_outcome is not None:
            paths.append(f"{base}/outcome_card.json")
        if attempt.scientific_assessment is not None:
            paths.append(f"{base}/scientific_assessment.json")
        if attempt.assessment_reconciliation is not None:
            paths.append(f"{base}/assessment_reconciliation.json")
    if champion is not None:
        paths.append(f"experiments/champions/candidates/{champion.candidate_id}.json")
    return paths
