"""Explicit scientific inputs and a deterministic derived assessment sidecar.

The Finalizer-owned OutcomeCard remains the execution/protocol authority.  This
module adds a rebuildable assessment from that card, Executor artifacts, the
frozen EvaluationContract, and caller-supplied comparison identities.  It never
infers seed, split, checkpoint, or baseline metrics from paths or prose.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.benchmarks.hashing import sha256_file
from autoad_researcher.experiment.evaluation_contract import EvaluationContract
from autoad_researcher.experiment.executor_agent import ExecutorSummary
from autoad_researcher.experiment.executor_handoff import (
    ExecutorAttemptHandoffService,
    ExecutorHandoffRequest,
    ExecutorHandoffResult,
)
from autoad_researcher.experiment.finalizer import OutcomeCard
from autoad_researcher.experiment.validity import (
    ComparisonIdentity,
    ImplementationEvidence,
    comparable,
    scientific_effect,
)


class ScientificEvaluationInputs(BaseModel):
    """Frozen caller-supplied facts that cannot be recovered safely after a run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    baseline_metrics: dict[str, float]
    candidate_identity: ComparisonIdentity
    baseline_identity: ComparisonIdentity


class ScientificAssessment(BaseModel):
    """Rebuildable interpretation of one immutable OutcomeCard."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    attempt_id: str
    outcome_card_ref: str
    outcome_card_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    inputs_ref: str
    inputs_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    patch_applied: bool
    smoke_passed: bool
    metrics_parsed: bool
    protocol_intact: bool
    evaluation_status: Literal["COMPARABLE", "NON_COMPARABLE"]
    scientific_effect: Literal["IMPROVEMENT", "NO_EFFECT", "REGRESSION", "INCONCLUSIVE"] | None = None
    primary_delta: float | None = None
    guardrail_deltas: dict[str, float] = Field(default_factory=dict)


class AssessmentReconciliation(BaseModel):
    """Immutable explanation of raw and derived assessment responsibilities."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    attempt_id: str
    outcome_card_ref: str
    outcome_card_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scientific_assessment_ref: str
    scientific_assessment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    comparison_status_at_finalization: Literal["COMPARABLE", "NON_COMPARABLE"]
    effective_evaluation_status: Literal["COMPARABLE", "NON_COMPARABLE"]
    execution_protocol_authority: Literal["outcome_card"] = "outcome_card"
    scientific_comparison_authority: Literal["scientific_assessment"] = "scientific_assessment"


class EffectiveScientificAssessment(BaseModel):
    """Only decision-consumable view: raw execution plus derived comparison facts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    attempt_id: str
    outcome_card_ref: str
    outcome_card_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scientific_assessment_ref: str
    scientific_assessment_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_status: Literal["COMPLETED", "CRASHED", "TIMEOUT", "CANCELLED", "LOST"]
    attempt_category: str
    protocol_intact: bool
    metrics_parsed: bool
    patch_applied: bool
    smoke_passed: bool
    evaluation_status: Literal["COMPARABLE", "NON_COMPARABLE"]
    scientific_effect: Literal["IMPROVEMENT", "NO_EFFECT", "REGRESSION", "INCONCLUSIVE"] | None = None
    primary_delta: float | None = None
    guardrail_deltas: dict[str, float] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)


class ScientificAssessmentInputsStore:
    def save(self, attempt_dir: Path, inputs: ScientificEvaluationInputs) -> str:
        path = attempt_dir / "scientific_evaluation_inputs.json"
        payload = inputs.model_dump(mode="json")
        if path.is_file():
            existing = ScientificEvaluationInputs.model_validate_json(path.read_text(encoding="utf-8"))
            if existing != inputs:
                raise ValueError("scientific evaluation inputs already exist with different content")
            return path.name
        _write_json_atomic(path, payload)
        return path.name

    def load(self, attempt_dir: Path) -> ScientificEvaluationInputs:
        path = attempt_dir / "scientific_evaluation_inputs.json"
        if not path.is_file():
            raise FileNotFoundError("scientific_evaluation_inputs.json is missing")
        return ScientificEvaluationInputs.model_validate_json(path.read_text(encoding="utf-8"))


class ScientificExecutorHandoffService:
    """Add explicit assessment inputs around the existing Executor handoff only."""

    def __init__(self, *, handoff_service: ExecutorAttemptHandoffService | None = None):
        self._handoff = handoff_service or ExecutorAttemptHandoffService()
        self._inputs = ScientificAssessmentInputsStore()

    def handoff(
        self,
        run_dir: Path,
        *,
        request: ExecutorHandoffRequest,
        scientific_inputs: ScientificEvaluationInputs,
        proposal_provider: Callable,
    ) -> ExecutorHandoffResult:
        result = self._handoff.handoff(
            run_dir,
            request=request,
            proposal_provider=proposal_provider,
        )
        if result.status == "queued" and result.attempt is not None:
            attempt_id = result.attempt.get("attempt_id")
            if not isinstance(attempt_id, str):
                raise ValueError("Executor handoff returned an invalid attempt ID")
            self._inputs.save(run_dir / "attempts" / attempt_id, scientific_inputs)
        return result


class ScientificAssessmentService:
    """Build an assessment from explicit artifacts and return an enriched card view."""

    def __init__(self, *, inputs_store: ScientificAssessmentInputsStore | None = None):
        self._inputs = inputs_store or ScientificAssessmentInputsStore()

    def assess(self, run_dir: Path, *, attempt_id: str) -> ScientificAssessment:
        attempt_dir = run_dir / "attempts" / attempt_id
        card_path = attempt_dir / "outcome_card.json"
        if not card_path.is_file():
            raise FileNotFoundError("OutcomeCard is missing")
        card = OutcomeCard.model_validate_json(card_path.read_text(encoding="utf-8"))
        if card.attempt_id != attempt_id:
            raise ValueError("OutcomeCard attempt ID does not match its directory")
        inputs_path = attempt_dir / "scientific_evaluation_inputs.json"
        inputs = self._inputs.load(attempt_dir)
        implementation = self._implementation_evidence(run_dir, attempt_dir, card)
        evaluation_status = comparable(inputs.candidate_identity, inputs.baseline_identity)
        contract = self._load_contract(run_dir, card)
        effect, delta, guardrails = scientific_effect(
            candidate_metrics=card.metrics,
            baseline_metrics=inputs.baseline_metrics,
            contract=contract,
            evaluation_status=evaluation_status,
            implementation_evidence=implementation,
            metrics_parsed=card.metrics_parsed,
            protocol_intact=card.protocol_intact,
        )
        assessment = ScientificAssessment(
            attempt_id=attempt_id,
            outcome_card_ref=str(card_path.relative_to(run_dir)),
            outcome_card_sha256=sha256_file(card_path),
            inputs_ref=str(inputs_path.relative_to(run_dir)),
            inputs_sha256=sha256_file(inputs_path),
            patch_applied=implementation.patch_applied,
            smoke_passed=implementation.smoke_passed,
            metrics_parsed=card.metrics_parsed,
            protocol_intact=card.protocol_intact,
            evaluation_status=evaluation_status,
            scientific_effect=effect,
            primary_delta=delta,
            guardrail_deltas=guardrails,
        )
        assessment_path = attempt_dir / "scientific_assessment.json"
        if assessment_path.is_file():
            existing = ScientificAssessment.model_validate_json(assessment_path.read_text(encoding="utf-8"))
            if existing != assessment:
                raise ValueError("scientific assessment changed for immutable inputs")
            self.reconcile(run_dir, attempt_id=attempt_id, card=card, assessment=existing)
            return existing
        _write_json_atomic(assessment_path, assessment.model_dump(mode="json", exclude_none=True))
        self.reconcile(run_dir, attempt_id=attempt_id, card=card, assessment=assessment)
        return assessment

    def reconcile(
        self,
        run_dir: Path,
        *,
        attempt_id: str,
        card: OutcomeCard | None = None,
        assessment: ScientificAssessment | None = None,
    ) -> AssessmentReconciliation:
        attempt_dir = run_dir / "attempts" / attempt_id
        card_path = attempt_dir / "outcome_card.json"
        assessment_path = attempt_dir / "scientific_assessment.json"
        raw = card or OutcomeCard.model_validate_json(card_path.read_text(encoding="utf-8"))
        derived = assessment or ScientificAssessment.model_validate_json(assessment_path.read_text(encoding="utf-8"))
        reconciliation = AssessmentReconciliation(
            attempt_id=attempt_id,
            outcome_card_ref=str(card_path.relative_to(run_dir)),
            outcome_card_sha256=sha256_file(card_path),
            scientific_assessment_ref=str(assessment_path.relative_to(run_dir)),
            scientific_assessment_sha256=sha256_file(assessment_path),
            comparison_status_at_finalization=raw.evaluation_status,
            effective_evaluation_status=derived.evaluation_status,
        )
        path = attempt_dir / "assessment_reconciliation.json"
        if path.is_file():
            existing = AssessmentReconciliation.model_validate_json(path.read_text(encoding="utf-8"))
            if existing != reconciliation:
                raise ValueError("assessment reconciliation changed for immutable inputs")
            return existing
        _write_json_atomic(path, reconciliation.model_dump(mode="json"))
        return reconciliation

    def effective_assessment(self, run_dir: Path, *, attempt_id: str) -> EffectiveScientificAssessment:
        attempt_dir = run_dir / "attempts" / attempt_id
        card_path = attempt_dir / "outcome_card.json"
        card = OutcomeCard.model_validate_json(card_path.read_text(encoding="utf-8"))
        assessment = self.assess(run_dir, attempt_id=attempt_id)
        reconciliation = self.reconcile(run_dir, attempt_id=attempt_id, card=card, assessment=assessment)
        refs = [
            value for value in (
                card.execution_result_ref,
                card.evaluation_contract_ref,
                card.protected_artifact_validation_ref,
                reconciliation.scientific_assessment_ref,
                str((attempt_dir / "assessment_reconciliation.json").relative_to(run_dir)),
            ) if value
        ]
        return EffectiveScientificAssessment(
            attempt_id=attempt_id,
            outcome_card_ref=str(card_path.relative_to(run_dir)),
            outcome_card_sha256=sha256_file(card_path),
            scientific_assessment_ref=reconciliation.scientific_assessment_ref,
            scientific_assessment_sha256=reconciliation.scientific_assessment_sha256,
            execution_status=card.execution_status,
            attempt_category=card.attempt_category,
            protocol_intact=card.protocol_intact,
            metrics_parsed=card.metrics_parsed,
            patch_applied=assessment.patch_applied,
            smoke_passed=assessment.smoke_passed,
            evaluation_status=assessment.evaluation_status,
            scientific_effect=assessment.scientific_effect,
            primary_delta=assessment.primary_delta,
            guardrail_deltas=assessment.guardrail_deltas,
            evidence_refs=refs,
        )

    def assessed_card(self, run_dir: Path, *, attempt_id: str) -> OutcomeCard:
        attempt_dir = run_dir / "attempts" / attempt_id
        card = OutcomeCard.model_validate_json((attempt_dir / "outcome_card.json").read_text(encoding="utf-8"))
        assessment = self.assess(run_dir, attempt_id=attempt_id)
        return card.model_copy(
            update={
                "patch_applied": assessment.patch_applied,
                "smoke_passed": assessment.smoke_passed,
                "metrics_parsed": assessment.metrics_parsed,
                "protocol_intact": assessment.protocol_intact,
                "evaluation_status": assessment.evaluation_status,
                "scientific_effect": assessment.scientific_effect,
                "primary_delta": assessment.primary_delta,
                "guardrail_deltas": assessment.guardrail_deltas,
            }
        )

    @staticmethod
    def _implementation_evidence(run_dir: Path, attempt_dir: Path, card: OutcomeCard) -> ImplementationEvidence:
        """Use the admitted B_dev evidence for a linked B_test confirmation.

        A confirmation evaluates an already-committed candidate, so it has no
        second Executor edit.  Its immutable link names the original Attempt;
        all other paths remain local to that source Attempt.
        """
        link_path = attempt_dir / "candidate_confirmation.json"
        if link_path.is_file():
            try:
                candidate_attempt_id = json.loads(link_path.read_text(encoding="utf-8"))["candidate_attempt_id"]
                if not isinstance(candidate_attempt_id, str) or not candidate_attempt_id.startswith("attempt_"):
                    raise ValueError("invalid candidate attempt ID")
                source = run_dir / "attempts" / candidate_attempt_id
                if source.is_dir():
                    attempt_dir = source
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                return ImplementationEvidence(patch_applied=False, smoke_passed=False)
        summary_path = attempt_dir / "executor_summary.json"
        patch_path = attempt_dir / "patch.diff"
        if not summary_path.is_file():
            return ImplementationEvidence(patch_applied=False, smoke_passed=False)
        summary = ExecutorSummary.model_validate_json(summary_path.read_text(encoding="utf-8"))
        patch_applied = (
            summary.status == "completed"
            and bool(summary.changed_files)
            and patch_path.is_file()
            and bool(patch_path.read_text(encoding="utf-8").strip())
        )
        smoke_passed = (
            card.execution_status == "COMPLETED"
            and card.attempt_category == "scientifically_evaluable"
            and card.metrics_parsed
        )
        return ImplementationEvidence(patch_applied=patch_applied, smoke_passed=smoke_passed)

    @staticmethod
    def _load_contract(run_dir: Path, card: OutcomeCard) -> EvaluationContract | None:
        if card.evaluation_contract_ref is None:
            return None
        ref = PurePosixPath(card.evaluation_contract_ref)
        if ref.is_absolute() or ".." in ref.parts:
            return None
        path = run_dir.joinpath(*ref.parts)
        if not path.is_file():
            return None
        try:
            return EvaluationContract.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            return None


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
