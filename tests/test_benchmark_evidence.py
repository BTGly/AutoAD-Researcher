"""测试 benchmark evidence schemas。"""

import pytest
from pydantic import ValidationError

from autoad_researcher.benchmarks.evidence import (
    BenchmarkCommandSpec,
    BenchmarkDatasetManifest,
    BenchmarkEnvironmentSnapshot,
    BenchmarkExecutionResult,
    BenchmarkFileFingerprint,
    BenchmarkMetricsResult,
    BenchmarkMetricValue,
    BenchmarkRepositoryState,
    BenchmarkWeightEntry,
    BenchmarkWeightManifest,
)


class TestBenchmarkFileFingerprint:
    def test_valid(self):
        f = BenchmarkFileFingerprint(path="x.py", size_bytes=100, sha256="a" * 64)
        assert f.sha256 == "a" * 64

    def test_sha256_wrong_length_rejected(self):
        with pytest.raises(ValidationError):
            BenchmarkFileFingerprint(path="x", size_bytes=1, sha256="short")


class TestBenchmarkRepositoryState:
    def test_valid(self):
        s = BenchmarkRepositoryState(
            schema_version=1, case_id="c1",
            expected_commit="a" * 40, actual_commit="b" * 40,
            detached_head=True, dirty=False,
            repository_fingerprint="c" * 64,
        )
        assert s.dirty is False

    def test_commit_wrong_length_rejected(self):
        with pytest.raises(ValidationError):
            BenchmarkRepositoryState(
                schema_version=1, case_id="c1",
                expected_commit="short", actual_commit="b" * 40,
                detached_head=True, dirty=False,
                repository_fingerprint="c" * 64,
            )

    def test_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            BenchmarkRepositoryState(
                schema_version=1, case_id="c1",
                expected_commit="a" * 40, actual_commit="b" * 40,
                detached_head=True, dirty=False,
                repository_fingerprint="c" * 64, extra="no",  # type: ignore[call-arg]
            )


class TestBenchmarkEnvironmentSnapshot:
    def test_valid(self):
        e = BenchmarkEnvironmentSnapshot(
            schema_version=1, python_version="3.8", platform="linux",
            accelerator="cuda", torch_version="1.0", torchvision_version="0.1",
            cuda_available=True, cuda_device_count=1,
            lockfile_sha256="a" * 64, environment_sha256="b" * 64,
        )
        assert e.accelerator == "cuda"


class TestBenchmarkWeightManifest:
    def test_valid(self):
        m = BenchmarkWeightManifest(
            schema_version=1, backbone="wideresnet50", framework="torchvision",
            torchvision_version="0.1", offline_load_verified=True,
            weight_manifest_sha256="a" * 64,
            files=[BenchmarkWeightEntry(relative_path="x.pth", size_bytes=100, sha256="b" * 64)],
        )
        assert len(m.files) == 1


class TestBenchmarkDatasetManifest:
    def test_valid(self):
        m = BenchmarkDatasetManifest(
            schema_version=1, dataset_name="MVTec AD", category="bottle",
            root_env="DT_ROOT", train_good_count=10, test_good_count=5,
            test_anomaly_count=5, mask_count=5,
            manifest_sha256="a" * 64,
        )
        assert m.train_good_count == 10


class TestBenchmarkCommandSpec:
    def test_valid(self):
        c = BenchmarkCommandSpec(
            schema_version=1, shell=False, argv_template=["python", "x.py"],
            cwd="runs/x", timeout_seconds=7200, network_guard="python_socket_guard_v1",
            resolved_argv_sha256="a" * 64,
        )
        assert not c.shell

    def test_empty_argv_rejected(self):
        with pytest.raises(ValidationError):
            BenchmarkCommandSpec(
                schema_version=1, shell=False, argv_template=[], cwd="x",
                timeout_seconds=1, network_guard="g",
                resolved_argv_sha256="a" * 64,
            )


class TestBenchmarkMetricsResult:
    def test_valid(self):
        m = BenchmarkMetricsResult(
            schema_version=1, status="success", source="results.csv",
            source_sha256="a" * 64,
            metrics={"image_auroc": BenchmarkMetricValue(value=0.98, unit="ratio", required=True)},
        )
        assert m.is_success

    def test_metric_parse_failed(self):
        m = BenchmarkMetricsResult(
            schema_version=1, status="metric_parse_failed", source="results.csv",
            source_sha256="a" * 64,
        )
        assert not m.is_success


class TestBenchmarkExecutionResult:
    def test_valid(self):
        r = BenchmarkExecutionResult(
            schema_version=1, case_id="c1", run_id="r1",
            attempt="attempt_01", status="success",
            duration_seconds=100.0,
        )
        assert r.attempt == "attempt_01"

    @pytest.mark.parametrize("bad_attempt", ["attempt_03", "", "attempt_00", "run_01"])
    def test_bad_attempt_rejected(self, bad_attempt):
        with pytest.raises(ValidationError):
            BenchmarkExecutionResult(
                schema_version=1, case_id="c1", run_id="r1",
                attempt=bad_attempt, status="success",  # type: ignore[arg-type]
            )

    def test_illegal_status_rejected(self):
        with pytest.raises(ValidationError):
            BenchmarkExecutionResult(
                schema_version=1, case_id="c1", run_id="r1",
                attempt="attempt_01", status="unknown",  # type: ignore[arg-type]
            )

    def test_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            BenchmarkExecutionResult(
                schema_version=1, case_id="c1", run_id="r1",
                attempt="attempt_01", status="success", extra="no",  # type: ignore[call-arg]
            )
