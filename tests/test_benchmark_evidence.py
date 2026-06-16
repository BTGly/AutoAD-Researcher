"""测试 benchmark evidence schemas — 完整不变量。"""

import pytest
from pydantic import ValidationError

from autoad_researcher.benchmarks.evidence import (
    BenchmarkCommandSpec,
    BenchmarkDatasetFileEntry,
    BenchmarkDatasetManifest,
    BenchmarkEnvironmentSnapshot,
    BenchmarkExecutionResult,
    BenchmarkFileFingerprint,
    BenchmarkMetricsResult,
    BenchmarkMetricValue,
    BenchmarkPreflightCheck,
    BenchmarkPreflightReport,
    BenchmarkRepositoryState,
    BenchmarkWeightEntry,
    BenchmarkWeightManifest,
)

SHA = "a" * 64
SHA2 = "b" * 64
COMMIT = "a" * 40
COMMIT2 = "b" * 40


class TestFileFingerprint:
    def test_nonhex_sha_rejected(self):
        with pytest.raises(ValidationError):
            BenchmarkFileFingerprint(path="x", size_bytes=1, sha256="z" * 64)

    def test_sha_too_short(self):
        with pytest.raises(ValidationError):
            BenchmarkFileFingerprint(path="x", size_bytes=1, sha256="a" * 63)


class TestRepositoryState:
    def test_valid(self):
        s = BenchmarkRepositoryState(
            schema_version=1, case_id="c1",
            expected_commit=COMMIT, actual_commit=COMMIT2,
            detached_head=True, dirty=False, remote_url="github.com/x/y", repository_fingerprint=SHA,
        )
        assert s.dirty is False

    def test_commit_must_be_hex(self):
        with pytest.raises(ValidationError):
            BenchmarkRepositoryState(
                schema_version=1, case_id="c1",
                expected_commit="z" * 40, actual_commit=COMMIT2,
                detached_head=True, dirty=False, remote_url="github.com/x/y", repository_fingerprint=SHA,
            )


class TestEnvironmentSnapshot:
    def test_valid(self):
        e = BenchmarkEnvironmentSnapshot(
            schema_version=1, python_version="3.8", platform="linux",
            accelerator="cuda", torch_version="1.0", torchvision_version="0.1",
            cuda_available=True, cuda_device_count=1, gpu_index=0,
            lockfile_sha256=SHA, environment_sha256=SHA2,
        )
        assert e.accelerator == "cuda"

    def test_cuda_must_have_available(self):
        with pytest.raises(ValidationError, match="cuda_available"):
            BenchmarkEnvironmentSnapshot(
                schema_version=1, python_version="3.8", platform="linux",
                accelerator="cuda", torch_version="1", torchvision_version="1",
                cuda_available=False, cuda_device_count=0,
                lockfile_sha256=SHA, environment_sha256=SHA2,
            )

    def test_no_cuda_requires_zero_devices(self):
        with pytest.raises(ValidationError, match="device_count=0"):
            BenchmarkEnvironmentSnapshot(
                schema_version=1, python_version="3.8", platform="linux",
                accelerator="cpu", torch_version="1", torchvision_version="1",
                cuda_available=False, cuda_device_count=3,
                lockfile_sha256=SHA, environment_sha256=SHA2,
            )

    def test_negative_device_count(self):
        with pytest.raises(ValidationError):
            BenchmarkEnvironmentSnapshot(
                schema_version=1, python_version="3.8", platform="linux",
                accelerator="cpu", torch_version="1", torchvision_version="1",
                cuda_available=False, cuda_device_count=-1,
                lockfile_sha256=SHA, environment_sha256=SHA2,
            )


class TestWeightManifest:
    def test_offline_verified_needs_files(self):
        with pytest.raises(ValidationError, match="at least one file"):
            BenchmarkWeightManifest(
                schema_version=1, backbone="w", framework="t",
                torchvision_version="1", offline_load_verified=True,
                weight_manifest_sha256=SHA,
            )

    def test_valid_with_files(self):
        m = BenchmarkWeightManifest(
            schema_version=1, backbone="w", framework="t",
            torchvision_version="1", offline_load_verified=True,
            weight_manifest_sha256=SHA,
            files=[BenchmarkWeightEntry(relative_path="x.pth", size_bytes=100, sha256=SHA)],
        )
        assert len(m.files) == 1

    def test_weight_file_zero_size_rejected(self):
        with pytest.raises(ValidationError):
            BenchmarkWeightEntry(relative_path="x", size_bytes=0, sha256=SHA)


class TestDatasetManifest:
    def test_valid(self):
        m = BenchmarkDatasetManifest(
            schema_version=1, dataset_name="MVTec AD", category="bottle",
            root_env="DT_ROOT", train_good_count=10, test_good_count=5,
            test_anomaly_count=5, mask_count=5, manifest_sha256=SHA,
            files=[BenchmarkDatasetFileEntry(relative_path="a.png", size_bytes=100)],
        )
        assert m.manifest_strategy == "relative_path_size_v1"

    def test_must_have_at_least_one_file(self):
        with pytest.raises(ValidationError):
            BenchmarkDatasetManifest(
                schema_version=1, dataset_name="M", category="c",
                root_env="R", train_good_count=1, test_good_count=1,
                test_anomaly_count=1, mask_count=1, manifest_sha256=SHA,
            )

    def test_files_must_be_sorted(self):
        with pytest.raises(ValidationError, match="sorted"):
            BenchmarkDatasetManifest(
                schema_version=1, dataset_name="M", category="c", root_env="R",
                train_good_count=1, test_good_count=1, test_anomaly_count=1, mask_count=1,
                manifest_sha256=SHA,
                files=[
                    BenchmarkDatasetFileEntry(relative_path="b.png", size_bytes=1),
                    BenchmarkDatasetFileEntry(relative_path="a.png", size_bytes=1),
                ],
            )

    def test_duplicate_paths_rejected(self):
        with pytest.raises(ValidationError, match="duplicate"):
            BenchmarkDatasetManifest(
                schema_version=1, dataset_name="M", category="c", root_env="R",
                train_good_count=1, test_good_count=1, test_anomaly_count=1, mask_count=1,
                manifest_sha256=SHA,
                files=[
                    BenchmarkDatasetFileEntry(relative_path="a.png", size_bytes=1),
                    BenchmarkDatasetFileEntry(relative_path="a.png", size_bytes=2),
                ],
            )


class TestCommandSpec:
    def test_valid(self):
        c = BenchmarkCommandSpec(
            schema_version=1, shell=False, argv_template=["python", "x.py"],
            cwd="runs/x", timeout_seconds=7200, network_guard="g",
            resolved_argv_sha256=SHA,
        )
        assert not c.shell

    def test_forbidden_env_key_rejected(self):
        with pytest.raises(ValidationError, match="forbidden"):
            BenchmarkCommandSpec(
                schema_version=1, shell=False, argv_template=["python"],
                cwd="x", timeout_seconds=1, network_guard="g",
                resolved_argv_sha256=SHA,
                environment={"API_KEY": "secret"},
            )

    def test_allowed_env_key_ok(self):
        BenchmarkCommandSpec(
            schema_version=1, shell=False, argv_template=["python"],
            cwd="x", timeout_seconds=1, network_guard="g",
            resolved_argv_sha256=SHA,
            environment={"TORCH_HOME": "cache/torch"},
        )


class TestMetricsResult:
    def test_success_requires_metrics(self):
        with pytest.raises(ValidationError, match="non-empty"):
            BenchmarkMetricsResult(
                schema_version=1, status="success", source="x.csv", source_sha256=SHA,
                dataset_row="row1",
            )

    def test_success_requires_required_metric(self):
        with pytest.raises(ValidationError, match="required metric"):
            BenchmarkMetricsResult(
                schema_version=1, status="success", source="x.csv", source_sha256=SHA,
                dataset_row="row1",
                metrics={"a": BenchmarkMetricValue(value=0.5, unit="ratio", required=False)},
            )

    def test_success_requires_dataset_row(self):
        with pytest.raises(ValidationError, match="dataset_row"):
            BenchmarkMetricsResult(
                schema_version=1, status="success", source="x.csv", source_sha256=SHA,
                metrics={"a": BenchmarkMetricValue(value=0.5, unit="ratio", required=True)},
            )

    def test_parse_failure_must_have_empty_metrics(self):
        with pytest.raises(ValidationError, match="empty metrics"):
            BenchmarkMetricsResult(
                schema_version=1, status="metric_parse_failed", source="x.csv", source_sha256=SHA,
                metrics={"a": BenchmarkMetricValue(value=0.5, unit="ratio", required=True)},
            )

    def test_nan_rejected(self):
        with pytest.raises(ValidationError):
            BenchmarkMetricValue(value=float("nan"), unit="ratio", required=True)

    def test_inf_rejected(self):
        with pytest.raises(ValidationError):
            BenchmarkMetricValue(value=float("inf"), unit="ratio", required=True)

    def test_success_valid(self):
        m = BenchmarkMetricsResult(
            schema_version=1, status="success", source="x.csv", source_sha256=SHA,
            dataset_row="r1",
            metrics={"a": BenchmarkMetricValue(value=0.98, unit="ratio", required=True)},
        )
        assert m.is_success


class TestPreflight:
    def test_passed_must_match(self):
        with pytest.raises(ValidationError, match="passed must match"):
            BenchmarkPreflightReport(
                schema_version=1, case_id="c1", attempt="attempt_01",
                passed=True,
                checks=[BenchmarkPreflightCheck(name="n", status="failed", code="E1", message="x")],
            )

    def test_valid_report(self):
        r = BenchmarkPreflightReport(
            schema_version=1, case_id="c1", attempt="attempt_01",
            passed=True,
            checks=[BenchmarkPreflightCheck(name="n", status="passed", code="OK", message="good")],
        )
        assert r.passed


class TestExecutionResult:
    def _base(self, **kw):
        defaults = dict(schema_version=1, case_id="c1", run_id="r1",
                        attempt="attempt_01", status="success")
        defaults.update(kw)
        return defaults

    def _success_evidence(self):
        return dict(
            exit_code=0, timed_out=False,
            duration_seconds=100.0,
            started_at="2026-01-01T00:00:00Z", finished_at="2026-01-01T01:00:00Z",
            repository_fingerprint_before=SHA, repository_fingerprint_after=SHA,
            case_sha256=SHA, environment_sha256=SHA, dataset_manifest_sha256=SHA,
            weights_manifest_sha256=SHA, evaluation_contract_sha256=SHA,
            command_sha256=SHA, metrics_sha256=SHA,
        )

    def test_success_requires_all_fingerprints(self):
        # Missing fingerprints should fail
        kw = self._base()
        kw.update(dict(exit_code=0, timed_out=False,
                       started_at="2026-01-01T00:00:00Z",
                       finished_at="2026-01-01T01:00:00Z"))
        with pytest.raises(ValidationError):
            BenchmarkExecutionResult(**kw)

    def test_success_full_valid(self):
        kw = self._base()
        kw.update(self._success_evidence())
        r = BenchmarkExecutionResult(**kw)
        assert r.status == "success"

    def test_success_requires_finished_after_started(self):
        kw = self._base()
        kw.update(self._success_evidence())
        kw["finished_at"] = "2025-01-01T00:00:00Z"
        with pytest.raises(ValidationError, match="finished_at"):
            BenchmarkExecutionResult(**kw)

    def test_preflight_failed_no_exit_code(self):
        with pytest.raises(ValidationError, match="exit_code"):
            BenchmarkExecutionResult(**self._base(status="preflight_failed", exit_code=2))

    def test_execution_failed_needs_exit_or_timeout(self):
        with pytest.raises(ValidationError, match="exit_code or timed_out"):
            BenchmarkExecutionResult(**self._base(
                status="execution_failed",
                failure_code="PROCESS_FAILED", failure_message="boom",
            ))

    def test_execution_failed_with_timeout_ok(self):
        r = BenchmarkExecutionResult(**self._base(
            status="execution_failed", timed_out=True,
            failure_code="PROCESS_TIMEOUT", failure_message="timed out",
        ))
        assert r.status == "execution_failed"

    def test_repo_mutation_needs_both_fingerprints(self):
        with pytest.raises(ValidationError, match="both fingerprints"):
            BenchmarkExecutionResult(**self._base(
                status="invalid_repository_mutation",
                failure_code="REPO_MUTATED", failure_message="dirty",
            ))

    def test_extra_fields_rejected(self):
        with pytest.raises(ValidationError):
            kw = self._base()
            kw.update(self._success_evidence())
            kw["extra"] = "no"
            BenchmarkExecutionResult(**kw)  # type: ignore[arg-type]

# --- Path safety ---

class TestPathSafety:
    def test_fingerprint_traversal_rejected(self):
        with pytest.raises(ValidationError, match="traversal"):
            BenchmarkFileFingerprint(path="../../escape", size_bytes=1, sha256=SHA)

    def test_fingerprint_absolute_rejected(self):
        with pytest.raises(ValidationError, match="absolute"):
            BenchmarkFileFingerprint(path="/etc/passwd", size_bytes=1, sha256=SHA)

    def test_weight_entry_traversal_rejected(self):
        with pytest.raises(ValidationError, match="traversal"):
            BenchmarkWeightEntry(relative_path="../x", size_bytes=1, sha256=SHA)

    def test_dataset_entry_traversal_rejected(self):
        with pytest.raises(ValidationError, match="traversal"):
            BenchmarkDatasetFileEntry(relative_path="a/../../b", size_bytes=1)

    def test_command_cwd_traversal_rejected(self):
        with pytest.raises(ValidationError, match="traversal"):
            BenchmarkCommandSpec(
                schema_version=1, shell=False, argv_template=["python"], cwd="../escape",
                timeout_seconds=1, network_guard="g", resolved_argv_sha256=SHA,
            )

    def test_metrics_source_traversal_rejected(self):
        with pytest.raises(ValidationError, match="traversal"):
            BenchmarkMetricsResult(
                schema_version=1, status="success", source="../../results.csv",
                source_sha256=SHA, dataset_row="r",
                metrics={"a": BenchmarkMetricValue(value=0.5, unit="ratio", required=True)},
            )


# --- Metric parse failure ---

class TestMetricParseFailure:
    def test_parse_failure_without_source_sha_ok(self):
        m = BenchmarkMetricsResult(
            schema_version=1, status="metric_parse_failed", source="x.csv",
        )
        assert m.source_sha256 is None
        assert not m.is_success


# --- Failure code / message ---

def _fail_kw(status, **kw):
    base = dict(schema_version=1, case_id="c1", run_id="r1", attempt="attempt_01", status=status)
    base.update(kw)
    return base


class TestFailureFields:
    def test_preflight_failed_requires_code(self):
        with pytest.raises(ValidationError, match="failure_code"):
            BenchmarkExecutionResult(**_fail_kw("preflight_failed"))

    def test_execution_failed_requires_code(self):
        with pytest.raises(ValidationError, match="failure_code"):
            BenchmarkExecutionResult(**_fail_kw("execution_failed", timed_out=True))

    def test_bad_failure_code_rejected(self):
        with pytest.raises(ValidationError, match="failure_code"):
            BenchmarkExecutionResult(**_fail_kw("execution_failed",
                timed_out=True, failure_code="bad", failure_message="x"))

    def test_success_must_not_set_failure_fields(self):
        kw = _fail_kw("success")
        kw.update(dict(exit_code=0, timed_out=False,
                       started_at="2026-01-01T00:00:00Z", finished_at="2026-01-01T01:00:00Z",
                       repository_fingerprint_before=SHA, repository_fingerprint_after=SHA,
                       case_sha256=SHA, environment_sha256=SHA, dataset_manifest_sha256=SHA,
                       weights_manifest_sha256=SHA, evaluation_contract_sha256=SHA,
                       command_sha256=SHA, metrics_sha256=SHA, duration_seconds=100.0,
                       failure_code="BAD", failure_message="x"))
        with pytest.raises(ValidationError, match="failure_code"):
            BenchmarkExecutionResult(**kw)


# --- Timezone ---

class TestTimestamps:
    def test_success_naive_started_rejected(self):
        from datetime import datetime as dt
        kw = _fail_kw("success")
        kw.update(dict(exit_code=0, timed_out=False,
                       started_at=dt(2026, 1, 1, 10, 0),
                       finished_at=dt(2026, 1, 1, 11, 0),
                       repository_fingerprint_before=SHA, repository_fingerprint_after=SHA,
                       case_sha256=SHA, environment_sha256=SHA, dataset_manifest_sha256=SHA,
                       weights_manifest_sha256=SHA, evaluation_contract_sha256=SHA,
                       command_sha256=SHA, metrics_sha256=SHA, duration_seconds=100.0))
        with pytest.raises(ValidationError, match="timezone"):
            BenchmarkExecutionResult(**kw)

    def test_success_missing_duration_rejected(self):
        kw = _fail_kw("success")
        kw.update(dict(exit_code=0, timed_out=False,
                       started_at="2026-01-01T00:00:00Z", finished_at="2026-01-01T01:00:00Z",
                       repository_fingerprint_before=SHA, repository_fingerprint_after=SHA,
                       case_sha256=SHA, environment_sha256=SHA, dataset_manifest_sha256=SHA,
                       weights_manifest_sha256=SHA, evaluation_contract_sha256=SHA,
                       command_sha256=SHA, metrics_sha256=SHA))
        with pytest.raises(ValidationError, match="duration_seconds"):
            BenchmarkExecutionResult(**kw)


# --- Metric value range ---

class TestMetricValueRange:
    def test_ratio_out_of_range(self):
        with pytest.raises(ValidationError, match=r"\[0,1\]"):
            BenchmarkMetricValue(value=1.5, unit="ratio", required=True)

    def test_percent_out_of_range(self):
        with pytest.raises(ValidationError, match=r"\[0,100\]"):
            BenchmarkMetricValue(value=101, unit="percent", required=False)

    def test_negative_seconds_rejected(self):
        with pytest.raises(ValidationError, match="non-negative"):
            BenchmarkMetricValue(value=-1, unit="seconds", required=False)

    def test_count_must_be_integer(self):
        with pytest.raises(ValidationError, match="integer"):
            BenchmarkMetricValue(value=1.5, unit="count", required=False)


# --- CUDA device count ---

class TestCudaDeviceCount:
    def test_cuda_with_zero_devices_rejected(self):
        with pytest.raises(ValidationError, match="device_count >= 1"):
            BenchmarkEnvironmentSnapshot(
                schema_version=1, python_version="3.8", platform="linux",
                accelerator="cuda", torch_version="1", torchvision_version="1",
                cuda_available=True, cuda_device_count=0,
                lockfile_sha256=SHA, environment_sha256=SHA2,
            )

# --- Execution status gaps ---

class TestStatusGaps:
    def _base(self, **kw):
        d = dict(schema_version=1, case_id="c1", run_id="r1", attempt="attempt_01")
        d.update(kw)
        return d

    def test_execution_failed_exit_zero_rejected(self):
        with pytest.raises(ValidationError, match="exit_code!=0"):
            BenchmarkExecutionResult(**self._base(
                status="execution_failed", exit_code=0, timed_out=False,
                failure_code="PROCESS_FAILED", failure_message="x",
            ))

    def test_execution_failed_nonzero_ok(self):
        r = BenchmarkExecutionResult(**self._base(
            status="execution_failed", exit_code=1,
            failure_code="PROCESS_FAILED", failure_message="x",
        ))
        assert r.status == "execution_failed"

    def test_success_before_after_fingerprint_differ_rejected(self):
        kw = self._base(status="success")
        kw.update(dict(
            exit_code=0, timed_out=False, duration_seconds=100.0,
            started_at="2026-01-01T00:00:00Z", finished_at="2026-01-01T01:00:00Z",
            repository_fingerprint_before=SHA, repository_fingerprint_after="cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc",
            case_sha256=SHA, environment_sha256=SHA, dataset_manifest_sha256=SHA,
            weights_manifest_sha256=SHA, evaluation_contract_sha256=SHA,
            command_sha256=SHA, metrics_sha256=SHA,
        ))
        with pytest.raises(ValidationError, match="unchanged repository fingerprint"):
            BenchmarkExecutionResult(**kw)

    def test_metric_parse_failed_missing_exit_code_rejected(self):
        kw = self._base(status="metric_parse_failed",
                        failure_code="RESULTS_MISSING", failure_message="x")
        with pytest.raises(ValidationError, match="exit_code=0"):
            BenchmarkExecutionResult(**kw)

    def test_metric_parse_failed_nonzero_exit_rejected(self):
        kw = self._base(status="metric_parse_failed",
                        exit_code=1, failure_code="RESULTS_MISSING", failure_message="x")
        with pytest.raises(ValidationError, match="exit_code=0"):
            BenchmarkExecutionResult(**kw)

    def test_metric_parse_failed_missing_preflight_shas_rejected(self):
        kw = self._base(status="metric_parse_failed",
                        exit_code=0, timed_out=False, duration_seconds=100.0,
                        started_at="2026-01-01T00:00:00Z", finished_at="2026-01-01T01:00:00Z",
                        failure_code="RESULTS_MISSING", failure_message="x")
        with pytest.raises(ValidationError, match="repository_fingerprint_before"):
            BenchmarkExecutionResult(**kw)

    def test_metric_parse_failed_full_ok(self):
        kw = self._base(status="metric_parse_failed")
        kw.update(dict(
            exit_code=0, timed_out=False, duration_seconds=100.0,
            started_at="2026-01-01T00:00:00Z", finished_at="2026-01-01T01:00:00Z",
            repository_fingerprint_before=SHA, repository_fingerprint_after=SHA,
            case_sha256=SHA, environment_sha256=SHA, dataset_manifest_sha256=SHA,
            weights_manifest_sha256=SHA, evaluation_contract_sha256=SHA,
            command_sha256=SHA,
            failure_code="RESULTS_MISSING", failure_message="x",
        ))
        r = BenchmarkExecutionResult(**kw)
        assert r.status == "metric_parse_failed"
        assert r.metrics_sha256 is None  # allowed for parse failure
