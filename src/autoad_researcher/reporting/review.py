"""Explicit report review and confirmed, narrowly scoped experiment handoffs."""

from __future__ import annotations

import json
import os
import shutil
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.assistant.v2.experiment.candidate_control import (
    CandidateControlService,
    CandidateLaunchInput,
)
from autoad_researcher.assistant.v2.research_intent_summary import (
    ResearchIntentSummary,
    save_research_intent_summary,
)
from autoad_researcher.assistant.v2.task_bridge import TaskBridge
from autoad_researcher.core.run_id import run_dir_path
from autoad_researcher.experiment.attempt_service import ExperimentAttemptService
from autoad_researcher.experiment.attempt_store import ExperimentAttemptStore
from autoad_researcher.reporting.evidence import EvidenceIndex
from autoad_researcher.reporting.narrative import NarrativeSectionsV1
from autoad_researcher.reporting.store import ReportStore
from autoad_researcher.schemas.artifacts import ArtifactReferenceV2
from autoad_researcher.task_workspace.task_profile import create_task_profile

ProposalType = Literal["ADD_CONFIRMATION", "RETRY_FAILED", "REFINE_CURRENT", "PIVOT", "REQUEST_HUMAN"]
ProposalStatus = Literal["DRAFT", "READY_FOR_CONFIRMATION", "CONFIRMED", "REJECTED", "SUPERSEDED", "HANDED_OFF"]


class ReviewDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision_id: str
    request_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]+$")
    report_id: str
    version: int
    decision: Literal["accept", "suspend", "needs_more", "needs_repair", "needs_pivot", "disputed"]
    user_comment: str = ""
    accepted_claims: list[str] = Field(default_factory=list)
    disputed_claims: list[str] = Field(default_factory=list)
    requested_follow_up: list[str] = Field(default_factory=list)
    created_at: str


class PivotTaskContext(BaseModel):
    """User-confirmable inputs for a new run; never a copied Session contract."""

    model_config = ConfigDict(extra="forbid")

    task_title: str | None = Field(default=None, max_length=30)
    user_request: str = Field(min_length=1)
    research_summary: ResearchIntentSummary


class FollowUpProposal(BaseModel):
    """A frozen request that can be reviewed before any control-plane action."""

    model_config = ConfigDict(extra="forbid")
    proposal_id: str
    source_report_id: str
    source_report_version: int
    source_session_id: str
    source_snapshot_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    proposal_type: ProposalType
    rationale: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[ArtifactReferenceV2] = Field(default_factory=list)
    requested_changes: list[str] = Field(default_factory=list)
    required_experiments: list[str] = Field(default_factory=list)
    estimated_budget: str | None = None
    unresolved_questions: list[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "medium"
    target_attempt_id: str | None = Field(default=None, pattern=r"^attempt_[0-9]{6}$")
    candidate_attempt_id: str | None = Field(default=None, pattern=r"^attempt_[0-9]{6}$")
    noise_threshold: float | None = Field(default=None, ge=0)
    refine_input: CandidateLaunchInput | None = None
    pivot_context: PivotTaskContext | None = None
    validation_errors: list[str] = Field(default_factory=list)
    status: ProposalStatus = "DRAFT"
    created_at: str
    confirmed_at: str | None = None
    rejected_at: str | None = None
    handoff: dict[str, str] | None = None


def create_proposal(
    run_dir: Path,
    *,
    report_id: str,
    proposal_type: ProposalType,
    rationale: str,
    evidence_ids: list[str] | None = None,
    requested_changes: list[str] | None = None,
    required_experiments: list[str] | None = None,
    estimated_budget: str | None = None,
    unresolved_questions: list[str] | None = None,
    risk_level: Literal["low", "medium", "high"] = "medium",
    target_attempt_id: str | None = None,
    candidate_attempt_id: str | None = None,
    noise_threshold: float | None = None,
    refine_input: CandidateLaunchInput | None = None,
    pivot_context: PivotTaskContext | None = None,
) -> FollowUpProposal:
    manifest = ReportStore().load_manifest(run_dir, report_id)
    draft = FollowUpProposal(
        proposal_id=f"proposal_{uuid4().hex}", source_report_id=report_id,
        source_report_version=manifest.version, source_session_id=manifest.session_id,
        source_snapshot_content_sha256=manifest.source_snapshot_content_sha256,
        proposal_type=proposal_type, rationale=rationale, evidence_ids=evidence_ids or [],
        evidence_refs=_resolve_evidence_refs(run_dir, report_id, evidence_ids or []),
        requested_changes=requested_changes or [], required_experiments=required_experiments or [],
        estimated_budget=estimated_budget, unresolved_questions=unresolved_questions or [],
        risk_level=risk_level, target_attempt_id=target_attempt_id,
        candidate_attempt_id=candidate_attempt_id, noise_threshold=noise_threshold,
        refine_input=refine_input, pivot_context=pivot_context,
        created_at=_utc_now(),
    )
    errors = validate_proposal(run_dir, draft)
    proposal = draft.model_copy(update={"validation_errors": errors, "status": "READY_FOR_CONFIRMATION" if not errors else "DRAFT"})
    _write(run_dir, report_id, "proposals", proposal.proposal_id, proposal)
    append_event(run_dir, "report.proposal.created", {"report_id": report_id, "proposal_id": proposal.proposal_id, "status": proposal.status})
    return proposal


def validate_proposal(run_dir: Path, proposal: FollowUpProposal) -> list[str]:
    """Validate durable report/session state only; this path never enqueues work."""
    errors: list[str] = []
    try:
        manifest = ReportStore().load_manifest(run_dir, proposal.source_report_id)
    except FileNotFoundError:
        return ["source report no longer exists"]
    if manifest.source_snapshot_content_sha256 != proposal.source_snapshot_content_sha256:
        errors.append("source report snapshot hash changed")
    if manifest.session_id != proposal.source_session_id:
        errors.append("source report session changed")
    try:
        evidence = _load_index(run_dir, proposal.source_report_id)
        registered = {item.artifact_ref.locator: item.artifact_ref.sha256 for item in evidence.entries}
        if any(registered.get(ref.locator) != ref.sha256 for ref in proposal.evidence_refs):
            errors.append("proposal contains an unregistered or changed evidence reference")
        expected_refs = _resolve_evidence_refs(run_dir, proposal.source_report_id, proposal.evidence_ids)
        if proposal.evidence_refs != expected_refs:
            errors.append("proposal evidence IDs and references differ")
    except FileNotFoundError:
        errors.append("report evidence index is unavailable")
    except ValueError as exc:
        errors.append(str(exc))

    if proposal.proposal_type in {"REFINE_CURRENT", "PIVOT"} and not proposal.requested_changes:
        errors.append(f"{proposal.proposal_type} requires at least one requested change")
    if proposal.proposal_type in {"RETRY_FAILED", "ADD_CONFIRMATION"} and proposal.requested_changes:
        errors.append(f"{proposal.proposal_type} may not alter the existing evaluation contract")
    attempts = ExperimentAttemptStore()
    if proposal.proposal_type == "RETRY_FAILED":
        attempt = attempts.load(run_dir, proposal.target_attempt_id or "")
        if attempt is None or attempt.session_id != proposal.source_session_id:
            errors.append("RETRY_FAILED must bind an Attempt in the source Session")
        elif attempt.runtime_status not in {"FAILED", "TIMED_OUT", "LOST"}:
            errors.append("RETRY_FAILED requires a failed terminal Attempt")
    elif proposal.proposal_type == "ADD_CONFIRMATION":
        if proposal.candidate_attempt_id is None or proposal.noise_threshold is None:
            errors.append("ADD_CONFIRMATION requires candidate_attempt_id and noise_threshold")
    elif proposal.proposal_type == "REFINE_CURRENT":
        if proposal.refine_input is None:
            errors.append("REFINE_CURRENT requires a separately reviewed candidate launch input")
    elif proposal.proposal_type == "PIVOT":
        if proposal.pivot_context is None:
            errors.append("PIVOT requires a new task context; it cannot reuse a materialized Session")
    return errors


def load_proposal(run_dir: Path, *, report_id: str, proposal_id: str) -> FollowUpProposal:
    return FollowUpProposal.model_validate_json(_proposal_path(run_dir, report_id, proposal_id).read_text(encoding="utf-8"))


def reject_proposal(run_dir: Path, *, report_id: str, proposal_id: str) -> FollowUpProposal:
    with _proposal_lock(run_dir, report_id):
        proposal = load_proposal(run_dir, report_id=report_id, proposal_id=proposal_id)
        if proposal.status == "HANDED_OFF":
            raise ValueError("handed-off proposal may not be rejected")
        if proposal.status == "REJECTED":
            return proposal
        updated = proposal.model_copy(update={"status": "REJECTED", "rejected_at": _utc_now()})
        _write(run_dir, report_id, "proposals", proposal_id, updated)
    append_event(run_dir, "report.proposal.rejected", {"report_id": report_id, "proposal_id": proposal_id})
    return updated


def confirm_proposal(run_dir: Path, *, report_id: str, proposal_id: str) -> FollowUpProposal:
    """Confirm once, then delegate only to a pre-existing safe control-plane path."""
    with _proposal_lock(run_dir, report_id):
        proposal = load_proposal(run_dir, report_id=report_id, proposal_id=proposal_id)
        if proposal.status == "REJECTED":
            raise ValueError("rejected proposal may not be handed off")
        if proposal.status == "HANDED_OFF":
            return proposal
        errors = validate_proposal(run_dir, proposal)
        if errors:
            updated = proposal.model_copy(update={"validation_errors": errors, "status": "DRAFT"})
            _write(run_dir, report_id, "proposals", proposal_id, updated)
            raise ValueError("proposal is not ready for confirmation: " + "; ".join(errors))
        confirmed = proposal.model_copy(update={"status": "CONFIRMED", "confirmed_at": proposal.confirmed_at or _utc_now()})
        _write(run_dir, report_id, "proposals", proposal_id, confirmed)
        handed_off = _handoff(run_dir, confirmed)
        _write(run_dir, report_id, "proposals", proposal_id, handed_off)
    append_event(run_dir, "report.proposal.handed_off", {"report_id": report_id, "proposal_id": proposal_id, "proposal_type": proposal.proposal_type, "handoff": handed_off.handoff or {}})
    return handed_off


def _handoff(run_dir: Path, proposal: FollowUpProposal) -> FollowUpProposal:
    if proposal.proposal_type == "REQUEST_HUMAN":
        return proposal.model_copy(update={"status": "HANDED_OFF", "handoff": {"kind": "human_queue", "proposal_id": proposal.proposal_id}})
    if proposal.proposal_type == "RETRY_FAILED":
        started = ExperimentAttemptService().create_retry(run_dir, attempt_id=proposal.target_attempt_id or "")
        return proposal.model_copy(update={"status": "HANDED_OFF", "handoff": {"kind": "retry", "attempt_id": started.attempt.attempt_id, "pipeline_job_id": str(started.pipeline_job["job_id"])}})
    if proposal.proposal_type == "ADD_CONFIRMATION":
        from autoad_researcher.assistant.v2.experiment.candidate_confirmation import CandidateConfirmationInput, CandidateConfirmationService
        result = CandidateConfirmationService().start(run_dir, session_id=proposal.source_session_id, value=CandidateConfirmationInput(candidate_attempt_id=proposal.candidate_attempt_id or "", noise_threshold=proposal.noise_threshold or 0, idempotency_key=f"report-proposal:{proposal.proposal_id}"))
        return proposal.model_copy(update={"status": "HANDED_OFF", "handoff": {"kind": "confirmation", "attempt_id": result.started.attempt.attempt_id, "pipeline_job_id": str(result.started.pipeline_job["job_id"])}})
    if proposal.proposal_type == "REFINE_CURRENT":
        assert proposal.refine_input is not None
        result = CandidateControlService().start(
            run_dir,
            session_id=proposal.source_session_id,
            value=proposal.refine_input.model_copy(
                update={"idempotency_key": f"report-proposal:{proposal.proposal_id}"}
            ),
        )
        if result.status not in {"queued", "reused"} or result.attempt is None or result.pipeline_job is None:
            raise ValueError(result.blocker or "REFINE_CURRENT could not create a candidate Attempt")
        return proposal.model_copy(update={"status": "HANDED_OFF", "handoff": {
            "kind": "refine", "attempt_id": str(result.attempt["attempt_id"]),
            "pipeline_job_id": str(result.pipeline_job["job_id"]),
        }})
    if proposal.proposal_type == "PIVOT":
        return _handoff_pivot(run_dir, proposal)
    raise ValueError("proposal type has no safe handoff")


def record_review(
    run_dir: Path,
    *,
    report_id: str,
    request_id: str,
    decision: Literal["accept", "suspend", "needs_more", "needs_repair", "needs_pivot", "disputed"],
    user_comment: str = "",
    accepted_claims: list[str] | None = None,
    disputed_claims: list[str] | None = None,
    requested_follow_up: list[str] | None = None,
) -> ReviewDecision:
    """Append one review decision with request-id replay protection."""

    manifest = ReportStore().load_manifest(run_dir, report_id)
    candidate = ReviewDecision(
        decision_id="",
        request_id=request_id,
        report_id=report_id,
        version=manifest.version,
        decision=decision,
        user_comment=user_comment,
        accepted_claims=accepted_claims or [],
        disputed_claims=disputed_claims or [],
        requested_follow_up=requested_follow_up or [],
        created_at="",
    )
    _validate_claim_ids(run_dir, report_id, candidate.accepted_claims, candidate.disputed_claims)
    with _proposal_lock(run_dir, report_id):
        for existing in _load_reviews(run_dir, report_id):
            if existing.request_id != request_id:
                continue
            if _review_payload(existing) == _review_payload(candidate):
                return existing
            raise ValueError("review request_id conflicts with an existing decision")
        item = candidate.model_copy(update={
            "decision_id": f"review_{uuid4().hex}",
            "created_at": _utc_now(),
        })
        _write(run_dir, report_id, "reviews", item.decision_id, item)
        ReportStore().set_review_status(
            run_dir,
            report_id=report_id,
            status=_review_status_for(item.decision),
        )
    append_event(
        run_dir,
        "report.review.recorded",
        {"report_id": report_id, "decision_id": item.decision_id, "decision": decision},
    )
    return item


def _resolve_evidence_refs(run_dir: Path, report_id: str, evidence_ids: list[str]) -> list[ArtifactReferenceV2]:
    entries = {item.evidence_id: item.artifact_ref for item in _load_index(run_dir, report_id).entries}
    unknown = set(evidence_ids).difference(entries)
    if unknown:
        raise ValueError("proposal references unknown Evidence IDs")
    return [entries[item] for item in evidence_ids]


def _handoff_pivot(run_dir: Path, proposal: FollowUpProposal) -> FollowUpProposal:
    """Create an isolated, pending task context for a direction change.

    A report confirmation authorizes creation of the new task context. It does
    not silently select a repository or start execution in the new run; those
    remain explicit confirmations under that run's TaskBridge contract.
    """

    assert proposal.pivot_context is not None
    context = proposal.pivot_context
    new_run_id = f"pivot_{proposal.proposal_id.removeprefix('proposal_')}"
    new_run_dir = run_dir_path(run_dir.parent, new_run_id)
    lineage = {
        "schema_version": 1,
        "parent_run_id": run_dir.name,
        "parent_report_id": proposal.source_report_id,
        "parent_report_version": proposal.source_report_version,
        "parent_session_id": proposal.source_session_id,
        "parent_snapshot_content_sha256": proposal.source_snapshot_content_sha256,
        "proposal_id": proposal.proposal_id,
    }
    lineage_path = new_run_dir / "report_pivot_lineage.json"

    if new_run_dir.exists():
        if not lineage_path.is_file():
            raise ValueError("pivot run identity is occupied without matching lineage")
        if _read_json(lineage_path) != lineage:
            raise ValueError("pivot run identity conflicts with a different proposal")
        draft = TaskBridge.load_pending_experiment_task(new_run_dir)
    else:
        new_run_dir.mkdir(parents=True, exist_ok=False)
        try:
            for name in ("sources", "ui_chat", "context", "chat"):
                (new_run_dir / name).mkdir(exist_ok=True)
            create_task_profile(
                run_dir=new_run_dir,
                run_id=new_run_id,
                task_title=context.task_title,
                created_at=datetime.now(timezone.utc),
            )
            save_research_intent_summary(new_run_dir, context.research_summary)
            draft = TaskBridge.build_experiment_task(new_run_dir, user_input=context.user_request)
            _write_json_atomic(lineage_path, lineage)
            append_event(new_run_dir, "report.pivot.task_created", lineage | {"task_id": draft.task_id})
        except Exception:
            shutil.rmtree(new_run_dir, ignore_errors=True)
            raise

    return proposal.model_copy(update={"status": "HANDED_OFF", "handoff": {
        "kind": "pivot_task_context",
        "run_id": new_run_id,
        "task_id": draft.task_id,
        "task_status": draft.status,
        "lineage_ref": "report_pivot_lineage.json",
    }})


def _validate_claim_ids(
    run_dir: Path,
    report_id: str,
    accepted_claims: list[str],
    disputed_claims: list[str],
) -> None:
    requested = [*accepted_claims, *disputed_claims]
    if len(requested) != len(set(requested)):
        raise ValueError("review claim IDs must not repeat")
    path = run_dir / "reports" / report_id / "narrative_sections.json"
    if not path.is_file():
        raise ValueError("report narrative is unavailable for claim review")
    narrative = NarrativeSectionsV1.model_validate_json(path.read_text(encoding="utf-8"))
    available = {
        section.claim_id or f"claim_{section.section_id}"
        for section in narrative.sections
    }
    unknown = sorted(set(requested).difference(available))
    if unknown:
        raise ValueError("review references unknown claim IDs: " + ", ".join(unknown))


def _load_reviews(run_dir: Path, report_id: str) -> list[ReviewDecision]:
    directory = run_dir / "reports" / report_id / "reviews"
    if not directory.is_dir():
        return []
    reviews = [
        ReviewDecision.model_validate_json(path.read_text(encoding="utf-8"))
        for path in directory.glob("review_*.json")
    ]
    return sorted(reviews, key=lambda item: (item.created_at, item.decision_id))


def _review_payload(item: ReviewDecision) -> dict[str, object]:
    return item.model_dump(exclude={"decision_id", "created_at"})


def _review_status_for(decision: str) -> str:
    return {
        "accept": "accepted",
        "suspend": "unreviewed",
        "needs_more": "needs_more",
        "needs_repair": "needs_repair",
        "needs_pivot": "needs_more",
        "disputed": "disputed",
    }[decision]


def _read_json(path: Path) -> dict[str, object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("pivot lineage is invalid")
    return raw


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _load_index(run_dir: Path, report_id: str) -> EvidenceIndex:
    return EvidenceIndex.model_validate_json((run_dir / "reports" / report_id / "evidence_index.json").read_text(encoding="utf-8"))


def _proposal_path(run_dir: Path, report_id: str, proposal_id: str) -> Path:
    if not proposal_id or "/" in proposal_id or "\\" in proposal_id:
        raise ValueError("invalid proposal_id")
    return run_dir / "reports" / report_id / "proposals" / f"{proposal_id}.json"


def _write(run_dir: Path, report_id: str, directory: str, name: str, item: BaseModel) -> None:
    path = run_dir / "reports" / report_id / directory / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(item.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n")
        handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary, path)


@contextmanager
def _proposal_lock(run_dir: Path, report_id: str):
    path = run_dir / "reports" / report_id / "proposals" / ".proposal.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 5.0
    fd: int | None = None
    while time.monotonic() < deadline:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR); break
        except FileExistsError:
            time.sleep(0.05)
    if fd is None:
        raise TimeoutError("could not acquire report proposal lock")
    try:
        yield
    finally:
        os.close(fd)
        try:
            path.unlink()
        except OSError:
            pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
