"""测试 idea schemas。"""

import pytest
from pydantic import ValidationError

from autoad_researcher.schemas import (
    ArtifactReference,
    IdeaCandidate,
    IdeaContext,
    IdeaGenerationResult,
    IdeaRouteDecision,
)


class TestIdeaRouteDecision:
    def test_minimal_valid(self):
        d = IdeaRouteDecision(mode="multi_agent_exploration", reason="no idea")
        assert d.requested_mode is None

    def test_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            IdeaRouteDecision(mode="multi_agent_exploration", reason="x", extra="no")  # type: ignore[call-arg]

    def test_direct_mode_requires_explicit_request(self):
        with pytest.raises(ValidationError, match="must be explicitly requested"):
            IdeaRouteDecision(mode="direct_user_idea", reason="x")

    def test_mode_must_match_requested(self):
        with pytest.raises(ValidationError, match="mode must match"):
            IdeaRouteDecision(
                mode="idea_decomposition",
                requested_mode="direct_user_idea",
                reason="x",
            )


class TestIdeaContext:
    def test_minimal_valid(self):
        ctx = IdeaContext(
            run_id="run_demo",
            route=IdeaRouteDecision(mode="multi_agent_exploration", reason="no idea"),
            clarified_task=__import__("autoad_researcher.schemas", fromlist=["ClarifiedTask"]).ClarifiedTask(
                run_id="run_demo", status="ready", original_request="x",
            ),
        )
        assert ctx.paper_summary is None


class TestIdeaCandidate:
    def _candidate(self, **kw):
        defaults = dict(
            idea_id="idea_001",
            title="test idea",
            description="a test candidate",
            insertion_point="backbone",
            rationale="should improve features",
            minimum_experiment="smoke test",
            estimated_cost="low",
            confidence=0.8,
            evidence=[ArtifactReference(artifact="clarified_task.json", locator="user_idea")],
        )
        defaults.update(kw)
        return IdeaCandidate(**defaults)

    def test_minimal_valid(self):
        c = self._candidate()
        assert c.idea_id == "idea_001"

    @pytest.mark.parametrize("bad_id", ["", "../bad", "foo/bar", ".hidden"])
    def test_invalid_idea_id_rejected(self, bad_id):
        with pytest.raises(ValidationError):
            self._candidate(idea_id=bad_id)

    def test_confidence_below_zero_rejected(self):
        with pytest.raises(ValidationError):
            self._candidate(confidence=-0.1)

    def test_confidence_above_one_rejected(self):
        with pytest.raises(ValidationError):
            self._candidate(confidence=1.1)

    def test_evidence_empty_rejected(self):
        with pytest.raises(ValidationError):
            self._candidate(evidence=[])


class TestIdeaGenerationResult:
    def _candidate(self, idea_id="idea_001"):
        return IdeaCandidate(
            idea_id=idea_id,
            title="test",
            description="desc",
            insertion_point="backbone",
            rationale="good",
            minimum_experiment="smoke",
            estimated_cost="low",
            confidence=0.8,
            evidence=[ArtifactReference(artifact="input_task.yaml", locator="request")],
        )

    def test_minimal_valid(self):
        r = IdeaGenerationResult(
            run_id="run_demo",
            mode="idea_decomposition",
            candidates=[self._candidate()],
        )
        assert len(r.candidates) == 1

    def test_candidates_empty_rejected(self):
        with pytest.raises(ValidationError):
            IdeaGenerationResult(run_id="run_demo", mode="idea_decomposition", candidates=[])

    def test_candidates_more_than_three_rejected(self):
        with pytest.raises(ValidationError):
            IdeaGenerationResult(
                run_id="run_demo", mode="idea_decomposition",
                candidates=[self._candidate(f"idea_{i:03d}") for i in range(4)],
            )

    def test_duplicate_idea_id_rejected(self):
        with pytest.raises(ValidationError, match="duplicate idea_id"):
            IdeaGenerationResult(
                run_id="run_demo", mode="idea_decomposition",
                candidates=[self._candidate("dup"), self._candidate("dup")],
            )

    def test_duplicate_recommended_rejected(self):
        with pytest.raises(ValidationError, match="duplicate recommended"):
            IdeaGenerationResult(
                run_id="run_demo", mode="idea_decomposition",
                candidates=[self._candidate("a"), self._candidate("b")],
                recommended_candidate_ids=["a", "a"],
            )

    def test_recommended_not_in_candidates_rejected(self):
        with pytest.raises(ValidationError, match="not found"):
            IdeaGenerationResult(
                run_id="run_demo", mode="idea_decomposition",
                candidates=[self._candidate("a")],
                recommended_candidate_ids=["missing"],
            )

    def test_direct_mode_requires_one_candidate(self):
        with pytest.raises(ValidationError, match="exactly one"):
            IdeaGenerationResult(
                run_id="run_demo", mode="direct_user_idea",
                candidates=[self._candidate("a"), self._candidate("b")],
            )
