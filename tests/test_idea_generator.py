"""测试 IdeaGenerator。"""

import pytest

from autoad_researcher.core import (
    ArtifactStore,
    EventStore,
    IdeaGenerator,
    IdeaSourceRouter,
)
from autoad_researcher.ideation import DirectIdeaBackend
from autoad_researcher.schemas import (
    ClarifiedTask,
    IdeaGenerationResult,
)


def _setup_run(tmp_path):
    store = ArtifactStore(runs_root=tmp_path, enable_events=False)
    store.write_json("run_demo", "clarified_task.json", ClarifiedTask(
        run_id="run_demo",
        status="ready",
        original_request="把模块 M 加入 PatchCore",
        user_idea="把模块 M 放入 layer2",
        target_domain="visual",
    ))
    IdeaSourceRouter(runs_root=tmp_path).run("run_demo", requested_mode="direct_user_idea")


class TestIdeaGenerator:
    def test_success(self, tmp_path):
        _setup_run(tmp_path)
        result = IdeaGenerator(DirectIdeaBackend(), runs_root=tmp_path).run("run_demo")

        assert result.stage == "idea_generation"
        assert result.status == "success"
        assert result.metadata["mode"] == "direct_user_idea"
        assert result.metadata["candidate_count"] == 1

        loaded = ArtifactStore(runs_root=tmp_path, enable_events=False).read_model(
            "run_demo", "idea_candidates.json", IdeaGenerationResult
        )
        assert loaded.mode == "direct_user_idea"
        assert len(loaded.candidates) == 1

    def test_event_order(self, tmp_path):
        _setup_run(tmp_path)
        IdeaGenerator(DirectIdeaBackend(), runs_root=tmp_path).run("run_demo")

        events = EventStore(runs_root=tmp_path).read_events("run_demo")
        assert [
            (e.event_type, e.payload.get("artifact"))
            for e in events[-2:]
        ] == [
            ("artifact_read", "idea_context.json"),
            ("artifact_written", "idea_candidates.json"),
        ]

    def test_context_run_id_mismatch(self, tmp_path):
        import json
        store = ArtifactStore(runs_root=tmp_path, enable_events=False)
        store.write_json("run_demo", "clarified_task.json", ClarifiedTask(
            run_id="run_demo", status="ready", original_request="x",
            user_idea="把模块 M 放入 layer2",
        ))
        IdeaSourceRouter(runs_root=tmp_path).run("run_demo", requested_mode="direct_user_idea")
        # Overwrite idea_context with corrupt raw JSON (bypass IdeaContext validator)
        (tmp_path / "run_demo" / "idea_context.json").write_text(
            json.dumps({
                "run_id": "run_demo",
                "route": {"mode": "direct_user_idea", "reason": "x"},
                "clarified_task": {
                    "run_id": "other", "status": "ready", "original_request": "x",
                    "user_idea": "把模块 M 放入 layer2",
                },
            })
        )

        with pytest.raises(ValueError, match="in idea context"):
            IdeaGenerator(DirectIdeaBackend(), runs_root=tmp_path).run("run_demo")

    def test_backend_result_run_id_mismatch(self, tmp_path):
        _setup_run(tmp_path)

        class BadBackend(DirectIdeaBackend):
            def generate_ideas(self, *, context):
                result = super().generate_ideas(context=context)
                return result.model_copy(update={"run_id": "other"})

        with pytest.raises(ValueError, match="run_id mismatch"):
            IdeaGenerator(BadBackend(), runs_root=tmp_path).run("run_demo")

    def test_backend_result_mode_mismatch(self, tmp_path):
        _setup_run(tmp_path)

        class BadBackend(DirectIdeaBackend):
            def generate_ideas(self, *, context):
                result = super().generate_ideas(context=context)
                return result.model_copy(update={"mode": "multi_agent_exploration"})

        with pytest.raises(ValueError, match="mode mismatch"):
            IdeaGenerator(BadBackend(), runs_root=tmp_path).run("run_demo")

    def test_empty_candidates_rejected_by_revalidation(self, tmp_path):
        _setup_run(tmp_path)

        class BadBackend(DirectIdeaBackend):
            def generate_ideas(self, *, context):
                result = super().generate_ideas(context=context)
                return result.model_copy(update={"candidates": []})

        with pytest.raises(Exception):  # ValidationError via _revalidate
            IdeaGenerator(BadBackend(), runs_root=tmp_path).run("run_demo")

        assert not ArtifactStore(runs_root=tmp_path, enable_events=False).exists(
            "run_demo", "idea_candidates.json"
        )

    def test_backend_exception_propagates(self, tmp_path):
        _setup_run(tmp_path)

        class BadBackend(DirectIdeaBackend):
            def generate_ideas(self, *, context):
                raise RuntimeError("backend crash")

        with pytest.raises(RuntimeError, match="backend crash"):
            IdeaGenerator(BadBackend(), runs_root=tmp_path).run("run_demo")

    def test_no_artifact_on_failure(self, tmp_path):
        _setup_run(tmp_path)

        class BadBackend(DirectIdeaBackend):
            def generate_ideas(self, *, context):
                raise RuntimeError("crash")

        with pytest.raises(RuntimeError):
            IdeaGenerator(BadBackend(), runs_root=tmp_path).run("run_demo")

        assert not ArtifactStore(runs_root=tmp_path, enable_events=False).exists(
            "run_demo", "idea_candidates.json"
        )
