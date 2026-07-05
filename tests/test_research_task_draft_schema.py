"""Tests for ResearchTaskDraftV1 schema."""

import pytest
from pydantic import ValidationError

from autoad_researcher.assistant.draft_schema import ResearchTaskDraftV1


def _valid_draft(**overrides):
    kwargs = {
        "run_id": "run_001",
        "draft_id": "draft_001",
        "metric_command": "python eval.py --split dev",
        "metric_name": "image_auroc",
        "metric_direction": "maximize",
        "baseline": "PatchCore MVTec bottle",
        "baseline_value": 0.852,
        "ambition": "beat_baseline",
        "scope": "mixed",
        "constraints": ["不能改 eval 脚本"],
        "dataset": "MVTec AD",
    }
    kwargs.update(overrides)
    return ResearchTaskDraftV1(**kwargs)


# ── 五要素字段 ──


class TestDraftMinimum:
    def test_valid_minimal(self):
        d = ResearchTaskDraftV1(
            run_id="run_001",
            draft_id="draft_001",
            metric_command="python eval.py",
            metric_name="accuracy",
            metric_direction="maximize",
            baseline="PatchCore",
            ambition="beat_baseline",
            scope="mixed",
        )
        assert d.confirmation == "draft"
        assert d.baseline_value is None

    def test_requires_metric_command(self):
        with pytest.raises(ValidationError):
            _valid_draft(metric_command="")

    def test_requires_metric_name(self):
        with pytest.raises(ValidationError):
            _valid_draft(metric_name="")

    def test_requires_baseline(self):
        with pytest.raises(ValidationError):
            _valid_draft(baseline="")

    def test_rejects_invalid_ambition(self):
        with pytest.raises(ValidationError):
            _valid_draft(ambition="invalid")

    def test_rejects_invalid_scope(self):
        with pytest.raises(ValidationError):
            _valid_draft(scope="invalid")

    def test_rejects_invalid_direction(self):
        with pytest.raises(ValidationError):
            _valid_draft(metric_direction="higher")


class TestDraftConfirmation:
    def test_default_draft(self):
        d = _valid_draft()
        assert d.confirmation == "draft"

    def test_mark_confirmed(self):
        d = _valid_draft(confirmation="confirmed")
        assert d.confirmation == "confirmed"

    def test_mark_revised(self):
        d = _valid_draft(confirmation="revised")
        assert d.confirmation == "revised"


class TestDraftExtraForbid:
    def test_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            _valid_draft(method="Cross-Scale Attention")

    def test_rejects_algorithm_field(self):
        with pytest.raises(ValidationError):
            ResearchTaskDraftV1(
                run_id="r", draft_id="d",
                metric_command="c", metric_name="m", metric_direction="maximize",
                baseline="b", ambition="beat_baseline", scope="mixed",
                algorithm="ensemble",  # extra=forbid → rejected
            )


class TestDraftNullable:
    def test_baseline_value_optional(self):
        d = _valid_draft(baseline_value=None)
        assert d.baseline_value is None

    def test_ambition_target_optional(self):
        d = _valid_draft(ambition="reach_target", ambition_target=0.90)
        assert d.ambition_target == 0.90

    def test_dataset_optional(self):
        d = _valid_draft(dataset=None)
        assert d.dataset is None
