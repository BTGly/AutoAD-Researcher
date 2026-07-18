"""Deterministic scientific decisions and recoverable Champion promotion.

This module owns no execution loop.  It consumes Finalizer-owned OutcomeCards,
creates immutable candidate snapshots, validates an independent approval record,
and applies one write-ahead promotion journal around an injected Git operation.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.experiment.scientific_assessment import EffectiveScientificAssessment


CHAMPION_DIR = "experiments/champions"

DecisionAction = Literal[
    "reject_result",
    "run_failed",
    "confirm_seed",
    "no_effect",
    "regression",
    "candidate",
    "no_promote",
    "ready_for_promotion",
]


class DecisionResult(BaseModel):
    """One deterministic gate result before Coordinator semantic interpretation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: DecisionAction
    reason: str = Field(min_length=1)
    phase: Literal["b_dev", "b_test"]
    attempt_id: str
    evidence_refs: list[str] = Field(default_factory=list)


class DecisionEngine:
    """Classify an effective assessment without LLM or free-text heuristics."""

    def decide(
        self,
        *,
        assessment: EffectiveScientificAssessment,
        phase: Literal["b_dev", "b_test"],
        noise_threshold: float | None,
        minimum_noise_multiplier: float = 1.0,
    ) -> DecisionResult:
        if minimum_noise_multiplier < 0:
            raise ValueError("minimum_noise_multiplier must be non-negative")
        refs = [
            value
            for value in (
                *assessment.evidence_refs,
            )
            if value
        ]
        if assessment.attempt_category == "protocol_violated" or not assessment.protocol_intact:
            return DecisionResult(
                action="reject_result",
                reason="evaluation protocol is not intact",
                phase=phase,
                attempt_id=assessment.attempt_id,
                evidence_refs=refs,
            )
        if assessment.attempt_category == "run_failed" or assessment.execution_status != "COMPLETED":
            return DecisionResult(
                action="run_failed",
                reason="attempt did not produce a scientifically evaluable completion",
                phase=phase,
                attempt_id=assessment.attempt_id,
                evidence_refs=refs,
            )
        if assessment.patch_applied is not True or assessment.smoke_passed is not True or not assessment.metrics_parsed:
            return DecisionResult(
                action="reject_result",
                reason="implementation evidence is incomplete",
                phase=phase,
                attempt_id=assessment.attempt_id,
                evidence_refs=refs,
            )
        if assessment.evaluation_status != "COMPARABLE" or assessment.scientific_effect is None:
            return DecisionResult(
                action="reject_result",
                reason="result is not comparable under the frozen contract",
                phase=phase,
                attempt_id=assessment.attempt_id,
                evidence_refs=refs,
            )
        if any(delta < 0 for delta in assessment.guardrail_deltas.values()):
            return DecisionResult(
                action="no_promote",
                reason="one or more guardrail metrics regressed",
                phase=phase,
                attempt_id=assessment.attempt_id,
                evidence_refs=refs,
            )
        if assessment.scientific_effect == "REGRESSION":
            return DecisionResult(
                action="regression",
                reason="primary metric regressed",
                phase=phase,
                attempt_id=assessment.attempt_id,
                evidence_refs=refs,
            )
        if assessment.scientific_effect in {"NO_EFFECT", "INCONCLUSIVE"}:
            return DecisionResult(
                action="no_effect" if assessment.scientific_effect == "NO_EFFECT" else "confirm_seed",
                reason="result does not establish a reliable improvement",
                phase=phase,
                attempt_id=assessment.attempt_id,
                evidence_refs=refs,
            )
        if assessment.primary_delta is None:
            return DecisionResult(
                action="confirm_seed",
                reason="primary delta is unavailable",
                phase=phase,
                attempt_id=assessment.attempt_id,
                evidence_refs=refs,
            )
        if noise_threshold is None:
            return DecisionResult(
                action="confirm_seed",
                reason="noise floor is not calibrated",
                phase=phase,
                attempt_id=assessment.attempt_id,
                evidence_refs=refs,
            )
        if assessment.primary_delta <= noise_threshold * minimum_noise_multiplier:
            return DecisionResult(
                action="confirm_seed",
                reason="improvement is within the configured noise boundary",
                phase=phase,
                attempt_id=assessment.attempt_id,
                evidence_refs=refs,
            )
        return DecisionResult(
            action="candidate" if phase == "b_dev" else "ready_for_promotion",
            reason="deterministic scientific gates passed",
            phase=phase,
            attempt_id=assessment.attempt_id,
            evidence_refs=refs,
        )


class CandidateSnapshot(BaseModel):
    """Immutable evidence bundle created when B_dev admits a candidate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    candidate_id: str = Field(pattern=r"^candidate_[0-9]{6}$")
    session_id: str = Field(min_length=1)
    evaluation_contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    idea_id: str = Field(pattern=r"^idea_[0-9]{6}$")
    attempt_id: str = Field(min_length=1)
    source_branch: str = Field(min_length=1)
    source_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    patch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    metrics_ref: str = Field(min_length=1)
    resource_ref: str = Field(min_length=1)
    b_dev_evidence_ref: str = Field(min_length=1)
    b_test_evidence_ref: str | None = None
    b_test_passed: bool = False
    guardrails_passed: bool = True
    attempt_category: Literal["scientifically_evaluable"] = "scientifically_evaluable"
    created_at: str

    @model_validator(mode="after")
    def _validate_refs(self):
        for field_name in (
            "metrics_ref",
            "resource_ref",
            "b_dev_evidence_ref",
            "b_test_evidence_ref",
        ):
            value = getattr(self, field_name)
            if value is None:
                continue
            path = PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts or value in {"", "."}:
                raise ValueError(f"{field_name} must be a run-relative reference")
        if self.b_test_passed and self.b_test_evidence_ref is None:
            raise ValueError("a passed B_test requires b_test_evidence_ref")
        return self


class PromotionApproval(BaseModel):
    """Independent approval decision referenced by the promotion command."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    approval_id: str = Field(pattern=r"^approval_[0-9]{6}$")
    candidate_id: str = Field(pattern=r"^candidate_[0-9]{6}$")
    mode: Literal["human", "automatic"]
    decision: Literal["approved", "rejected"]
    policy_snapshot_ref: str = Field(min_length=1)
    approved_by: str | None = None
    created_at: str


class PromotionJournal(BaseModel):
    """Single-coordinator write-ahead journal around Git merge and event commit."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    journal_id: str = Field(pattern=r"^promotion_[0-9]{6}$")
    candidate_id: str = Field(pattern=r"^candidate_[0-9]{6}$")
    candidate_snapshot_ref: str = Field(min_length=1)
    approval_id: str = Field(pattern=r"^approval_[0-9]{6}$")
    expected_trunk_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    resulting_trunk_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    status: Literal["prepared", "committed", "rolled_back", "failed"]
    failure_reason: str | None = None
    created_at: str
    updated_at: str


class ChampionEvent(BaseModel):
    """Append-only audit fact for promotion or rollback."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    event_id: str = Field(min_length=1)
    transaction_id: str = Field(pattern=r"^promotion_[0-9]{6}$")
    event_type: Literal["promoted_and_merged", "rolled_back"]
    evaluation_contract_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_id: str = Field(pattern=r"^candidate_[0-9]{6}$")
    previous_candidate_id: str | None = Field(default=None, pattern=r"^candidate_[0-9]{6}$")
    source_branch: str | None = None
    source_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    trunk_commit_before: str = Field(pattern=r"^[0-9a-f]{40}$")
    trunk_commit_after: str = Field(pattern=r"^[0-9a-f]{40}$")
    merge_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    revert_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    approval_ref: str = Field(min_length=1)
    reverts_event_id: str | None = None
    created_at: str


class ChampionPointer(BaseModel):
    """Current Champion for one frozen EvaluationContract."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate_id: str = Field(pattern=r"^candidate_[0-9]{6}$")
    event_id: str = Field(min_length=1)
    trunk_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    updated_at: str


class CandidateRegistry:
    """Immutable candidate and approval artifacts plus current Champion pointers."""

    def create_candidate(self, run_dir: Path, candidate: CandidateSnapshot) -> str:
        path = self._root(run_dir) / "candidates" / f"{candidate.candidate_id}.json"
        self._write_immutable(path, candidate.model_dump(mode="json"), "candidate ID already exists with different content")
        return str(path.relative_to(run_dir))

    def load_candidate(self, run_dir: Path, candidate_id: str) -> CandidateSnapshot:
        path = self._root(run_dir) / "candidates" / f"{candidate_id}.json"
        if not path.is_file():
            raise FileNotFoundError(f"candidate not found: {candidate_id}")
        return CandidateSnapshot.model_validate_json(path.read_text(encoding="utf-8"))

    def create_approval(self, run_dir: Path, approval: PromotionApproval) -> str:
        path = self._root(run_dir) / "approvals" / f"{approval.approval_id}.json"
        self._write_immutable(path, approval.model_dump(mode="json"), "approval ID already exists with different content")
        return str(path.relative_to(run_dir))

    def load_approval(self, run_dir: Path, approval_id: str) -> PromotionApproval:
        path = self._root(run_dir) / "approvals" / f"{approval_id}.json"
        if not path.is_file():
            raise FileNotFoundError(f"approval not found: {approval_id}")
        return PromotionApproval.model_validate_json(path.read_text(encoding="utf-8"))

    def current_by_contract(self, run_dir: Path) -> dict[str, ChampionPointer]:
        path = self._root(run_dir) / "current_by_contract.json"
        if not path.is_file():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("current_by_contract.json must contain an object")
        return {key: ChampionPointer.model_validate(value) for key, value in raw.items()}

    def current_summary_for_session(self, run_dir: Path, *, session_id: str) -> dict[str, dict] | None:
        candidates = {
            item.candidate_id: item
            for item in self.list_candidates(run_dir, session_id=session_id)
        }
        summary: dict[str, dict] = {}
        for contract_hash, pointer in self.current_by_contract(run_dir).items():
            candidate = candidates.get(pointer.candidate_id)
            if candidate is not None:
                summary[contract_hash] = {
                    "pointer": pointer.model_dump(mode="json"),
                    "candidate": candidate.model_dump(mode="json"),
                }
        return summary or None

    def list_candidates(self, run_dir: Path, *, session_id: str | None = None) -> list[CandidateSnapshot]:
        directory = self._root(run_dir) / "candidates"
        if not directory.is_dir():
            return []
        result = [
            CandidateSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(directory.glob("candidate_*.json"))
        ]
        return [item for item in result if session_id is None or item.session_id == session_id]

    def update_pointer(
        self,
        run_dir: Path,
        *,
        contract_hash: str,
        pointer: ChampionPointer | None,
    ) -> None:
        pointers = self.current_by_contract(run_dir)
        if pointer is None:
            pointers.pop(contract_hash, None)
        else:
            pointers[contract_hash] = pointer
        self._write_json_atomic(
            self._root(run_dir) / "current_by_contract.json",
            {key: value.model_dump(mode="json") for key, value in sorted(pointers.items())},
        )

    @staticmethod
    def _root(run_dir: Path) -> Path:
        return run_dir / CHAMPION_DIR

    @classmethod
    def _write_immutable(cls, cls_path: Path, payload: dict, conflict_message: str) -> None:
        if cls_path.is_file():
            if json.loads(cls_path.read_text(encoding="utf-8")) != payload:
                raise ValueError(conflict_message)
            return
        cls._write_json_atomic(cls_path, payload)

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict) -> None:
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


class PromotionService:
    """Apply or recover one promotion while the registry lock is held."""

    def __init__(self, *, registry: CandidateRegistry | None = None):
        self._registry = registry or CandidateRegistry()

    def promote_and_merge_candidate(
        self,
        run_dir: Path,
        *,
        journal_id: str,
        candidate_id: str,
        approval_id: str,
        expected_trunk_commit: str,
        current_trunk_commit: Callable[[], str],
        merge_candidate: Callable[[CandidateSnapshot], str],
    ) -> ChampionEvent:
        with self._lock(run_dir):
            existing = self._load_journal(run_dir, journal_id)
            if existing is not None:
                if existing.candidate_id != candidate_id or existing.approval_id != approval_id:
                    raise ValueError("journal ID already exists for a different promotion")
                committed = self._event_for_transaction(run_dir, journal_id)
                if existing.status == "committed" and committed is not None:
                    return committed
                if existing.status != "prepared":
                    raise ValueError(f"promotion journal is terminal: {existing.status}")
                return self._resume_prepared(
                    run_dir,
                    journal=existing,
                    current_trunk_commit=current_trunk_commit,
                    merge_candidate=merge_candidate,
                )

            candidate = self._registry.load_candidate(run_dir, candidate_id)
            approval = self._registry.load_approval(run_dir, approval_id)
            self._validate_promotion(candidate, approval)
            current = current_trunk_commit()
            if current != expected_trunk_commit:
                raise ValueError("trunk HEAD changed before promotion")
            now = _utc_now()
            journal = PromotionJournal(
                journal_id=journal_id,
                candidate_id=candidate_id,
                candidate_snapshot_ref=f"{CHAMPION_DIR}/candidates/{candidate_id}.json",
                approval_id=approval_id,
                expected_trunk_commit=expected_trunk_commit,
                status="prepared",
                created_at=now,
                updated_at=now,
            )
            self._save_journal(run_dir, journal)
            return self._resume_prepared(
                run_dir,
                journal=journal,
                current_trunk_commit=current_trunk_commit,
                merge_candidate=merge_candidate,
            )

    def rollback(
        self,
        run_dir: Path,
        *,
        promotion_event_id: str,
        current_trunk_commit: Callable[[], str],
        revert_merge: Callable[[str], str],
    ) -> ChampionEvent:
        with self._lock(run_dir):
            promotion = self._event_by_id(run_dir, promotion_event_id)
            if promotion is None or promotion.event_type != "promoted_and_merged" or promotion.merge_commit is None:
                raise ValueError("promotion event is missing or cannot be rolled back")
            existing = next(
                (
                    event
                    for event in self._events(run_dir)
                    if event.event_type == "rolled_back" and event.reverts_event_id == promotion_event_id
                ),
                None,
            )
            if existing is not None:
                return existing
            pointers = self._registry.current_by_contract(run_dir)
            pointer = pointers.get(promotion.evaluation_contract_hash)
            if pointer is None or pointer.event_id != promotion.event_id:
                raise ValueError("only the current Champion promotion may be rolled back")
            before = current_trunk_commit()
            if before != pointer.trunk_commit:
                raise ValueError("trunk HEAD changed before rollback")
            revert_commit = revert_merge(promotion.merge_commit)
            previous_pointer: ChampionPointer | None = None
            if promotion.previous_candidate_id is not None:
                previous_event = self._latest_promotion_for_candidate(
                    run_dir,
                    candidate_id=promotion.previous_candidate_id,
                    contract_hash=promotion.evaluation_contract_hash,
                )
                if previous_event is None:
                    raise ValueError("previous Champion event is missing")
                previous_pointer = ChampionPointer(
                    candidate_id=promotion.previous_candidate_id,
                    event_id=previous_event.event_id,
                    trunk_commit=revert_commit,
                    updated_at=_utc_now(),
                )
            event = ChampionEvent(
                event_id=f"rollback:{promotion.event_id}",
                transaction_id=promotion.transaction_id,
                event_type="rolled_back",
                evaluation_contract_hash=promotion.evaluation_contract_hash,
                candidate_id=promotion.candidate_id,
                previous_candidate_id=promotion.previous_candidate_id,
                trunk_commit_before=before,
                trunk_commit_after=revert_commit,
                revert_commit=revert_commit,
                approval_ref=promotion.approval_ref,
                reverts_event_id=promotion.event_id,
                created_at=_utc_now(),
            )
            self._append_event(run_dir, event)
            self._registry.update_pointer(
                run_dir,
                contract_hash=promotion.evaluation_contract_hash,
                pointer=previous_pointer,
            )
            journal = self._load_journal(run_dir, promotion.transaction_id)
            if journal is not None:
                journal.status = "rolled_back"
                journal.updated_at = _utc_now()
                self._save_journal(run_dir, journal)
            append_event(run_dir, "experiment.champion.rolled_back", event.model_dump(mode="json"))
            return event

    def _resume_prepared(
        self,
        run_dir: Path,
        *,
        journal: PromotionJournal,
        current_trunk_commit: Callable[[], str],
        merge_candidate: Callable[[CandidateSnapshot], str],
    ) -> ChampionEvent:
        candidate = self._registry.load_candidate(run_dir, journal.candidate_id)
        approval = self._registry.load_approval(run_dir, journal.approval_id)
        self._validate_promotion(candidate, approval)
        current = current_trunk_commit()
        if journal.resulting_trunk_commit is None:
            if current != journal.expected_trunk_commit:
                self._fail_journal(run_dir, journal, "trunk HEAD conflicts with PREPARED journal")
                raise ValueError("trunk HEAD conflicts with PREPARED promotion")
            resulting = merge_candidate(candidate)
            if len(resulting) != 40 or any(character not in "0123456789abcdef" for character in resulting):
                self._fail_journal(run_dir, journal, "merge callback returned an invalid commit SHA")
                raise ValueError("merge callback returned an invalid commit SHA")
            journal.resulting_trunk_commit = resulting
            journal.updated_at = _utc_now()
            self._save_journal(run_dir, journal)
            current = current_trunk_commit()
        if current != journal.resulting_trunk_commit:
            self._fail_journal(run_dir, journal, "trunk HEAD does not match recorded merge result")
            raise ValueError("trunk HEAD does not match recorded merge result")
        existing_event = self._event_for_transaction(run_dir, journal.journal_id)
        if existing_event is not None:
            self._commit_pointer_and_journal(run_dir, candidate, journal, existing_event)
            return existing_event
        pointers = self._registry.current_by_contract(run_dir)
        previous = pointers.get(candidate.evaluation_contract_hash)
        event = ChampionEvent(
            event_id=f"champion:{journal.journal_id}",
            transaction_id=journal.journal_id,
            event_type="promoted_and_merged",
            evaluation_contract_hash=candidate.evaluation_contract_hash,
            candidate_id=candidate.candidate_id,
            previous_candidate_id=None if previous is None else previous.candidate_id,
            source_branch=candidate.source_branch,
            source_commit=candidate.source_commit,
            trunk_commit_before=journal.expected_trunk_commit,
            trunk_commit_after=journal.resulting_trunk_commit,
            merge_commit=journal.resulting_trunk_commit,
            approval_ref=f"{CHAMPION_DIR}/approvals/{approval.approval_id}.json",
            created_at=_utc_now(),
        )
        self._append_event(run_dir, event)
        self._commit_pointer_and_journal(run_dir, candidate, journal, event)
        append_event(run_dir, "experiment.champion.promoted_and_merged", event.model_dump(mode="json"))
        return event

    def _commit_pointer_and_journal(
        self,
        run_dir: Path,
        candidate: CandidateSnapshot,
        journal: PromotionJournal,
        event: ChampionEvent,
    ) -> None:
        self._registry.update_pointer(
            run_dir,
            contract_hash=candidate.evaluation_contract_hash,
            pointer=ChampionPointer(
                candidate_id=candidate.candidate_id,
                event_id=event.event_id,
                trunk_commit=event.trunk_commit_after,
                updated_at=_utc_now(),
            ),
        )
        journal.status = "committed"
        journal.updated_at = _utc_now()
        self._save_journal(run_dir, journal)

    @staticmethod
    def _validate_promotion(candidate: CandidateSnapshot, approval: PromotionApproval) -> None:
        if approval.candidate_id != candidate.candidate_id:
            raise ValueError("approval does not reference candidate")
        if approval.decision != "approved":
            raise ValueError("candidate promotion is not approved")
        if not candidate.b_test_passed or candidate.b_test_evidence_ref is None:
            raise ValueError("candidate has not passed B_test")
        if not candidate.guardrails_passed:
            raise ValueError("candidate guardrails did not pass")
        if candidate.attempt_category != "scientifically_evaluable":
            raise ValueError("candidate is not scientifically evaluable")

    @staticmethod
    def _journal_path(run_dir: Path, journal_id: str) -> Path:
        return run_dir / CHAMPION_DIR / "transactions" / f"{journal_id}.json"

    def _load_journal(self, run_dir: Path, journal_id: str) -> PromotionJournal | None:
        path = self._journal_path(run_dir, journal_id)
        return PromotionJournal.model_validate_json(path.read_text(encoding="utf-8")) if path.is_file() else None

    def _save_journal(self, run_dir: Path, journal: PromotionJournal) -> None:
        CandidateRegistry._write_json_atomic(
            self._journal_path(run_dir, journal.journal_id),
            journal.model_dump(mode="json", exclude_none=True),
        )

    def _fail_journal(self, run_dir: Path, journal: PromotionJournal, reason: str) -> None:
        journal.status = "failed"
        journal.failure_reason = reason
        journal.updated_at = _utc_now()
        self._save_journal(run_dir, journal)

    @staticmethod
    def _event_path(run_dir: Path) -> Path:
        return run_dir / CHAMPION_DIR / "champion_events.jsonl"

    def _append_event(self, run_dir: Path, event: ChampionEvent) -> None:
        if self._event_by_id(run_dir, event.event_id) is not None:
            return
        path = self._event_path(run_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(event.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _events(self, run_dir: Path) -> list[ChampionEvent]:
        path = self._event_path(run_dir)
        if not path.is_file():
            return []
        return [
            ChampionEvent.model_validate_json(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _event_for_transaction(self, run_dir: Path, transaction_id: str) -> ChampionEvent | None:
        return next(
            (
                event
                for event in self._events(run_dir)
                if event.transaction_id == transaction_id and event.event_type == "promoted_and_merged"
            ),
            None,
        )

    def _event_by_id(self, run_dir: Path, event_id: str) -> ChampionEvent | None:
        return next((event for event in self._events(run_dir) if event.event_id == event_id), None)

    def _latest_promotion_for_candidate(
        self,
        run_dir: Path,
        *,
        candidate_id: str,
        contract_hash: str,
    ) -> ChampionEvent | None:
        matches = [
            event
            for event in self._events(run_dir)
            if event.event_type == "promoted_and_merged"
            and event.candidate_id == candidate_id
            and event.evaluation_contract_hash == contract_hash
        ]
        return matches[-1] if matches else None

    @staticmethod
    @contextmanager
    def _lock(run_dir: Path, timeout: float = 5.0):
        path = run_dir / CHAMPION_DIR / ".lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout
        fd: int | None = None
        token = uuid.uuid4().hex
        while time.monotonic() < deadline:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                os.write(fd, token.encode("utf-8"))
                os.fsync(fd)
                break
            except FileExistsError:
                time.sleep(0.02)
        if fd is None:
            raise TimeoutError("could not acquire Champion promotion lock")
        try:
            yield
        finally:
            os.close(fd)
            try:
                if path.read_text(encoding="utf-8") == token:
                    path.unlink()
            except (FileNotFoundError, OSError):
                pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
