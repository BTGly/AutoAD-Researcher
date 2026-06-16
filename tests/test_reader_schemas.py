"""测试 reader schemas。"""

import pytest
from pydantic import ValidationError

from autoad_researcher.schemas import (
    EvidenceReference,
    PaperSummary,
    RepositorySummary,
)


class TestPaperSummary:
    def test_minimal_valid(self):
        ps = PaperSummary(
            run_id="run_demo",
            source_id="paper_main",
            research_problem="representation learning",
            core_idea="multi-scale feature fusion",
        )
        assert ps.core_idea == "multi-scale feature fusion"
        assert ps.requires_anomaly_labels == "unknown"
        assert ps.potential_transfer_points == []

    def test_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            PaperSummary(
                run_id="run_demo",
                source_id="paper_main",
                research_problem="x",
                core_idea="y",
                extra_field="not allowed",  # type: ignore[call-arg]
            )

    def test_requires_anomaly_labels_rejects_illegal_value(self):
        with pytest.raises(ValidationError):
            PaperSummary(
                run_id="run_demo",
                source_id="paper_main",
                research_problem="x",
                core_idea="y",
                requires_anomaly_labels="maybe",  # type: ignore[arg-type]
            )

    def test_code_available_rejects_illegal_value(self):
        with pytest.raises(ValidationError):
            PaperSummary(
                run_id="run_demo",
                source_id="paper_main",
                research_problem="x",
                core_idea="y",
                code_available="maybe",  # type: ignore[arg-type]
            )

    def test_default_lists_independent(self):
        a = PaperSummary(run_id="r1", source_id="s1", research_problem="x", core_idea="y")
        b = PaperSummary(run_id="r2", source_id="s2", research_problem="x", core_idea="y")
        a.potential_transfer_points.append("fusion")
        assert b.potential_transfer_points == []


class TestRepositorySummary:
    def test_minimal_valid(self):
        rs = RepositorySummary(run_id="run_demo", source_id="baseline_repo")
        assert rs.editable_paths == []
        assert rs.protected_paths == []

    def test_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            RepositorySummary(
                run_id="run_demo",
                source_id="baseline_repo",
                extra_field="not allowed",  # type: ignore[call-arg]
            )

    def test_default_lists_independent(self):
        a = RepositorySummary(run_id="r1", source_id="s1")
        b = RepositorySummary(run_id="r2", source_id="s2")
        a.protected_paths.append("eval.py")
        assert b.protected_paths == []


class TestEvidenceReference:
    def test_minimal_valid(self):
        ref = EvidenceReference(source_id="paper_main", locator="page 4")
        assert ref.description is None

    def test_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            EvidenceReference(
                source_id="paper_main",
                locator="page 4",
                extra_field="not allowed",  # type: ignore[call-arg]
            )
