"""Server-owned Candidate Proposal generation and approval boundary."""

from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.assistant.v2.evidence_service import load_usable_evidence
from autoad_researcher.assistant.v2.experiment.baseline_control import BaselineControlService
from autoad_researcher.assistant.v2.experiment.candidate_control import (
    CandidateControlService,
    CandidateLaunchInput,
    CandidateLaunchResult,
)
from autoad_researcher.benchmarks.hashing import canonical_sha256, sha256_file
from autoad_researcher.experiment.attempt_store import ExperimentAttemptStore
from autoad_researcher.experiment.coordinator import CompactCycleService, CycleDecision, IdeaCandidate
from autoad_researcher.experiment.evaluation_contract import EvaluationContract
from autoad_researcher.experiment.executor_adapters import ExecutorAdapter, ExecutorAdapterEvidence
from autoad_researcher.experiment.executor_agent import ExecutorProposal
from autoad_researcher.experiment.executor_contracts import InterventionContract
from autoad_researcher.experiment.finalizer import OutcomeCard
from autoad_researcher.experiment.idea_tree import IdeaTree, IdeaTreeMutation, IdeaTreeStore
from autoad_researcher.experiment.session_store import ExperimentSessionStore
from autoad_researcher.ui.chat_client import call_research_chat


PROPOSALS_DIR = "experiments/candidate_proposals"
_PROPOSAL_ID_RE = re.compile(r"^proposal_[0-9a-f]{16}$")
_MAX_FILE_CONTEXT_CHARS = 24_000


class CandidateInterventionDraft(BaseModel):
    """The model may describe a bounded change, never a command or repository."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    target_modules: list[str] = Field(min_length=1)
    allowed_paths: list[str] = Field(min_length=1)
    forbidden_paths: list[str] = Field(default_factory=list)
    allowed_parameters: list[str] | dict[str, Any] = Field(default_factory=list)
    evaluation_invariants: list[str] = Field(default_factory=list)
    time_budget: int = Field(gt=0)


class CandidateProposalDraft(BaseModel):
    """One schema-bound proposal returned by the experiment agent."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    mechanism: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    observable: str = Field(min_length=1)
    research_axis: str = Field(min_length=1)
    minimal_intervention: str = Field(min_length=1)
    falsification: str = Field(min_length=1)
    expected_cost: Literal["unknown", "low", "medium", "high"]
    relationship_to_previous_ideas: str = Field(min_length=1)
    grounding: list[str] = Field(default_factory=list)
    intervention: CandidateInterventionDraft
    executor_proposal: ExecutorProposal


class CandidateProposal(BaseModel):
    """Durable review object; the browser receives this projection only."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    proposal_id: str = Field(pattern=r"^proposal_[0-9a-f]{16}$")
    run_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    status: Literal["generating", "pending_review", "approved", "rejected", "started"]
    idea_node_id: str = Field(pattern=r"^idea_[0-9]{6}$")
    idea_tree_revision: int = Field(ge=1)
    evaluation_contract_ref: str = Field(min_length=1)
    evaluation_contract_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    idea: IdeaCandidate
    candidate: CandidateLaunchInput
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)
    decided_by: str | None = None
    attempt_id: str | None = None


class CandidateProposalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    proposal: CandidateProposal
    candidate: CandidateLaunchResult | None = None
    blocker: str | None = None


class CandidateProposalGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=1, max_length=160)


class CandidateProposalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved_by: str = Field(default="user", min_length=1, max_length=80)


CandidateProposalProvider = Callable[[dict[str, Any]], CandidateProposalDraft | dict[str, Any]]


class CandidateProposalStore:
    """One atomic JSON record per proposal, following the existing run stores."""

    def load(self, run_dir: Path, *, session_id: str, proposal_id: str) -> CandidateProposal | None:
        path = self._path(run_dir, session_id=session_id, proposal_id=proposal_id)
        if not path.is_file():
            return None
        value = CandidateProposal.model_validate_json(path.read_text(encoding="utf-8"))
        self._verify_content(value)
        return value

    def list_for_session(self, run_dir: Path, *, session_id: str) -> list[CandidateProposal]:
        directory = self._directory(run_dir, session_id)
        if not directory.is_dir():
            return []
        values: list[CandidateProposal] = []
        for path in sorted(directory.glob("proposal_*.json")):
            value = CandidateProposal.model_validate_json(path.read_text(encoding="utf-8"))
            self._verify_content(value)
            values.append(value)
        return values

    def find_by_idempotency(self, run_dir: Path, *, session_id: str, idempotency_key: str) -> CandidateProposal | None:
        return next(
            (item for item in self.list_for_session(run_dir, session_id=session_id) if item.idempotency_key == idempotency_key),
            None,
        )

    def pending(self, run_dir: Path, *, session_id: str) -> CandidateProposal | None:
        return next(
            (item for item in self.list_for_session(run_dir, session_id=session_id) if item.status in {"generating", "pending_review", "approved"}),
            None,
        )

    def create(self, run_dir: Path, value: CandidateProposal) -> CandidateProposal:
        self._verify_content(value)
        path = self._path(run_dir, session_id=value.session_id, proposal_id=value.proposal_id)
        with self._lock(run_dir):
            existing = self.load(run_dir, session_id=value.session_id, proposal_id=value.proposal_id)
            if existing is not None:
                if existing.content_sha256 != value.content_sha256:
                    raise ValueError("idempotency_conflict: candidate proposal differs")
                return existing
            pending = self.pending(run_dir, session_id=value.session_id)
            if pending is not None and pending.proposal_id != value.proposal_id:
                raise ValueError("candidate proposal review is already pending")
            self._write_unlocked(path, value)
        append_event(run_dir, "experiment.candidate_proposal.created", value.model_dump(mode="json", exclude={"candidate"}))
        return value

    def update(self, run_dir: Path, value: CandidateProposal) -> CandidateProposal:
        self._verify_content(value)
        path = self._path(run_dir, session_id=value.session_id, proposal_id=value.proposal_id)
        with self._lock(run_dir):
            existing = self.load(run_dir, session_id=value.session_id, proposal_id=value.proposal_id)
            if existing is None:
                raise FileNotFoundError("candidate proposal not found")
            if existing.content_sha256 != value.content_sha256:
                raise ValueError("candidate proposal content is immutable")
            self._write_unlocked(path, value)
        append_event(run_dir, "experiment.candidate_proposal.updated", value.model_dump(mode="json", exclude={"candidate"}))
        return value

    @staticmethod
    def _content(value: CandidateProposal) -> dict[str, Any]:
        return {
            "proposal_id": value.proposal_id,
            "run_id": value.run_id,
            "session_id": value.session_id,
            "idempotency_key": value.idempotency_key,
            "idea_node_id": value.idea_node_id,
            "idea_tree_revision": value.idea_tree_revision,
            "evaluation_contract_ref": value.evaluation_contract_ref,
            "evaluation_contract_sha256": value.evaluation_contract_sha256,
            "idea": value.idea.model_dump(mode="json"),
            "candidate": value.candidate.model_dump(mode="json"),
        }

    @classmethod
    def _verify_content(cls, value: CandidateProposal) -> None:
        if canonical_sha256(cls._content(value)) != value.content_sha256:
            raise ValueError("candidate proposal content hash does not match")

    @staticmethod
    def _directory(run_dir: Path, session_id: str) -> Path:
        return run_dir / PROPOSALS_DIR / session_id

    @classmethod
    def _path(cls, run_dir: Path, *, session_id: str, proposal_id: str) -> Path:
        if not _PROPOSAL_ID_RE.fullmatch(proposal_id):
            raise ValueError("invalid candidate proposal ID")
        return cls._directory(run_dir, session_id) / f"{proposal_id}.json"

    @staticmethod
    def _write_unlocked(path: Path, value: CandidateProposal) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(value.model_dump_json(indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    @contextmanager
    def _lock(run_dir: Path, timeout: float = 5.0):
        path = run_dir / PROPOSALS_DIR / ".proposals.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout
        fd: int | None = None
        while time.monotonic() < deadline:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                break
            except FileExistsError:
                time.sleep(0.05)
        if fd is None:
            raise TimeoutError("could not acquire candidate proposal lock")
        try:
            yield
        finally:
            os.close(fd)
            path.unlink(missing_ok=True)


class CandidateProposalService:
    """Generate a review object, then delegate approved execution to CandidateControlService."""

    def __init__(
        self,
        *,
        store: CandidateProposalStore | None = None,
        sessions: ExperimentSessionStore | None = None,
        trees: IdeaTreeStore | None = None,
        attempts: ExperimentAttemptStore | None = None,
    ) -> None:
        self._store = store or CandidateProposalStore()
        self._sessions = sessions or ExperimentSessionStore()
        self._trees = trees or IdeaTreeStore()
        self._attempts = attempts or ExperimentAttemptStore()

    def generate(
        self,
        run_dir: Path,
        *,
        session_id: str,
        idempotency_key: str,
        provider: CandidateProposalProvider,
        model_profile: str = "experiment-agent",
    ) -> CandidateProposalResult:
        session, contract, baseline, adapter = self._require_generation_state(run_dir, session_id=session_id)
        existing = self._store.find_by_idempotency(run_dir, session_id=session_id, idempotency_key=idempotency_key)
        if existing is not None:
            if existing.status == "generating":
                return self._finish_generating(run_dir, existing, model_profile=model_profile)
            return CandidateProposalResult(status="reused", proposal=existing)
        pending = self._store.pending(run_dir, session_id=session_id)
        if pending is not None:
            return CandidateProposalResult(status="reused", proposal=pending)

        context = self._generation_context(
            run_dir,
            session=session,
            contract=contract,
            baseline=baseline,
            adapter=adapter,
        )
        draft = CandidateProposalDraft.model_validate(provider(context))
        self._validate_draft(draft, adapter=adapter, contract=contract)
        tree, _ = self._trees.create_or_get(run_dir, session_id=session_id)
        idea_node_id = _next_node_id(tree)
        proposal_id = f"proposal_{canonical_sha256({'session_id': session_id, 'idempotency_key': idempotency_key, 'contract': contract.sha256})[:16]}"
        candidate = self._candidate_input(
            draft,
            idea_node_id=idea_node_id,
            contract=contract,
            proposal_id=proposal_id,
            protected_paths=adapter.protected_paths,
        )
        proposal = CandidateProposal(
            proposal_id=proposal_id,
            run_id=run_dir.name,
            session_id=session_id,
            idempotency_key=idempotency_key,
            status="generating",
            idea_node_id=idea_node_id,
            idea_tree_revision=tree.revision + 1,
            evaluation_contract_ref=session.evaluation_contract_ref or "",
            evaluation_contract_sha256=session.evaluation_contract_sha256 or "",
            idea=_idea_candidate(draft),
            candidate=candidate,
            content_sha256="0" * 64,
            created_at=_utc_now(),
            updated_at=_utc_now(),
        )
        proposal = proposal.model_copy(update={"content_sha256": canonical_sha256(CandidateProposalStore._content(proposal))})
        self._store.create(run_dir, proposal)
        return self._finish_generating(run_dir, proposal, model_profile=model_profile, draft=draft)

    def approve(
        self,
        run_dir: Path,
        *,
        session_id: str,
        proposal_id: str,
        approved_by: str = "user",
    ) -> CandidateProposalResult:
        proposal = self._store.load(run_dir, session_id=session_id, proposal_id=proposal_id)
        if proposal is None:
            raise FileNotFoundError("candidate proposal not found")
        if proposal.status == "rejected":
            raise ValueError("candidate proposal was rejected")
        if proposal.status == "generating":
            raise ValueError("candidate proposal is still being generated")
        self._validate_current_binding(run_dir, proposal)
        if proposal.status == "started":
            return CandidateProposalResult(status="reused", proposal=proposal)
        approved = proposal.model_copy(update={"status": "approved", "decided_by": approved_by, "updated_at": _utc_now()})
        self._store.update(run_dir, approved)
        try:
            result = CandidateControlService().start(run_dir, session_id=session_id, value=approved.candidate)
        except ValueError:
            raise
        if result.attempt is None:
            return CandidateProposalResult(status=result.status, proposal=approved, candidate=result, blocker=result.blocker)
        started = approved.model_copy(update={"status": "started", "attempt_id": str(result.attempt["attempt_id"]), "updated_at": _utc_now()})
        self._store.update(run_dir, started)
        return CandidateProposalResult(status="started", proposal=started, candidate=result)

    def reject(
        self,
        run_dir: Path,
        *,
        session_id: str,
        proposal_id: str,
        rejected_by: str = "user",
    ) -> CandidateProposalResult:
        proposal = self._store.load(run_dir, session_id=session_id, proposal_id=proposal_id)
        if proposal is None:
            raise FileNotFoundError("candidate proposal not found")
        if proposal.status == "started":
            raise ValueError("started candidate proposal cannot be rejected")
        if proposal.status == "rejected":
            return CandidateProposalResult(status="reused", proposal=proposal)
        rejected = proposal.model_copy(update={"status": "rejected", "decided_by": rejected_by, "updated_at": _utc_now()})
        self._store.update(run_dir, rejected)
        return CandidateProposalResult(status="rejected", proposal=rejected)

    def _finish_generating(
        self,
        run_dir: Path,
        proposal: CandidateProposal,
        *,
        model_profile: str,
        draft: CandidateProposalDraft | None = None,
    ) -> CandidateProposalResult:
        tree, _ = self._trees.create_or_get(run_dir, session_id=proposal.session_id)
        existing_node = tree.node(proposal.idea_node_id) if any(item.node_id == proposal.idea_node_id for item in tree.nodes) else None
        if existing_node is None:
            draft = draft or CandidateProposalDraft(
                mechanism=proposal.idea.mechanism,
                hypothesis=proposal.idea.hypothesis,
                observable=proposal.idea.observable,
                research_axis=proposal.idea.research_axis,
                minimal_intervention=proposal.idea.minimal_intervention,
                falsification=proposal.idea.falsification,
                expected_cost=proposal.idea.expected_cost,
                relationship_to_previous_ideas=proposal.idea.relationship_to_previous_ideas,
                grounding=proposal.idea.grounding,
                intervention=CandidateInterventionDraft(
                    target_modules=proposal.candidate.intervention_contract.target_modules,
                    allowed_paths=proposal.candidate.intervention_contract.allowed_paths,
                    forbidden_paths=proposal.candidate.intervention_contract.forbidden_paths,
                    allowed_parameters=proposal.candidate.intervention_contract.allowed_parameters,
                    evaluation_invariants=proposal.candidate.intervention_contract.evaluation_invariants,
                    time_budget=proposal.candidate.intervention_contract.time_budget,
                ),
                executor_proposal=proposal.candidate.approved_proposal,
            )
            decision = _cycle_decision(draft)
            cycle = CompactCycleService().run(
                run_dir,
                session_id=proposal.session_id,
                cycle_id=f"candidate-proposal:{proposal.proposal_id}",
                observation="Baseline completed; preparing one bounded candidate proposal.",
                ideation_focus="one minimal falsifiable intervention grounded in the baseline evidence",
                decision_provider=lambda _context: decision,
                model_profile=model_profile,
                prompt_version="candidate-proposal-v1",
            )
            existing_node = cycle.tree.node(proposal.idea_node_id)
            tree = cycle.tree
        if existing_node.mechanism != proposal.candidate.intervention_contract.mechanism or existing_node.hypothesis != proposal.candidate.intervention_contract.hypothesis:
            raise ValueError("candidate proposal IdeaTree binding differs")
        if tree.revision != proposal.idea_tree_revision:
            updated = proposal.model_copy(update={"idea_tree_revision": tree.revision, "updated_at": _utc_now()})
            updated = updated.model_copy(update={"content_sha256": canonical_sha256(CandidateProposalStore._content(updated))})
            proposal = self._store.update(run_dir, updated)
        pending = proposal.model_copy(update={"status": "pending_review", "updated_at": _utc_now()})
        self._store.update(run_dir, pending)
        return CandidateProposalResult(status="created", proposal=pending)

    def _require_generation_state(self, run_dir: Path, *, session_id: str):
        session = self._sessions.load(run_dir, session_id)
        if session is None:
            raise FileNotFoundError("experiment session not found")
        if session.authorization.execution_mode == "plan_only":
            raise ValueError("plan_only Session may not generate a candidate proposal")
        if not (
            (session.status == "READY_FOR_BASELINE" and session.baseline_status == "b_dev_completed")
            or (session.status == "READY" and session.baseline_status == "completed")
        ):
            raise ValueError("candidate proposal requires a completed baseline B_dev")
        if not session.evaluation_contract_ref or not session.evaluation_contract_sha256:
            raise ValueError("execution_contract_incomplete: baseline evaluation contract is missing")
        contract_path = run_dir / session.evaluation_contract_ref
        if not contract_path.is_file() or sha256_file(contract_path) != session.evaluation_contract_sha256:
            raise ValueError("execution_contract_incomplete: frozen evaluation contract changed")
        contract = EvaluationContract.model_validate_json(contract_path.read_text(encoding="utf-8"))
        baseline = next(
            (item for item in self._attempts.list_for_session(run_dir, session_id=session_id) if item.job_type == "experiment_baseline" and item.runtime_status == "COMPLETED"),
            None,
        )
        if baseline is None:
            raise ValueError("candidate proposal requires a completed baseline Attempt")
        outcome_path = run_dir / "attempts" / baseline.attempt_id / "outcome_card.json"
        if not outcome_path.is_file():
            raise ValueError("candidate proposal requires baseline OutcomeCard")
        outcome = OutcomeCard.model_validate_json(outcome_path.read_text(encoding="utf-8"))
        binding = BaselineControlService._load_binding(run_dir, session)
        adapter_result = ExecutorAdapter().inspect(run_dir / binding.repository_ref)
        if adapter_result.status != "supported" or adapter_result.evidence is None:
            raise ValueError(adapter_result.blocker or "execution adapter is unsupported")
        return session, contract, outcome, adapter_result.evidence

    def _generation_context(self, run_dir: Path, *, session, contract: EvaluationContract, baseline: OutcomeCard, adapter: ExecutorAdapterEvidence) -> dict[str, Any]:
        task_path = run_dir / session.task_ref
        task = yaml.safe_load(task_path.read_text(encoding="utf-8")) if task_path.is_file() else {}
        binding = BaselineControlService._load_binding(run_dir, session)
        repository = run_dir / binding.repository_ref
        files: dict[str, str] = {}
        remaining = _MAX_FILE_CONTEXT_CHARS
        for relative in [adapter.entrypoint, *adapter.allowed_paths]:
            if relative in files or remaining <= 0:
                continue
            path = repository / relative
            if not path.is_file():
                continue
            content = path.read_text(encoding="utf-8", errors="replace")[:remaining]
            files[relative] = content
            remaining -= len(content)
        evidence = [
            {
                "source_id": item.get("source_id", ""),
                "evidence_type": item.get("evidence_type", ""),
                "artifact_path": item.get("artifact_path", ""),
                "summary": str(item.get("summary", ""))[:4_000],
            }
            for item in load_usable_evidence(run_dir)
            if isinstance(item, dict)
        ]
        return {
            "task": task,
            "session": session.model_dump(mode="json"),
            "evaluation_contract": contract.model_dump(mode="json"),
            "baseline_outcome": baseline.model_dump(mode="json"),
            "adapter": adapter.model_dump(mode="json"),
            "research_evidence": evidence,
            "repository_files": files,
        }

    @staticmethod
    def _validate_draft(draft: CandidateProposalDraft, *, adapter: ExecutorAdapterEvidence, contract: EvaluationContract) -> None:
        allowed = set(adapter.allowed_paths)
        protected = set(adapter.protected_paths) | {metric.implementation_ref for metric in contract.metrics}
        paths = [*draft.intervention.target_modules, *draft.intervention.allowed_paths, *draft.intervention.forbidden_paths]
        for path in paths:
            _safe_relative(path)
        if not set(draft.intervention.allowed_paths).issubset(allowed):
            raise ValueError("candidate proposal contains a path outside the adapter allowlist")
        if not set(draft.intervention.target_modules).issubset(set(draft.intervention.allowed_paths)):
            raise ValueError("candidate proposal target modules must be editable paths")
        if set(draft.intervention.allowed_paths) & protected:
            raise ValueError("candidate proposal includes a protected path")
        if draft.intervention.time_budget > contract.resource_budget.max_wall_seconds:
            raise ValueError("candidate proposal exceeds the frozen time budget")
        if not draft.executor_proposal.edits:
            raise ValueError("candidate proposal must contain at least one edit")
        for edit in draft.executor_proposal.edits:
            _safe_relative(edit.path)
            if not edit.search or edit.search == edit.replace:
                raise ValueError("candidate proposal contains an empty edit")
            if edit.path not in draft.intervention.allowed_paths or edit.path in protected:
                raise ValueError("candidate patch is outside the reviewed path boundary")

    @staticmethod
    def _candidate_input(
        draft: CandidateProposalDraft,
        *,
        idea_node_id: str,
        contract: EvaluationContract,
        proposal_id: str,
        protected_paths: list[str] | None = None,
    ) -> CandidateLaunchInput:
        forbidden = sorted(set(draft.intervention.forbidden_paths) | set(protected_paths or []) | {metric.implementation_ref for metric in contract.metrics})
        intervention = InterventionContract(
            idea_id=idea_node_id,
            mechanism=draft.mechanism,
            hypothesis=draft.hypothesis,
            target_modules=draft.intervention.target_modules,
            allowed_paths=draft.intervention.allowed_paths,
            forbidden_paths=forbidden,
            allowed_parameters=draft.intervention.allowed_parameters,
            evaluation_invariants=draft.intervention.evaluation_invariants,
            time_budget=draft.intervention.time_budget,
        )
        seed = contract.seed_policy.exploration_seed if contract.seed_policy is not None else contract.seeds[0]
        return CandidateLaunchInput(
            intervention_contract=intervention,
            approved_proposal=draft.executor_proposal,
            comparison_seed=seed,
            idempotency_key=f"candidate:{proposal_id}",
        )

    def _validate_current_binding(self, run_dir: Path, proposal: CandidateProposal) -> None:
        session = self._sessions.load(run_dir, proposal.session_id)
        if session is None:
            raise FileNotFoundError("experiment session not found")
        if session.evaluation_contract_ref != proposal.evaluation_contract_ref or session.evaluation_contract_sha256 != proposal.evaluation_contract_sha256:
            raise ValueError("candidate proposal is stale because the evaluation contract changed")
        tree = self._trees.load(run_dir, session_id=proposal.session_id)
        if tree is None or tree.revision != proposal.idea_tree_revision:
            raise ValueError("candidate proposal is stale because the IdeaTree changed")
        node = tree.node(proposal.idea_node_id)
        if node.mechanism != proposal.candidate.intervention_contract.mechanism or node.hypothesis != proposal.candidate.intervention_contract.hypothesis:
            raise ValueError("candidate proposal IdeaTree binding changed")


def model_candidate_proposal_provider(
    context: dict[str, Any],
    *,
    api_key: str,
    provider_url: str,
    model: str,
    thinking_type: Literal["enabled", "disabled"] | None = None,
    reasoning_effort: Literal["high", "max"] | None = None,
) -> CandidateProposalDraft:
    """Call the configured experiment model with a bounded, read-only context."""

    schema = json.dumps(CandidateProposalDraft.model_json_schema(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    messages = [
        {
            "role": "system",
            "content": (
                "You are AutoAD's experiment proposal agent. Use only the supplied task, frozen evaluation contract, "
                "baseline evidence, adapter evidence, and bounded repository files. Propose exactly one minimal, "
                "falsifiable candidate. Do not change the evaluator, dataset, split, primary metric, or protected files. "
                "Return one JSON object matching CandidateProposalDraft exactly. Do not return commands, shell scripts, "
                "absolute paths, or prose outside the JSON object. The JSON Schema is: " + schema
            ),
        },
        {"role": "user", "content": json.dumps(context, ensure_ascii=False, sort_keys=True)},
    ]
    result = call_research_chat(
        api_key,
        provider_url,
        messages,
        model=model,
        timeout_s=90,
        priority="background",
        response_format_json=True,
        temperature=0,
        thinking_type=thinking_type,
        reasoning_effort=reasoning_effort,
    )
    if result.get("error"):
        raise ValueError("candidate proposal model is unavailable")
    raw = str(result.get("reply") or "")
    parsed = _parse_json_object(raw)
    if parsed is None:
        raise ValueError("candidate proposal model returned invalid structured output")
    return CandidateProposalDraft.model_validate(parsed)


def _cycle_decision(draft: CandidateProposalDraft) -> CycleDecision:
    return CycleDecision(
        observation="Baseline completed and the proposal is grounded in its persisted outcome.",
        comparison="The candidate remains unexecuted until explicit user approval.",
        hypothesis_verdict=draft.hypothesis,
        keep_why="Keep the baseline protocol and evaluate only this bounded intervention.",
        failure_why="A non-improving or invalid B_dev result must remain a recorded candidate outcome.",
        mechanism_interpretation=draft.mechanism,
        confidence=draft.executor_proposal.confidence,
        uncertainty="The intervention still requires B_dev evidence.",
        next_action="add_child",
        target_node_id="idea_000000",
        mutations=[IdeaTreeMutation(
            kind="add_child",
            parent_id="idea_000000",
            mechanism=draft.mechanism,
            hypothesis=draft.hypothesis,
            observable=draft.observable,
            research_axis=draft.research_axis,
            minimal_intervention=draft.minimal_intervention,
            falsification=draft.falsification,
            relationship_to_previous_ideas=draft.relationship_to_previous_ideas,
            grounding=draft.grounding,
            expected_cost=draft.expected_cost,
        )],
    )


def _idea_candidate(draft: CandidateProposalDraft) -> IdeaCandidate:
    return IdeaCandidate(
        mechanism=draft.mechanism,
        hypothesis=draft.hypothesis,
        observable=draft.observable,
        research_axis=draft.research_axis,
        minimal_intervention=draft.minimal_intervention,
        falsification=draft.falsification,
        expected_cost=draft.expected_cost,
        relationship_to_previous_ideas=draft.relationship_to_previous_ideas,
        grounding=draft.grounding,
    )


def _next_node_id(tree: IdeaTree) -> str:
    return f"idea_{max((int(node.node_id.removeprefix('idea_')) for node in tree.nodes), default=0) + 1:06d}"


def _safe_relative(value: str) -> None:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("candidate proposal paths must be repository-relative")


def _parse_json_object(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        stripped = fenced.group(1).strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        for index, character in enumerate(stripped):
            if character != "{":
                continue
            try:
                value, _ = decoder.raw_decode(stripped[index:])
            except json.JSONDecodeError:
                continue
            return value if isinstance(value, dict) else None
        return None
    return value if isinstance(value, dict) else None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
