from datetime import datetime, timezone

import pytest

from autoad_researcher.experiment.finalizer import OutcomeCard
from autoad_researcher.experiment.promotion import (
    CandidateRegistry,
    CandidateSnapshot,
    DecisionEngine,
    PromotionApproval,
    PromotionService,
)
from autoad_researcher.experiment.scientific_assessment import EffectiveScientificAssessment


def _card(**updates) -> OutcomeCard:
    values = {
        "attempt_id": "attempt_000001",
        "runtime_status": "COMPLETED",
        "attempt_category": "scientifically_evaluable",
        "execution_result_ref": "attempts/attempt_000001/execution_result.json",
        "metrics": {"score": 0.9},
        "protocol_valid": True,
        "protocol_errors": [],
        "execution_status": "COMPLETED",
        "patch_applied": True,
        "smoke_passed": True,
        "metrics_parsed": True,
        "protocol_intact": True,
        "evaluation_status": "COMPARABLE",
        "scientific_effect": "IMPROVEMENT",
        "primary_delta": 0.1,
        "guardrail_deltas": {"latency": 0.01},
    }
    values.update(updates)
    return OutcomeCard.model_validate(values)


def _assessment(**updates) -> EffectiveScientificAssessment:
    reproducibility_status = updates.pop("reproducibility_status", "not_checked")
    card = _card(**updates)
    return EffectiveScientificAssessment(
        attempt_id=card.attempt_id,
        outcome_card_ref="attempts/attempt_000001/outcome_card.json",
        outcome_card_sha256="a" * 64,
        scientific_assessment_ref="attempts/attempt_000001/scientific_assessment.json",
        scientific_assessment_sha256="b" * 64,
        execution_status=card.execution_status,
        attempt_category=card.attempt_category,
        protocol_intact=card.protocol_intact,
        metrics_parsed=card.metrics_parsed,
        patch_applied=card.patch_applied,
        smoke_passed=card.smoke_passed,
        evaluation_status=card.evaluation_status,
        scientific_effect=card.scientific_effect,
        primary_delta=card.primary_delta,
        guardrail_deltas=card.guardrail_deltas,
        reproducibility_status=reproducibility_status,
        evidence_refs=[card.execution_result_ref],
    )


def _candidate(**updates) -> CandidateSnapshot:
    values = {
        "candidate_id": "candidate_000001",
        "session_id": "session_000001",
        "evaluation_contract_hash": "d" * 64,
        "idea_id": "idea_000001",
        "attempt_id": "attempt_000001",
        "source_branch": "executor/attempt_000001",
        "source_commit": "c" * 40,
        "patch_sha256": "e" * 64,
        "metrics_ref": "attempts/attempt_000001/metrics.json",
        "resource_ref": "attempts/attempt_000001/execution_result.json",
        "b_dev_evidence_ref": "attempts/attempt_000001/outcome_card.json",
        "b_test_evidence_ref": "attempts/attempt_000002/outcome_card.json",
        "b_test_passed": True,
        "guardrails_passed": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    values.update(updates)
    return CandidateSnapshot.model_validate(values)


def _approval(**updates) -> PromotionApproval:
    values = {
        "approval_id": "approval_000001",
        "candidate_id": "candidate_000001",
        "mode": "human",
        "decision": "approved",
        "policy_snapshot_ref": "experiments/policy/promotion.json",
        "approved_by": "user",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    values.update(updates)
    return PromotionApproval.model_validate(values)


def test_decision_engine_applies_protocol_noise_and_guardrail_gates():
    engine = DecisionEngine()
    assert engine.decide(assessment=_assessment(protocol_intact=False), phase="b_dev", noise_threshold=0.01).action == "reject_result"
    assert engine.decide(assessment=_assessment(primary_delta=0.005), phase="b_dev", noise_threshold=0.01).action == "confirm_seed"
    assert engine.decide(assessment=_assessment(guardrail_deltas={"latency": -0.01}), phase="b_dev", noise_threshold=0.01).action == "no_promote"
    assert engine.decide(assessment=_assessment(), phase="b_dev", noise_threshold=0.01).action == "candidate"
    assert engine.decide(assessment=_assessment(), phase="b_test", noise_threshold=0.01).action == "ready_for_promotion"


def test_decision_engine_requires_seed_or_noise_for_unstable_candidate():
    result = DecisionEngine().decide(
        assessment=_assessment(reproducibility_status="not_reproducible"),
        phase="b_dev",
        noise_threshold=0.01,
    )
    assert result.action == "confirm_seed"
    assert "fix the seed" in result.reason


def test_candidate_and_approval_are_immutable_by_identifier(tmp_path):
    registry = CandidateRegistry()
    candidate = _candidate()
    ref = registry.create_candidate(tmp_path, candidate)
    assert registry.load_candidate(tmp_path, candidate.candidate_id) == candidate
    assert ref.endswith("candidate_000001.json")
    registry.create_candidate(tmp_path, candidate)
    with pytest.raises(ValueError):
        registry.create_candidate(tmp_path, candidate.model_copy(update={"attempt_id": "attempt_000009"}))
    approval = _approval()
    registry.create_approval(tmp_path, approval)
    with pytest.raises(ValueError):
        registry.create_approval(tmp_path, approval.model_copy(update={"decision": "rejected"}))


def test_promotion_requires_b_test_and_valid_approval(tmp_path):
    registry = CandidateRegistry()
    registry.create_candidate(tmp_path, _candidate(b_test_passed=False, b_test_evidence_ref=None))
    registry.create_approval(tmp_path, _approval())
    service = PromotionService(registry=registry)
    with pytest.raises(ValueError, match="B_test"):
        service.promote_and_merge_candidate(
            tmp_path,
            journal_id="promotion_000001",
            candidate_id="candidate_000001",
            approval_id="approval_000001",
            expected_trunk_commit="a" * 40,
            current_trunk_commit=lambda: "a" * 40,
            merge_candidate=lambda _: "b" * 40,
        )


def test_promotion_replay_and_rollback_are_idempotent(tmp_path):
    registry = CandidateRegistry()
    registry.create_candidate(tmp_path, _candidate())
    registry.create_approval(tmp_path, _approval())
    state = {"head": "a" * 40, "merges": 0, "reverts": 0}

    def merge(_candidate):
        state["merges"] += 1
        state["head"] = "b" * 40
        return state["head"]

    service = PromotionService(registry=registry)
    event = service.promote_and_merge_candidate(
        tmp_path,
        journal_id="promotion_000001",
        candidate_id="candidate_000001",
        approval_id="approval_000001",
        expected_trunk_commit="a" * 40,
        current_trunk_commit=lambda: state["head"],
        merge_candidate=merge,
    )
    replay = service.promote_and_merge_candidate(
        tmp_path,
        journal_id="promotion_000001",
        candidate_id="candidate_000001",
        approval_id="approval_000001",
        expected_trunk_commit="a" * 40,
        current_trunk_commit=lambda: state["head"],
        merge_candidate=merge,
    )
    assert replay == event
    assert state["merges"] == 1
    assert registry.current_by_contract(tmp_path)["d" * 64].candidate_id == "candidate_000001"

    def revert(merge_commit):
        assert merge_commit == "b" * 40
        state["reverts"] += 1
        state["head"] = "f" * 40
        return state["head"]

    rollback = service.rollback(
        tmp_path,
        promotion_event_id=event.event_id,
        current_trunk_commit=lambda: state["head"],
        revert_merge=revert,
    )
    assert service.rollback(
        tmp_path,
        promotion_event_id=event.event_id,
        current_trunk_commit=lambda: state["head"],
        revert_merge=revert,
    ) == rollback
    assert state["reverts"] == 1
    assert registry.current_by_contract(tmp_path) == {}


def test_prepared_journal_recovers_after_event_before_pointer(tmp_path):
    class FailingRegistry(CandidateRegistry):
        def __init__(self):
            self.failed = False

        def update_pointer(self, run_dir, *, contract_hash, pointer):
            if not self.failed:
                self.failed = True
                raise RuntimeError("simulated pointer failure")
            return super().update_pointer(run_dir, contract_hash=contract_hash, pointer=pointer)

    registry = FailingRegistry()
    registry.create_candidate(tmp_path, _candidate())
    registry.create_approval(tmp_path, _approval())
    state = {"head": "a" * 40, "merges": 0}

    def merge(_candidate):
        state["merges"] += 1
        state["head"] = "b" * 40
        return state["head"]

    with pytest.raises(RuntimeError, match="pointer failure"):
        PromotionService(registry=registry).promote_and_merge_candidate(
            tmp_path,
            journal_id="promotion_000001",
            candidate_id="candidate_000001",
            approval_id="approval_000001",
            expected_trunk_commit="a" * 40,
            current_trunk_commit=lambda: state["head"],
            merge_candidate=merge,
        )
    event = PromotionService().promote_and_merge_candidate(
        tmp_path,
        journal_id="promotion_000001",
        candidate_id="candidate_000001",
        approval_id="approval_000001",
        expected_trunk_commit="a" * 40,
        current_trunk_commit=lambda: state["head"],
        merge_candidate=merge,
    )
    assert event.event_type == "promoted_and_merged"
    assert state["merges"] == 1
    assert CandidateRegistry().current_by_contract(tmp_path)["d" * 64].candidate_id == "candidate_000001"
