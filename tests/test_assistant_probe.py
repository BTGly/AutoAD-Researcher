"""Tests for silent_probe / WhatWeKnow."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from autoad_researcher.assistant.probe import KNOWN_ARTIFACT_MAP, WhatWeKnow, silent_probe


FIXTURE_RUN_ID = "silent_probe_fixture"


def test_silent_probe_extracts_known_artifact_summary():
    result = silent_probe(FIXTURE_RUN_ID, runs_root=Path("tests/fixtures"))

    assert result.run_id == FIXTURE_RUN_ID
    assert result.has_baseline_contract is True
    assert result.has_repo_summary is True
    assert result.has_paper_artifacts is True
    assert result.has_context_draft is True
    assert result.has_implementation_variants is True
    assert result.has_transfer_analysis is True
    assert result.baseline_method == "PatchCore"
    assert result.baseline_commit == "fcaa92f124fb1ad74a7acf56726decd4b27cbcad"
    assert result.modifiable_hooks == ["backbone", "coreset_sampling"]
    assert any("Coreset" in method or "coreset" in method for method in result.paper_methods)
    assert result.available_variants == ["idea_run_l3_bottle_001_var_A"]


def test_silent_probe_marks_currently_missing_fields_without_guessing():
    result = silent_probe(FIXTURE_RUN_ID, runs_root=Path("tests/fixtures"))

    assert result.dataset is None
    assert result.primary_metric is None
    assert "dataset" in result.missing_fields
    assert "primary_metric" in result.missing_fields
    assert "category" in result.missing_fields
    assert "metric_direction" in result.missing_fields
    assert not hasattr(result, "preflight_passed")


def test_silent_probe_records_evidence_artifacts_as_run_relative_paths():
    result = silent_probe(FIXTURE_RUN_ID, runs_root=Path("tests/fixtures"))

    assert "baseline_architecture_contract.json" in result.evidence_artifacts
    assert "context/research_context_draft.json" in result.evidence_artifacts
    assert all(not artifact.startswith("/") for artifact in result.evidence_artifacts)
    assert all(".." not in artifact.split("/") for artifact in result.evidence_artifacts)


def test_silent_probe_missing_artifacts_do_not_crash(tmp_path):
    (tmp_path / "empty_run").mkdir()

    result = silent_probe("empty_run", runs_root=tmp_path)

    assert result.has_baseline_contract is False
    assert result.has_paper_artifacts is False
    assert result.evidence_artifacts == []
    assert "baseline_method" in result.missing_fields


def test_silent_probe_rejects_unsafe_run_id(tmp_path):
    with pytest.raises(ValueError, match="run_id"):
        silent_probe("../escape", runs_root=tmp_path)


def test_known_artifact_map_is_hard_coded_safe_relative_paths():
    assert set(KNOWN_ARTIFACT_MAP) == {
        "baseline_contract",
        "repo_summary",
        "paper_sources",
        "paper_summary",
        "context_draft",
        "variants",
        "transfer_analysis",
    }
    for path in KNOWN_ARTIFACT_MAP.values():
        assert not path.is_absolute()
        assert ".." not in path.parts


def test_invalid_json_is_warning_not_crash(tmp_path):
    run_dir = tmp_path / "bad_run"
    run_dir.mkdir()
    (run_dir / "baseline_architecture_contract.json").write_text("{bad json", encoding="utf-8")

    result = silent_probe("bad_run", runs_root=tmp_path)

    assert result.has_baseline_contract is False
    assert result.baseline_method is None
    assert result.warnings
    assert result.warnings[0].startswith("invalid_json:baseline_architecture_contract.json")


def test_what_we_know_forbids_unknown_fields():
    with pytest.raises(ValidationError):
        WhatWeKnow(run_id="run_001", preflight_passed=True)  # type: ignore[call-arg]


def test_context_with_dataset_and_primary_metric_can_be_extracted(tmp_path):
    run_dir = tmp_path / "with_context"
    context_dir = run_dir / "context"
    context_dir.mkdir(parents=True)
    (context_dir / "research_context_draft.json").write_text(
        json.dumps(
            {
                "dataset": {"dataset_name": "MVTec AD"},
                "metrics": {"primary_metrics": ["image_auroc"]},
            }
        ),
        encoding="utf-8",
    )

    result = silent_probe("with_context", runs_root=tmp_path)

    assert result.dataset == "MVTec AD"
    assert result.primary_metric == "image_auroc"
    assert "dataset" not in result.missing_fields
    assert "primary_metric" not in result.missing_fields
