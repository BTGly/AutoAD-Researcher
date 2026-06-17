"""Tests for metrics parsing and scientific validity."""

from pathlib import Path

from autoad_researcher.analysis import MetricParseSpec, parse_metrics
from autoad_researcher.runner import (
    ExperimentCommandPlan,
    ExperimentExecutionResult,
    ExperimentInputRefs,
    experiment_command_sha256,
)
from autoad_researcher.supervisor import validate_scientific_contract


def test_parse_required_metric_from_json_source(tmp_path: Path):
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw/results.json").write_text('{"metrics": {"image_auroc": 0.91}}', encoding="utf-8")

    report = parse_metrics(
        tmp_path,
        [
            MetricParseSpec(
                metric_name="image_auroc",
                source_path="raw/results.json",
                json_path=["metrics", "image_auroc"],
                dataset_row="mvtec/bottle",
                unit="ratio",
                required=True,
            )
        ],
    )

    assert report.status == "passed"
    assert report.metrics[0].value == 0.91
    assert report.metrics[0].source_sha256


def test_missing_required_metric_fails_report(tmp_path: Path):
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw/results.json").write_text('{"metrics": {}}', encoding="utf-8")

    report = parse_metrics(
        tmp_path,
        [
            MetricParseSpec(
                metric_name="image_auroc",
                source_path="raw/results.json",
                json_path=["metrics", "image_auroc"],
                dataset_row="mvtec/bottle",
                unit="ratio",
                required=True,
            )
        ],
    )

    assert report.status == "failed"
    assert report.metrics[0].parse_status == "invalid"


def test_non_numeric_metric_is_invalid(tmp_path: Path):
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw/results.json").write_text('{"metrics": {"image_auroc": "high"}}', encoding="utf-8")

    report = parse_metrics(
        tmp_path,
        [
            MetricParseSpec(
                metric_name="image_auroc",
                source_path="raw/results.json",
                json_path=["metrics", "image_auroc"],
                dataset_row="mvtec/bottle",
                unit="ratio",
                required=True,
            )
        ],
    )

    assert report.status == "failed"
    assert report.metrics[0].parse_status == "invalid"


def test_parse_required_metric_from_patchcore_csv_source(tmp_path: Path):
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw/results.csv").write_text(
        "Row Names,instance_auroc,full_pixel_auroc,anomaly_pixel_auroc\n"
        "mvtec_bottle,1.0,0.9848,0.9795\n"
        "Mean,1.0,0.9848,0.9795\n",
        encoding="utf-8",
    )

    report = parse_metrics(
        tmp_path,
        [
            MetricParseSpec(
                metric_name="instance_auroc",
                source_path="raw/results.csv",
                source_format="csv",
                csv_row_key="Row Names",
                csv_row_value="mvtec_bottle",
                csv_metric_column="instance_auroc",
                dataset_row="mvtec_bottle",
                unit="ratio",
                required=True,
            )
        ],
    )

    assert report.status == "passed"
    assert report.metrics[0].value == 1.0
    assert report.metrics[0].source_sha256


def test_csv_metric_missing_column_fails(tmp_path: Path):
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw/results.csv").write_text(
        "Row Names,instance_auroc\n"
        "mvtec_bottle,1.0\n",
        encoding="utf-8",
    )

    report = parse_metrics(
        tmp_path,
        [
            MetricParseSpec(
                metric_name="full_pixel_auroc",
                source_path="raw/results.csv",
                source_format="csv",
                csv_row_key="Row Names",
                csv_row_value="mvtec_bottle",
                csv_metric_column="full_pixel_auroc",
                dataset_row="mvtec_bottle",
                unit="ratio",
                required=True,
            )
        ],
    )

    assert report.status == "failed"
    assert report.metrics[0].parse_status == "invalid"


def make_input_refs():
    command = ExperimentCommandPlan(
        schema_version=1,
        command_id="cmd",
        program="python",
        args=["train.py"],
        cwd="workspace/repos/project",
        environment={},
        timeout_seconds=10,
        network=False,
        expected_outputs=["raw/results.json"],
    )
    return ExperimentInputRefs(
        repository_fingerprint="repo-clean",
        environment_sha256="a" * 64,
        dataset_manifest_sha256="b" * 64,
        asset_manifest_sha256="c" * 64,
        command_sha256=experiment_command_sha256(command),
    )


def success_execution(input_refs):
    return ExperimentExecutionResult(
        schema_version=1,
        run_id="run_demo",
        attempt="attempt_01",
        command_id="cmd",
        command_sha256=input_refs.command_sha256,
        status="success",
        exit_code=0,
        timed_out=False,
        stdout_path="stdout.log",
        stderr_path="stderr.log",
        output_manifest_path="output_manifest.json",
    )


def test_scientific_validity_valid_when_all_checks_pass(tmp_path: Path):
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw/results.json").write_text('{"metrics": {"image_auroc": 0.91}}', encoding="utf-8")
    metrics = parse_metrics(
        tmp_path,
        [
            MetricParseSpec(
                metric_name="image_auroc",
                source_path="raw/results.json",
                json_path=["metrics", "image_auroc"],
                dataset_row="mvtec/bottle",
                unit="ratio",
                required=True,
            )
        ],
    )
    refs = make_input_refs()

    report = validate_scientific_contract(
        execution_result=success_execution(refs),
        input_refs=refs,
        metrics_report=metrics,
        expected_repository_fingerprint="repo-clean",
        actual_repository_fingerprint="repo-clean",
        expected_category="bottle",
        actual_category="bottle",
        expected_baseline="PatchCore",
        actual_baseline="PatchCore",
        seed_fixed=True,
        data_path_leak_detected=False,
    )

    assert report.status == "valid"


def test_scientific_validity_invalid_when_required_metric_missing(tmp_path: Path):
    metrics = parse_metrics(
        tmp_path,
        [
            MetricParseSpec(
                metric_name="image_auroc",
                source_path="raw/missing.json",
                json_path=["metrics", "image_auroc"],
                dataset_row="mvtec/bottle",
                unit="ratio",
                required=True,
            )
        ],
    )
    refs = make_input_refs()

    report = validate_scientific_contract(
        execution_result=success_execution(refs),
        input_refs=refs,
        metrics_report=metrics,
        expected_repository_fingerprint="repo-clean",
        actual_repository_fingerprint="repo-clean",
        expected_category="bottle",
        actual_category="bottle",
        expected_baseline="PatchCore",
        actual_baseline="PatchCore",
        seed_fixed=True,
        data_path_leak_detected=False,
    )

    assert report.status == "invalid"
    assert any(c.check_id == "required_metrics" and c.status == "failed" for c in report.checks)


def test_scientific_validity_insufficient_when_category_missing(tmp_path: Path):
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw/results.json").write_text('{"metrics": {"image_auroc": 0.91}}', encoding="utf-8")
    metrics = parse_metrics(
        tmp_path,
        [
            MetricParseSpec(
                metric_name="image_auroc",
                source_path="raw/results.json",
                json_path=["metrics", "image_auroc"],
                dataset_row="mvtec/bottle",
                unit="ratio",
                required=True,
            )
        ],
    )
    refs = make_input_refs()

    report = validate_scientific_contract(
        execution_result=success_execution(refs),
        input_refs=refs,
        metrics_report=metrics,
        expected_repository_fingerprint="repo-clean",
        actual_repository_fingerprint="repo-clean",
        expected_category=None,
        actual_category="bottle",
        expected_baseline="PatchCore",
        actual_baseline="PatchCore",
        seed_fixed=True,
        data_path_leak_detected=False,
    )

    assert report.status == "insufficient_evidence"
