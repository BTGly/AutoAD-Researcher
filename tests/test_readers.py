"""测试 PaperReader 和 RepositoryReader。"""

from datetime import datetime, timezone

import pytest

from autoad_researcher.core import (
    ArtifactStore,
    EventStore,
    InputIntake,
    PaperReader,
    RepositoryReader,
)
from autoad_researcher.readers import (
    StaticPaperReaderBackend,
    StaticRepositoryReaderBackend,
)
from autoad_researcher.schemas import (
    InputTask,
    PaperSummary,
    RepositorySummary,
    SourceEntry,
    SourceManifest,
)


def _setup_run(tmp_path, run_id="run_demo"):
    """创建包含 paper_main 和 baseline_repo 的最小 run。"""
    InputIntake(runs_root=tmp_path).persist(
        run_id,
        task=InputTask(
            run_id=run_id,
            request="把论文方法迁移到 PatchCore。",
            source_ids=["paper_main", "baseline_repo"],
        ),
        manifest=SourceManifest(
            run_id=run_id,
            created_at=datetime.now(timezone.utc),
            sources=[
                SourceEntry(
                    source_id="paper_main",
                    kind="paper_pdf",
                    original_reference="/example/paper.pdf",
                ),
                SourceEntry(
                    source_id="baseline_repo",
                    kind="repository",
                    original_reference="/example/repo",
                ),
            ],
        ),
    )


def _paper_summary(run_id="run_demo", source_id="paper_main"):
    return PaperSummary(
        run_id=run_id,
        source_id=source_id,
        research_problem="representation learning",
        core_idea="multi-scale feature fusion",
        potential_transfer_points=["feature fusion"],
    )


def _repo_summary(run_id="run_demo", source_id="baseline_repo"):
    return RepositorySummary(
        run_id=run_id,
        source_id=source_id,
        repository_name="baseline",
        training_entrypoints=["train.py"],
        evaluation_entrypoints=["eval.py"],
        protected_paths=["eval.py"],
    )


class TestPaperReader:
    def test_run_success(self, tmp_path):
        _setup_run(tmp_path)
        paper = _paper_summary()
        reader = PaperReader(StaticPaperReaderBackend(paper), runs_root=tmp_path)

        result = reader.run("run_demo", source_id="paper_main")

        assert result.stage == "paper_reading"
        assert result.status == "success"
        assert result.artifacts == ["paper_summary.json"]

        loaded = ArtifactStore(runs_root=tmp_path, enable_events=False).read_model(
            "run_demo", "paper_summary.json", PaperSummary
        )
        assert loaded.core_idea == "multi-scale feature fusion"

    def test_events_order(self, tmp_path):
        _setup_run(tmp_path)
        paper = _paper_summary()
        PaperReader(StaticPaperReaderBackend(paper), runs_root=tmp_path).run(
            "run_demo", source_id="paper_main"
        )

        events = EventStore(runs_root=tmp_path).read_events("run_demo")
        # Last 2 events: read source_manifest, write paper_summary
        assert [
            (e.event_type, e.payload.get("artifact"))
            for e in events[-2:]
        ] == [
            ("artifact_read", "source_manifest.json"),
            ("artifact_written", "paper_summary.json"),
        ]

    def test_source_not_found(self, tmp_path):
        _setup_run(tmp_path)
        reader = PaperReader(StaticPaperReaderBackend(_paper_summary()), runs_root=tmp_path)

        with pytest.raises(ValueError, match="source_id not found"):
            reader.run("run_demo", source_id="nonexistent")

    def test_wrong_source_kind(self, tmp_path):
        _setup_run(tmp_path)
        reader = PaperReader(StaticPaperReaderBackend(_paper_summary()), runs_root=tmp_path)

        with pytest.raises(ValueError, match="not a paper source"):
            reader.run("run_demo", source_id="baseline_repo")

    def test_run_id_mismatch_rejected(self, tmp_path):
        _setup_run(tmp_path)
        bad = _paper_summary(run_id="other_id")
        reader = PaperReader(StaticPaperReaderBackend(bad), runs_root=tmp_path)

        with pytest.raises(ValueError, match="run_id mismatch"):
            reader.run("run_demo", source_id="paper_main")

    def test_source_id_mismatch_rejected(self, tmp_path):
        _setup_run(tmp_path)
        bad = _paper_summary(source_id="other_source")
        reader = PaperReader(StaticPaperReaderBackend(bad), runs_root=tmp_path)

        with pytest.raises(ValueError, match="source_id mismatch"):
            reader.run("run_demo", source_id="paper_main")

    def test_backend_exception_propagates(self, tmp_path):
        _setup_run(tmp_path)

        class FailingBackend(StaticPaperReaderBackend):
            def read_paper(self, *, run_id, source):
                raise RuntimeError("backend crash")

        reader = PaperReader(FailingBackend(_paper_summary()), runs_root=tmp_path)
        with pytest.raises(RuntimeError, match="backend crash"):
            reader.run("run_demo", source_id="paper_main")


    def test_manifest_run_id_mismatch_rejected(self, tmp_path):
        _setup_run(tmp_path)
        # Write a manifest with wrong run_id into run_demo dir
        store = ArtifactStore(runs_root=tmp_path, enable_events=False)
        bad_manifest = SourceManifest(
            run_id="other_run",
            created_at=datetime.now(timezone.utc),
            sources=[SourceEntry(source_id="p", kind="paper_pdf", original_reference="/x")],
        )
        store.write_json("run_demo", "source_manifest.json", bad_manifest, overwrite=True)

        reader = PaperReader(StaticPaperReaderBackend(_paper_summary()), runs_root=tmp_path)
        with pytest.raises(ValueError, match="source manifest run_id mismatch"):
            reader.run("run_demo", source_id="p")


class TestRepositoryReader:
    def test_run_success(self, tmp_path):
        _setup_run(tmp_path)
        repo = _repo_summary()
        reader = RepositoryReader(StaticRepositoryReaderBackend(repo), runs_root=tmp_path)

        result = reader.run("run_demo", source_id="baseline_repo")

        assert result.stage == "repository_reading"
        assert result.status == "success"
        assert result.artifacts == ["repo_summary.json"]

        loaded = ArtifactStore(runs_root=tmp_path, enable_events=False).read_model(
            "run_demo", "repo_summary.json", RepositorySummary
        )
        assert loaded.protected_paths == ["eval.py"]

    def test_events_order(self, tmp_path):
        _setup_run(tmp_path)
        repo = _repo_summary()
        RepositoryReader(StaticRepositoryReaderBackend(repo), runs_root=tmp_path).run(
            "run_demo", source_id="baseline_repo"
        )

        events = EventStore(runs_root=tmp_path).read_events("run_demo")
        assert [
            (e.event_type, e.payload.get("artifact"))
            for e in events[-2:]
        ] == [
            ("artifact_read", "source_manifest.json"),
            ("artifact_written", "repo_summary.json"),
        ]

    def test_wrong_source_kind(self, tmp_path):
        _setup_run(tmp_path)
        reader = RepositoryReader(
            StaticRepositoryReaderBackend(_repo_summary()), runs_root=tmp_path
        )
        with pytest.raises(ValueError, match="not a repository source"):
            reader.run("run_demo", source_id="paper_main")

    def test_run_id_mismatch_rejected(self, tmp_path):
        _setup_run(tmp_path)
        bad = _repo_summary(run_id="other_id")
        reader = RepositoryReader(StaticRepositoryReaderBackend(bad), runs_root=tmp_path)
        with pytest.raises(ValueError, match="run_id mismatch"):
            reader.run("run_demo", source_id="baseline_repo")

    def test_source_not_found(self, tmp_path):
        _setup_run(tmp_path)
        reader = RepositoryReader(
            StaticRepositoryReaderBackend(_repo_summary()), runs_root=tmp_path
        )
        with pytest.raises(ValueError, match="source_id not found"):
            reader.run("run_demo", source_id="nonexistent")

    def test_source_id_mismatch_rejected(self, tmp_path):
        _setup_run(tmp_path)
        bad = _repo_summary(source_id="other_source")
        reader = RepositoryReader(StaticRepositoryReaderBackend(bad), runs_root=tmp_path)
        with pytest.raises(ValueError, match="source_id mismatch"):
            reader.run("run_demo", source_id="baseline_repo")

    def test_backend_exception_propagates(self, tmp_path):
        _setup_run(tmp_path)

        class FailingBackend(StaticRepositoryReaderBackend):
            def read_repository(self, *, run_id, source):
                raise RuntimeError("backend crash")

        reader = RepositoryReader(FailingBackend(_repo_summary()), runs_root=tmp_path)
        with pytest.raises(RuntimeError, match="backend crash"):
            reader.run("run_demo", source_id="baseline_repo")
