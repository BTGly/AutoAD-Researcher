"""测试 DirectIdeaBackend。"""

import pytest

from autoad_researcher.ideation import DirectIdeaBackend
from autoad_researcher.schemas import (
    ClarifiedTask,
    IdeaContext,
    IdeaRouteDecision,
)


def _make_context(user_idea):
    return IdeaContext(
        run_id="run_demo",
        route=IdeaRouteDecision(
            mode="direct_user_idea",
            requested_mode="direct_user_idea",
            reason="user explicitly selected direct mode",
        ),
        clarified_task=ClarifiedTask(
            run_id="run_demo",
            status="ready",
            original_request="把模块 M 加入 PatchCore",
            user_idea=user_idea,
        ),
    )


class TestDirectIdeaBackend:
    def test_generates_one_candidate(self):
        ctx = _make_context("把模块 M 放入 layer2")
        result = DirectIdeaBackend().generate_ideas(context=ctx)

        assert result.run_id == "run_demo"
        assert result.mode == "direct_user_idea"
        assert len(result.candidates) == 1

        c = result.candidates[0]
        assert c.idea_id == "user_idea"
        assert c.description == "把模块 M 放入 layer2"
        assert c.estimated_cost == "unknown"
        assert c.confidence == 1.0
        assert c.evidence[0].artifact == "clarified_task.json"
        assert c.evidence[0].locator == "user_idea"
        assert result.recommended_candidate_ids == ["user_idea"]

    def test_deterministic(self):
        ctx = _make_context("把模块 M 放入 layer2")
        r1 = DirectIdeaBackend().generate_ideas(context=ctx)
        r2 = DirectIdeaBackend().generate_ideas(context=ctx)
        assert r1.model_dump() == r2.model_dump()

    def test_rejects_unsupported_mode(self):
        # multi_agent_exploration is valid without user_idea → backend rejects
        ctx = IdeaContext(
            run_id="run_demo",
            route=IdeaRouteDecision(mode="multi_agent_exploration", reason="test"),
            clarified_task=ClarifiedTask(
                run_id="run_demo", status="ready", original_request="x",
            ),
        )
        with pytest.raises(ValueError, match="only supports direct_user_idea"):
            DirectIdeaBackend().generate_ideas(context=ctx)

    def test_rejects_empty_idea(self):
        # Bypass IdeaContext validator to test backend's own check
        ctx = IdeaContext.model_construct(
            run_id="run_demo",
            route=IdeaRouteDecision(
                mode="direct_user_idea",
                requested_mode="direct_user_idea",
                reason="x",
            ),
            clarified_task=ClarifiedTask(
                run_id="run_demo", status="ready", original_request="x",
            ),
        )
        with pytest.raises(ValueError, match="direct user idea is missing"):
            DirectIdeaBackend().generate_ideas(context=ctx)

    def test_does_not_modify_context(self):
        ctx = _make_context("把模块 M 放入 layer2")
        original = ctx.model_dump(mode="json")
        DirectIdeaBackend().generate_ideas(context=ctx)
        assert ctx.model_dump(mode="json") == original

    def test_whitespace_normalized_title(self):
        ctx = _make_context("  把模块  M  放入 layer2  ")
        result = DirectIdeaBackend().generate_ideas(context=ctx)
        assert result.candidates[0].title == "把模块 M 放入 layer2"
