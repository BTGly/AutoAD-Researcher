"""测试 dataset preflight."""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from autoad_researcher.benchmarks.dataset import build_dataset_manifest, resolve_dataset_root
from autoad_researcher.benchmarks.errors import BenchmarkPreflightError


def _make_fixture(tmp_path: Path) -> Path:
    """Create miniature MVTec bottle fixture."""
    root = tmp_path / "datasets" / "mvtec"
    bottle = root / "bottle"
    (bottle / "train" / "good").mkdir(parents=True)
    (bottle / "test" / "good").mkdir(parents=True)
    (bottle / "test" / "broken_large").mkdir(parents=True)
    (bottle / "ground_truth" / "broken_large").mkdir(parents=True)

    (bottle / "train" / "good" / "001.png").write_text("fake")
    (bottle / "test" / "good" / "002.png").write_text("fake")
    (bottle / "test" / "broken_large" / "003.png").write_text("fake")
    (bottle / "ground_truth" / "broken_large" / "003_mask.png").write_text("fake")
    return root


def _case():
    return SimpleNamespace(
        case_id="test",
        dataset=SimpleNamespace(
            name="MVTec AD", category="bottle",
            root_env="AUTOAD_INTERNAL_BENCHMARK_DATASET_ROOT",
        ),
    )


class TestResolveRoot:
    def test_valid(self, tmp_path):
        root = _make_fixture(tmp_path)
        case = _case()
        resolved = resolve_dataset_root(case=case, environ={"AUTOAD_INTERNAL_BENCHMARK_DATASET_ROOT": str(root)},
                                        workspace_root=tmp_path)
        assert resolved == root.resolve()

    def test_env_missing(self, tmp_path):
        with pytest.raises(BenchmarkPreflightError, match="not set"):
            resolve_dataset_root(case=_case(), environ={}, workspace_root=tmp_path)

    def test_env_empty(self, tmp_path):
        with pytest.raises(BenchmarkPreflightError, match="empty"):
            resolve_dataset_root(case=_case(), environ={"AUTOAD_INTERNAL_BENCHMARK_DATASET_ROOT": "  "},
                                workspace_root=tmp_path)

    def test_outside_workspace(self, tmp_path):
        root = _make_fixture(tmp_path)
        other_workspace = tmp_path / "other"
        other_workspace.mkdir()
        (other_workspace / "datasets").mkdir()
        with pytest.raises(BenchmarkPreflightError, match="inside workspace/datasets"):
            resolve_dataset_root(case=_case(), environ={"AUTOAD_INTERNAL_BENCHMARK_DATASET_ROOT": str(root)},
                                workspace_root=other_workspace)


class TestBuildManifest:
    def test_full_structure(self, tmp_path):
        root = _make_fixture(tmp_path)
        workspace = tmp_path
        (workspace / "datasets").mkdir(exist_ok=True)
        # symlink the fixture into workspace/datasets for boundary check
        manifest = build_dataset_manifest(case=_case(), dataset_root=root, workspace_root=tmp_path)
        assert manifest.train_good_count == 1
        assert manifest.test_good_count == 1
        assert manifest.test_anomaly_count == 1
        assert manifest.mask_count == 1
        assert len(manifest.files) == 4  # train_good + test_good + anomaly image + mask
        assert manifest.manifest_strategy == "relative_path_size_v1"

    def test_deterministic_sha(self, tmp_path):
        root = _make_fixture(tmp_path)
        m1 = build_dataset_manifest(case=_case(), dataset_root=root, workspace_root=tmp_path)
        m2 = build_dataset_manifest(case=_case(), dataset_root=root, workspace_root=tmp_path)
        assert m1.manifest_sha256 == m2.manifest_sha256

    def test_train_good_empty_rejected(self, tmp_path):
        root = _make_fixture(tmp_path)
        (root / "bottle" / "train" / "good" / "001.png").unlink()
        with pytest.raises(BenchmarkPreflightError, match="empty"):
            build_dataset_manifest(case=_case(), dataset_root=root, workspace_root=tmp_path)

    def test_test_good_empty_rejected(self, tmp_path):
        root = _make_fixture(tmp_path)
        (root / "bottle" / "test" / "good" / "002.png").unlink()
        with pytest.raises(BenchmarkPreflightError, match="test/good is empty"):
            build_dataset_manifest(case=_case(), dataset_root=root, workspace_root=tmp_path)

    def test_no_anomaly_rejected(self, tmp_path):
        root = _make_fixture(tmp_path)
        import shutil
        shutil.rmtree(root / "bottle" / "test" / "broken_large")
        with pytest.raises(BenchmarkPreflightError, match="no anomaly"):
            build_dataset_manifest(case=_case(), dataset_root=root, workspace_root=tmp_path)

    def test_mask_missing_rejected(self, tmp_path):
        root = _make_fixture(tmp_path)
        (root / "bottle" / "ground_truth" / "broken_large" / "003_mask.png").unlink()
        with pytest.raises(BenchmarkPreflightError, match="missing mask"):
            build_dataset_manifest(case=_case(), dataset_root=root, workspace_root=tmp_path)

    def test_orphan_mask_rejected(self, tmp_path):
        root = _make_fixture(tmp_path)
        (root / "bottle" / "ground_truth" / "broken_large" / "orphan_mask.png").write_text("fake")
        with pytest.raises(BenchmarkPreflightError, match="orphan mask"):
            build_dataset_manifest(case=_case(), dataset_root=root, workspace_root=tmp_path)

    def test_type_mismatch_rejected(self, tmp_path):
        root = _make_fixture(tmp_path)
        (root / "bottle" / "ground_truth" / "extra_type").mkdir()
        (root / "bottle" / "ground_truth" / "extra_type" / "001_mask.png").write_text("fake")
        with pytest.raises(BenchmarkPreflightError, match="anomaly types"):
            build_dataset_manifest(case=_case(), dataset_root=root, workspace_root=tmp_path)

    def test_zero_byte_rejected(self, tmp_path):
        root = _make_fixture(tmp_path)
        (root / "bottle" / "train" / "good" / "001.png").write_text("")
        with pytest.raises(BenchmarkPreflightError, match="zero-byte"):
            build_dataset_manifest(case=_case(), dataset_root=root, workspace_root=tmp_path)

    def test_non_png_rejected(self, tmp_path):
        root = _make_fixture(tmp_path)
        (root / "bottle" / "train" / "good" / "extra.txt").write_text("x")
        with pytest.raises(BenchmarkPreflightError, match="unexpected file"):
            build_dataset_manifest(case=_case(), dataset_root=root, workspace_root=tmp_path)

    def test_manifest_has_no_absolute_path(self, tmp_path):
        root = _make_fixture(tmp_path)
        manifest = build_dataset_manifest(case=_case(), dataset_root=root, workspace_root=tmp_path)
        for f in manifest.files:
            assert not f.relative_path.startswith("/")
            assert str(root.resolve()) not in f.relative_path

    def test_file_size_change_changes_sha(self, tmp_path):
        root = _make_fixture(tmp_path)
        m1 = build_dataset_manifest(case=_case(), dataset_root=root, workspace_root=tmp_path)
        (root / "bottle" / "train" / "good" / "001.png").write_text("bigger content")
        m2 = build_dataset_manifest(case=_case(), dataset_root=root, workspace_root=tmp_path)
        assert m1.manifest_sha256 != m2.manifest_sha256

    def test_ground_truth_type_missing_rejected(self, tmp_path):
        root = _make_fixture(tmp_path)
        import shutil
        shutil.rmtree(root / "bottle" / "ground_truth" / "broken_large")
        with pytest.raises(BenchmarkPreflightError, match="anomaly types"):
            build_dataset_manifest(case=_case(), dataset_root=root, workspace_root=tmp_path)
