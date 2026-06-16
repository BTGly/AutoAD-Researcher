"""测试 InputIntake。"""

from datetime import datetime, timezone

import pytest

from autoad_researcher.core import ArtifactStore, InputIntake
from autoad_researcher.schemas import InputTask, SourceEntry, SourceManifest


def _make_manifest(run_id="run_demo"):
    return SourceManifest(
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
                original_reference="https://github.com/example/repo",
            ),
        ],
    )


def _make_task(run_id="run_demo"):
    return InputTask(
        run_id=run_id,
        request="把这篇论文中的多尺度模块迁移到异常检测。",
        source_ids=["paper_main", "baseline_repo"],
        target_domain="visual_anomaly_detection",
        baseline="PatchCore",
        constraints=["不修改 evaluation script"],
    )


class TestInputIntake:
    def test_persist_success(self, tmp_path):
        intake = InputIntake(runs_root=tmp_path)
        result = intake.persist(
            "run_demo",
            task=_make_task(),
            manifest=_make_manifest(),
        )

        assert result.stage == "input_intake"
        assert result.status == "success"
        assert result.artifacts == ["source_manifest.json", "input_task.yaml"]
        assert result.metadata["source_count"] == 2

        run_dir = tmp_path / "run_demo"
        assert (run_dir / "source_manifest.json").exists()
        assert (run_dir / "input_task.yaml").exists()

        # 能按 schema 重新读取
        store = ArtifactStore(runs_root=tmp_path, enable_events=False)
        loaded_task = store.read_yaml_model("run_demo", "input_task.yaml", InputTask)
        assert loaded_task.baseline == "PatchCore"
        loaded_manifest = store.read_model(
            "run_demo", "source_manifest.json", SourceManifest
        )
        assert len(loaded_manifest.sources) == 2

    def test_task_run_id_mismatch(self, tmp_path):
        intake = InputIntake(runs_root=tmp_path)
        with pytest.raises(ValueError, match="task run_id"):
            intake.persist("run_demo", task=_make_task("other_id"), manifest=_make_manifest())

    def test_manifest_run_id_mismatch(self, tmp_path):
        intake = InputIntake(runs_root=tmp_path)
        with pytest.raises(ValueError, match="manifest run_id"):
            intake.persist("run_demo", task=_make_task(), manifest=_make_manifest("other_id"))

    def test_unknown_source_id_fails_before_write(self, tmp_path):
        intake = InputIntake(runs_root=tmp_path)
        task = _make_task()
        task.source_ids.append("unknown_source")

        with pytest.raises(ValueError, match="unknown sources"):
            intake.persist("run_demo", task=task, manifest=_make_manifest())

        # 没有部分写入
        run_dir = tmp_path / "run_demo"
        assert not (run_dir / "source_manifest.json").exists()
        assert not (run_dir / "input_task.yaml").exists()

    def test_invalid_run_id_rejected(self, tmp_path):
        intake = InputIntake(runs_root=tmp_path)
        with pytest.raises(ValueError):
            intake.persist("../escape", task=_make_task("../escape"), manifest=_make_manifest("../escape"))
