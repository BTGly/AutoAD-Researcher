"""测试 IntentClarifier core service。"""

from datetime import datetime, timezone

import pytest

from autoad_researcher.clarifiers import RuleBasedIntentClarifierBackend
from autoad_researcher.core import (
    ArtifactStore,
    EventStore,
    InputIntake,
    IntentClarifier,
)
from autoad_researcher.schemas import (
    InputTask,
    PaperSummary,
    RepositorySummary,
    SourceEntry,
    SourceManifest,
)


def _setup_full(tmp_path):
    """创建含 input_task + paper_summary + repo_summary 的 run。"""
    InputIntake(runs_root=tmp_path).persist(
        "run_demo",
        task=InputTask(
            run_id="run_demo",
            request="把论文模块迁移到 PatchCore",
            source_ids=["paper_main", "baseline_repo"],
            constraints=["不修改 eval"],
        ),
        manifest=SourceManifest(
            run_id="run_demo",
            created_at=datetime.now(timezone.utc),
            sources=[
                SourceEntry(source_id="paper_main", kind="paper_pdf", original_reference="/paper.pdf"),
                SourceEntry(source_id="baseline_repo", kind="repository", original_reference="/repo"),
            ],
        ),
    )
    store = ArtifactStore(runs_root=tmp_path, enable_events=False)
    store.write_json("run_demo", "paper_summary.json", PaperSummary(
        run_id="run_demo", source_id="paper_main",
        research_problem="x", core_idea="multi-scale fusion",
    ))
    store.write_json("run_demo", "repo_summary.json", RepositorySummary(
        run_id="run_demo", source_id="baseline_repo",
        baseline_methods=["PatchCore"],
    ))


class TestIntentClarifier:
    def test_success_full_input(self, tmp_path):
        _setup_full(tmp_path)
        clarifier = IntentClarifier(RuleBasedIntentClarifierBackend(), runs_root=tmp_path)
        result = clarifier.run("run_demo")

        assert result.stage == "intent_clarification"
        assert result.status == "success"

        ct = ArtifactStore(runs_root=tmp_path, enable_events=False).read_model(
            "run_demo", "clarified_task.json",
            __import__("autoad_researcher.schemas", fromlist=["ClarifiedTask"]).ClarifiedTask,
        )
        assert ct.original_request == "把论文模块迁移到 PatchCore"
        assert ct.baseline is None  # not set in input_task

    def test_input_task_only(self, tmp_path):
        InputIntake(runs_root=tmp_path).persist(
            "run_demo",
            task=InputTask(run_id="run_demo", request="迁移方法",
                           target_domain="visual", baseline="PatchCore"),
            manifest=SourceManifest(
                run_id="run_demo", created_at=datetime.now(timezone.utc), sources=[],
            ),
        )
        result = IntentClarifier(RuleBasedIntentClarifierBackend(), runs_root=tmp_path).run("run_demo")
        assert result.status == "success"

    def test_event_order(self, tmp_path):
        _setup_full(tmp_path)
        IntentClarifier(RuleBasedIntentClarifierBackend(), runs_root=tmp_path).run("run_demo")

        events = EventStore(runs_root=tmp_path).read_events("run_demo")
        # last event should be artifact_written clarified_task.json
        assert events[-1].event_type == "artifact_written"
        assert events[-1].payload["artifact"] == "clarified_task.json"

    def test_input_task_run_id_mismatch(self, tmp_path):
        store = ArtifactStore(runs_root=tmp_path, enable_events=False)
        store.write_yaml("run_demo", "input_task.yaml",
                         InputTask(run_id="other", request="x"))

        with pytest.raises(ValueError, match="run_id mismatch"):
            IntentClarifier(RuleBasedIntentClarifierBackend(), runs_root=tmp_path).run("run_demo")

    def test_paper_summary_run_id_mismatch(self, tmp_path):
        _setup_full(tmp_path)
        store = ArtifactStore(runs_root=tmp_path, enable_events=False)
        store.write_json("run_demo", "paper_summary.json", PaperSummary(
            run_id="other", source_id="paper_main", research_problem="x", core_idea="y",
        ), overwrite=True)

        with pytest.raises(ValueError, match="run_id mismatch"):
            IntentClarifier(RuleBasedIntentClarifierBackend(), runs_root=tmp_path).run("run_demo")

    def test_paper_source_id_not_in_task(self, tmp_path):
        _setup_full(tmp_path)
        store = ArtifactStore(runs_root=tmp_path, enable_events=False)
        store.write_json("run_demo", "paper_summary.json", PaperSummary(
            run_id="run_demo", source_id="unknown_source", research_problem="x", core_idea="y",
        ), overwrite=True)

        with pytest.raises(ValueError, match="not referenced"):
            IntentClarifier(RuleBasedIntentClarifierBackend(), runs_root=tmp_path).run("run_demo")

    def test_backend_rewrites_baseline_rejected(self, tmp_path):
        _setup_full(tmp_path)
        store = ArtifactStore(runs_root=tmp_path, enable_events=False)
        store.write_yaml("run_demo", "input_task.yaml", InputTask(
            run_id="run_demo", request="x", source_ids=["paper_main", "baseline_repo"], baseline="PatchCore",
        ), overwrite=True)

        class BadBackend(RuleBasedIntentClarifierBackend):
            def clarify(self, *, context):
                result = super().clarify(context=context)
                result.baseline = "FastFlow"
                return result

        with pytest.raises(ValueError, match="must not rewrite"):
            IntentClarifier(BadBackend(), runs_root=tmp_path).run("run_demo")

    def test_backend_rewrites_original_request_rejected(self, tmp_path):
        _setup_full(tmp_path)

        class BadBackend(RuleBasedIntentClarifierBackend):
            def clarify(self, *, context):
                result = super().clarify(context=context)
                return result.model_copy(update={"original_request": "changed"})

        with pytest.raises(ValueError, match="must preserve original request"):
            IntentClarifier(BadBackend(), runs_root=tmp_path).run("run_demo")

    def test_backend_exception_propagates(self, tmp_path):
        _setup_full(tmp_path)

        class BadBackend(RuleBasedIntentClarifierBackend):
            def clarify(self, *, context):
                raise RuntimeError("backend crash")

        with pytest.raises(RuntimeError, match="backend crash"):
            IntentClarifier(BadBackend(), runs_root=tmp_path).run("run_demo")

    def test_no_artifact_on_failure(self, tmp_path):
        _setup_full(tmp_path)

        class BadBackend(RuleBasedIntentClarifierBackend):
            def clarify(self, *, context):
                raise RuntimeError("crash")

        with pytest.raises(RuntimeError):
            IntentClarifier(BadBackend(), runs_root=tmp_path).run("run_demo")

        store = ArtifactStore(runs_root=tmp_path, enable_events=False)
        assert not store.exists("run_demo", "clarified_task.json")
