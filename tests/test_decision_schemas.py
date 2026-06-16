"""测试 decision schemas。"""

import pytest
from pydantic import ValidationError

from autoad_researcher.schemas import (
    ArtifactReference,
    ClarifiedTask,
    ConfirmedDecision,
    DecisionCandidate,
    DecisionEvidence,
)

# Helper to make artifact references with correct artifact for source
def _repo_ref(locator="baseline_methods", source_id="r"):
    return ArtifactReference(artifact="repo_summary.json", locator=locator, source_id=source_id)

def _paper_ref(locator="compared_methods", source_id="p"):
    return ArtifactReference(artifact="paper_summary.json", locator=locator, source_id=source_id)


class TestDecisionEvidence:
    def test_minimum_one_reference(self):
        with pytest.raises(ValidationError):
            DecisionEvidence(
                source="repo_detected", rationale="x", references=[],
            )


class TestDecisionCandidate:
    def test_value_empty_rejected(self):
        with pytest.raises(ValidationError):
            DecisionCandidate(value="", evidence=[
                DecisionEvidence(source="repo_detected", rationale="x",
                                 references=[_repo_ref()]),
            ])

    def test_evidence_empty_rejected(self):
        with pytest.raises(ValidationError):
            DecisionCandidate(value="PatchCore", evidence=[])


class TestConfirmedDecision:
    def test_minimal_valid(self):
        cd = ConfirmedDecision(value="PatchCore", source="user_provided", evidence="input_task.yaml:baseline")
        assert cd.value == "PatchCore"

    def test_illegal_source_rejected(self):
        with pytest.raises(ValidationError):
            ConfirmedDecision(value="x", source="repo_detected", evidence="x")  # type: ignore[arg-type]


class TestClarifiedTaskBaselineProvenance:
    def _base(self, **kw):
        defaults = dict(
            run_id="run_demo", status="ready", original_request="x",
        )
        defaults.update(kw)
        return ClarifiedTask(**defaults)

    def test_baseline_nonempty_without_decision_rejected(self):
        with pytest.raises(ValidationError, match="confirmed baseline requires baseline_decision"):
            self._base(baseline="PatchCore")

    def test_baseline_empty_with_decision_rejected(self):
        with pytest.raises(ValidationError, match="baseline_decision requires baseline"):
            self._base(baseline_decision=ConfirmedDecision(value="PatchCore", source="user_provided", evidence="x"))

    def test_baseline_decision_value_mismatch_rejected(self):
        with pytest.raises(ValidationError, match="value mismatch"):
            self._base(
                baseline="PatchCore",
                baseline_decision=ConfirmedDecision(value="PaDiM", source="user_provided", evidence="y"),
            )

    def test_duplicate_candidate_values_rejected(self):
        with pytest.raises(ValidationError, match="duplicate baseline candidate"):
            self._base(
                baseline_candidates=[
                    DecisionCandidate(value="PatchCore", evidence=[
                        DecisionEvidence(source="repo_detected", rationale="x",
                                         references=[_repo_ref()]),
                    ]),
                    DecisionCandidate(value="patchcore", evidence=[
                        DecisionEvidence(source="paper_mentioned", rationale="y",
                                         references=[_paper_ref()]),
                    ]),
                ],
            )

    def test_user_confirmed_must_match_candidate(self):
        with pytest.raises(ValidationError, match="must match a candidate"):
            self._base(
                baseline="PatchCore",
                baseline_decision=ConfirmedDecision(value="PatchCore", source="user_confirmed", evidence="x"),
                baseline_candidates=[
                    DecisionCandidate(value="UniAD", evidence=[
                        DecisionEvidence(source="repo_detected", rationale="x",
                                         references=[_repo_ref()]),
                    ]),
                ],
            )

    def test_user_provided_without_candidates_ok(self):
        ct = self._base(
            baseline="UniAD",
            baseline_decision=ConfirmedDecision(value="UniAD", source="user_provided", evidence="x"),
        )
        assert ct.baseline_candidates == []

    def test_candidates_without_baseline_ok(self):
        ct = self._base(
            baseline_candidates=[
                DecisionCandidate(value="PatchCore", evidence=[
                    DecisionEvidence(source="repo_detected", rationale="x",
                                     references=[_repo_ref()]),
                ]),
            ],
        )
        assert ct.baseline is None
