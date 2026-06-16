"""测试 IdeaSourceRouter。"""

import pytest

from autoad_researcher.core import (
    ArtifactStore,
    EventStore,
    IdeaSourceRouter,
)
from autoad_researcher.schemas import (
    ClarifiedTask,
    IdeaContext,
    PaperSummary,
    RepositorySummary,
    MissingInformation,
)


def _write_clarified(store, run_id="run_demo", **kw):
    defaults = dict(
        run_id=run_id, status="ready", original_request="迁移方法",
        source_ids=["paper_main", "baseline_repo"],
        user_idea="把多尺度模块加入 PatchCore",
        target_domain="visual",
    )
    defaults.update(kw)

    # Auto-provision baseline_decision when baseline is set
    if defaults.get("baseline") and not defaults.get("baseline_decision"):
        from autoad_researcher.schemas import ConfirmedDecision
        defaults.setdefault("baseline_decision", ConfirmedDecision(
            value=defaults["baseline"], source="user_provided", evidence="test",
        ))

    if defaults["status"] == "needs_blocking_input":
        defaults.setdefault("missing_information", [
            MissingInformation(
                item_id="m1", category="domain", field="target_domain",
                reason="x", blocking=True,
            )
        ])
    elif defaults["status"] == "has_nonblocking_questions":
        from autoad_researcher.schemas import ClarificationQuestion
        defaults.setdefault("missing_information", [
            MissingInformation(
                item_id="m1", category="baseline", field="baseline", reason="x",
            )
        ])
        defaults.setdefault("questions", [
            ClarificationQuestion(
                question_id="q1", missing_item_id="m1",
                question="what baseline?", why_needed="needed",
                answer_type="free_text",
            )
        ])

    store.write_json(run_id, "clarified_task.json", ClarifiedTask(**defaults))


def _write_paper(store, run_id="run_demo"):
    store.write_json(run_id, "paper_summary.json", PaperSummary(
        run_id=run_id, source_id="paper_main", research_problem="x", core_idea="y",
    ))


def _write_repo(store, run_id="run_demo"):
    store.write_json(run_id, "repo_summary.json", RepositorySummary(
        run_id=run_id, source_id="baseline_repo",
    ))


class TestIdeaSourceRouter:
    def test_no_idea_defaults_to_exploration(self, tmp_path):
        store = ArtifactStore(runs_root=tmp_path, enable_events=False)
        _write_clarified(store, user_idea=None)

        result = IdeaSourceRouter(runs_root=tmp_path).run("run_demo")
        assert result.status == "success"
        assert result.metadata["mode"] == "multi_agent_exploration"

    def test_with_idea_defaults_to_decomposition(self, tmp_path):
        store = ArtifactStore(runs_root=tmp_path, enable_events=False)
        _write_clarified(store)

        result = IdeaSourceRouter(runs_root=tmp_path).run("run_demo")
        assert result.metadata["mode"] == "idea_decomposition"

    def test_explicit_direct_mode(self, tmp_path):
        store = ArtifactStore(runs_root=tmp_path, enable_events=False)
        _write_clarified(store)

        result = IdeaSourceRouter(runs_root=tmp_path).run(
            "run_demo", requested_mode="direct_user_idea"
        )
        assert result.metadata["mode"] == "direct_user_idea"

    def test_direct_mode_requires_user_idea(self, tmp_path):
        store = ArtifactStore(runs_root=tmp_path, enable_events=False)
        _write_clarified(store, user_idea=None)

        with pytest.raises(ValueError, match="requires user_idea"):
            IdeaSourceRouter(runs_root=tmp_path).run(
                "run_demo", requested_mode="direct_user_idea"
            )

    def test_decomposition_requires_user_idea(self, tmp_path):
        store = ArtifactStore(runs_root=tmp_path, enable_events=False)
        _write_clarified(store, user_idea=None)

        with pytest.raises(ValueError, match="requires user_idea"):
            IdeaSourceRouter(runs_root=tmp_path).run(
                "run_demo", requested_mode="idea_decomposition"
            )

    def test_explicit_multi_agent_even_with_idea(self, tmp_path):
        store = ArtifactStore(runs_root=tmp_path, enable_events=False)
        _write_clarified(store)

        result = IdeaSourceRouter(runs_root=tmp_path).run(
            "run_demo", requested_mode="multi_agent_exploration"
        )
        assert result.metadata["mode"] == "multi_agent_exploration"

    def test_blocking_clarification_rejected(self, tmp_path):
        store = ArtifactStore(runs_root=tmp_path, enable_events=False)
        _write_clarified(store, status="needs_blocking_input")

        with pytest.raises(ValueError, match="blocking"):
            IdeaSourceRouter(runs_root=tmp_path).run("run_demo")

    def test_nonblocking_clarification_allowed(self, tmp_path):
        store = ArtifactStore(runs_root=tmp_path, enable_events=False)
        _write_clarified(store, status="has_nonblocking_questions")

        result = IdeaSourceRouter(runs_root=tmp_path).run("run_demo")
        assert result.status == "success"

    def test_clarified_run_id_mismatch(self, tmp_path):
        store = ArtifactStore(runs_root=tmp_path, enable_events=False)
        # Write a clarified task with mismatched internal run_id
        store.write_json("run_demo", "clarified_task.json",
                         ClarifiedTask(
                             run_id="other", status="ready",
                             original_request="x",
                         ))

        with pytest.raises(ValueError, match="run_id mismatch"):
            IdeaSourceRouter(runs_root=tmp_path).run("run_demo")

    def test_paper_run_id_mismatch(self, tmp_path):
        store = ArtifactStore(runs_root=tmp_path, enable_events=False)
        _write_clarified(store)
        store.write_json("run_demo", "paper_summary.json", PaperSummary(
            run_id="other", source_id="paper_main", research_problem="x", core_idea="y",
        ))

        with pytest.raises(ValueError, match="run_id mismatch"):
            IdeaSourceRouter(runs_root=tmp_path).run("run_demo")

    def test_paper_source_id_not_in_clarified(self, tmp_path):
        store = ArtifactStore(runs_root=tmp_path, enable_events=False)
        _write_clarified(store, source_ids=["baseline_repo"])
        _write_paper(store)

        with pytest.raises(ValueError, match="not in clarified"):
            IdeaSourceRouter(runs_root=tmp_path).run("run_demo")

    def test_empty_idea_defaults_to_exploration(self, tmp_path):
        store = ArtifactStore(runs_root=tmp_path, enable_events=False)
        _write_clarified(store, user_idea="")

        result = IdeaSourceRouter(runs_root=tmp_path).run("run_demo")
        assert result.metadata["mode"] == "multi_agent_exploration"

    def test_whitespace_idea_defaults_to_exploration(self, tmp_path):
        store = ArtifactStore(runs_root=tmp_path, enable_events=False)
        _write_clarified(store, user_idea="   ")

        result = IdeaSourceRouter(runs_root=tmp_path).run("run_demo")
        assert result.metadata["mode"] == "multi_agent_exploration"

    def test_empty_idea_direct_mode_rejected(self, tmp_path):
        store = ArtifactStore(runs_root=tmp_path, enable_events=False)
        _write_clarified(store, user_idea="")

        with pytest.raises(ValueError, match="requires user_idea"):
            IdeaSourceRouter(runs_root=tmp_path).run("run_demo", requested_mode="direct_user_idea")

    def test_repo_run_id_mismatch(self, tmp_path):
        store = ArtifactStore(runs_root=tmp_path, enable_events=False)
        _write_clarified(store)
        store.write_json("run_demo", "repo_summary.json", RepositorySummary(
            run_id="other", source_id="baseline_repo",
        ))

        with pytest.raises(ValueError, match="run_id mismatch"):
            IdeaSourceRouter(runs_root=tmp_path).run("run_demo")

    def test_repo_source_id_not_in_clarified(self, tmp_path):
        store = ArtifactStore(runs_root=tmp_path, enable_events=False)
        _write_clarified(store, source_ids=["paper_main"])
        _write_repo(store)

        with pytest.raises(ValueError, match="not in clarified"):
            IdeaSourceRouter(runs_root=tmp_path).run("run_demo")

    def test_snapshot_consistency(self, tmp_path):
        store = ArtifactStore(runs_root=tmp_path, enable_events=False)
        _write_clarified(store)
        _write_paper(store)

        IdeaSourceRouter(runs_root=tmp_path).run("run_demo")

        ctx = store.read_model("run_demo", "idea_context.json", IdeaContext)
        assert ctx.run_id == "run_demo"
        assert ctx.route.mode == "idea_decomposition"
        assert ctx.clarified_task.user_idea == "把多尺度模块加入 PatchCore"
        assert ctx.paper_summary is not None

    def test_exact_event_order_full_input(self, tmp_path):
        store = ArtifactStore(runs_root=tmp_path, enable_events=False)
        _write_clarified(store)
        _write_paper(store)
        _write_repo(store)

        IdeaSourceRouter(runs_root=tmp_path).run("run_demo")

        events = EventStore(runs_root=tmp_path).read_events("run_demo")
        assert [
            (e.event_type, e.payload.get("artifact"))
            for e in events[-4:]
        ] == [
            ("artifact_read", "clarified_task.json"),
            ("artifact_read", "paper_summary.json"),
            ("artifact_read", "repo_summary.json"),
            ("artifact_written", "idea_context.json"),
        ]

    def test_no_artifact_on_failure(self, tmp_path):
        store = ArtifactStore(runs_root=tmp_path, enable_events=False)
        _write_clarified(store, status="needs_blocking_input")

        with pytest.raises(ValueError):
            IdeaSourceRouter(runs_root=tmp_path).run("run_demo")

        assert not store.exists("run_demo", "idea_context.json")
