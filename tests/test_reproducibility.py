"""Tests for reproducibility comparison."""

from pathlib import Path

from autoad_researcher.analysis import (
    AttemptEvidenceSummary,
    MetricParseSpec,
    compare_attempts,
    parse_metrics,
)
from autoad_researcher.supervisor import ScientificValidityReport


def evidence(**overrides) -> AttemptEvidenceSummary:
    data = {
        "attempt_id": "attempt_01",
        "repository_fingerprint": "repo",
        "case_sha256": "a" * 64,
        "configuration_sha256": "b" * 64,
        "environment_sha256": "c" * 64,
        "dataset_manifest_sha256": "d" * 64,
        "asset_manifest_sha256": "e" * 64,
        "command_sha256": "f" * 64,
        "execution_result_sha256": "1" * 64,
    }
    data.update(overrides)
    return AttemptEvidenceSummary.model_validate(data)


def validity(status="valid") -> ScientificValidityReport:
    return ScientificValidityReport(schema_version=1, status=status, checks=[])


def metrics(tmp_path: Path, value: float):
    (tmp_path / "raw").mkdir(parents=True, exist_ok=True)
    (tmp_path / "raw/results.json").write_text(
        '{"metrics": {"image_auroc": %s}}' % value,
        encoding="utf-8",
    )
    return parse_metrics(
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


def metrics_with_specs(tmp_path: Path, values: dict[str, float]):
    (tmp_path / "raw").mkdir(parents=True, exist_ok=True)
    payload = ", ".join(f'"{name}": {value}' for name, value in values.items())
    (tmp_path / "raw/results.json").write_text(
        f'{{"metrics": {{{payload}}}}}',
        encoding="utf-8",
    )
    return parse_metrics(
        tmp_path,
        [
            MetricParseSpec(
                metric_name=name,
                source_path="raw/results.json",
                json_path=["metrics", name],
                dataset_row="mvtec/bottle",
                unit="ratio",
                required=True,
            )
            for name in sorted(values)
        ],
    )


def test_reproducible_when_invariants_and_metrics_match(tmp_path: Path):
    left_metrics = metrics(tmp_path / "a1", 0.91)
    right_metrics = metrics(tmp_path / "a2", 0.9101)

    report = compare_attempts(
        attempt_01=evidence(),
        attempt_02=evidence(attempt_id="attempt_02", execution_result_sha256="2" * 64),
        metrics_01=left_metrics,
        metrics_02=right_metrics,
        validity_01=validity(),
        validity_02=validity(),
        metric_tolerances={"image_auroc": 0.001},
    )

    assert report.status == "reproducible"
    assert report.metric_comparisons[0].status == "passed"


def test_not_reproducible_when_environment_differs(tmp_path: Path):
    report = compare_attempts(
        attempt_01=evidence(),
        attempt_02=evidence(attempt_id="attempt_02", environment_sha256="0" * 64),
        metrics_01=metrics(tmp_path / "a1", 0.91),
        metrics_02=metrics(tmp_path / "a2", 0.91),
        validity_01=validity(),
        validity_02=validity(),
        metric_tolerances={"image_auroc": 0},
    )

    assert report.status == "not_reproducible"
    assert any(c.name == "environment_sha256" and c.status == "failed" for c in report.invariant_checks)


def test_not_reproducible_when_metric_outside_tolerance(tmp_path: Path):
    report = compare_attempts(
        attempt_01=evidence(),
        attempt_02=evidence(attempt_id="attempt_02"),
        metrics_01=metrics(tmp_path / "a1", 0.91),
        metrics_02=metrics(tmp_path / "a2", 0.80),
        validity_01=validity(),
        validity_02=validity(),
        metric_tolerances={"image_auroc": 0.001},
    )

    assert report.status == "not_reproducible"
    assert report.metric_comparisons[0].status == "failed"


def test_invalid_when_any_validity_invalid(tmp_path: Path):
    report = compare_attempts(
        attempt_01=evidence(),
        attempt_02=evidence(attempt_id="attempt_02"),
        metrics_01=metrics(tmp_path / "a1", 0.91),
        metrics_02=metrics(tmp_path / "a2", 0.91),
        validity_01=validity(),
        validity_02=validity("invalid"),
        metric_tolerances={"image_auroc": 0},
    )

    assert report.status == "invalid"


def test_not_reproducible_when_required_metric_only_exists_on_right(tmp_path: Path):
    report = compare_attempts(
        attempt_01=evidence(),
        attempt_02=evidence(attempt_id="attempt_02"),
        metrics_01=metrics(tmp_path / "a1", 0.91),
        metrics_02=metrics_with_specs(
            tmp_path / "a2",
            {"anomaly_pixel_auroc": 0.88, "image_auroc": 0.91},
        ),
        validity_01=validity(),
        validity_02=validity(),
        metric_tolerances={"image_auroc": 0, "anomaly_pixel_auroc": 0.001},
    )

    assert report.status == "not_reproducible"
    missing = [
        comparison
        for comparison in report.metric_comparisons
        if comparison.metric_name == "anomaly_pixel_auroc"
    ][0]
    assert missing.required is True
    assert missing.left_parse_status == "absent"
    assert missing.right_parse_status == "parsed"
    assert missing.status == "failed"
