"""Tests for immutable research context freeze packages."""

import json
from pathlib import Path

import pytest

from autoad_researcher.research_context.freeze import active_freeze_context_path, freeze_context
from autoad_researcher.ui.sources import append_source_parse_attempt, append_source_ref


def _write_draft(run_dir: Path) -> None:
    context_dir = run_dir / "context"
    context_dir.mkdir(parents=True)
    (context_dir / "research_context_draft.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": run_dir.name,
                "context_id": f"ctx_{run_dir.name}_0",
                "context_version": 0,
                "task": {"task_id": f"task_{run_dir.name}", "goal": "test freeze"},
                "sources": {"paper_source_id": "src_pdf"},
                "facts": [
                    {
                        "fact_id": "fact_001",
                        "fact_type": "paper_fact",
                        "subject": "method",
                        "predicate": "mentions",
                        "value": "coreset",
                        "status": "confirmed",
                        "evidence_ids": ["ev_001"],
                        "evidence_refs": [
                            {
                                "source_id": "src_pdf",
                                "parse_attempt_id": "pa_000001",
                                "artifact": "paper/parse/attempts/pa_000001",
                                "evidence_type": "parsed_full_text",
                            }
                        ],
                        "producer_stage": "3.2_paper_intelligence",
                    }
                ],
                "gaps": [],
                "conflicts": [],
                "readiness": {
                    "status": "ready_for_idea_transfer_design",
                    "next_stage": "3.4_idea_transfer_design",
                },
                "source_evidence": [],
                "evidence_index_refs": [],
                "evidence_boundary": {
                    "unparsed_sources": [],
                    "partial_parse_attempts": [],
                    "failed_parse_attempts": [],
                    "claims_not_supported": [],
                },
                "context_sha256": "0" * 64,
            }
        ),
        encoding="utf-8",
    )


def _register_source(run_dir: Path) -> None:
    append_source_ref(
        run_dir,
        source_id="src_pdf",
        kind="paper_pdf",
        user_label="paper.pdf",
        stored_path="sources/src_pdf/paper.pdf",
        status="parsed",
        active_parse_attempt_id="pa_000001",
        parse_attempts=[
            {
                "parse_attempt_id": "pa_000001",
                "source_id": "src_pdf",
                "parser": "mineru_pipeline_v1",
                "status": "ok",
                "output_dir": "paper/parse/attempts/pa_000001",
                "quality_report": "paper/parse/attempts/pa_000001/parse_quality_report.json",
            }
        ],
    )


def test_freeze_is_immutable_after_new_parse_attempt(tmp_path: Path):
    run_dir = tmp_path / "run_freeze"
    run_dir.mkdir()
    _write_draft(run_dir)
    _register_source(run_dir)

    result = freeze_context(run_dir)

    assert result["freeze_version"] == "fv_001"
    first_snapshot = json.loads((run_dir / "context/freezes/fv_001/source_snapshot.json").read_text())
    assert first_snapshot["sources"][0]["active_parse_attempt_id"] == "pa_000001"

    append_source_parse_attempt(
        run_dir,
        "src_pdf",
        {
            "parse_attempt_id": "pa_000002",
            "source_id": "src_pdf",
            "parser": "mineru_pipeline_v1",
            "status": "ok",
            "output_dir": "paper/parse/attempts/pa_000002",
            "quality_report": "paper/parse/attempts/pa_000002/parse_quality_report.json",
        },
        make_active=True,
    )
    second = freeze_context(run_dir)

    assert second["freeze_version"] == "fv_002"
    unchanged = json.loads((run_dir / "context/freezes/fv_001/source_snapshot.json").read_text())
    latest = json.loads((run_dir / "context/freezes/fv_002/source_snapshot.json").read_text())
    assert unchanged["sources"][0]["active_parse_attempt_id"] == "pa_000001"
    assert latest["sources"][0]["active_parse_attempt_id"] == "pa_000002"


def test_partial_freeze_tmp_dir_not_treated_as_valid_freeze(tmp_path: Path):
    run_dir = tmp_path / "run_tmp_freeze"
    run_dir.mkdir()
    _write_draft(run_dir)
    _register_source(run_dir)
    tmp_freeze = run_dir / "context/freezes/.tmp_fv_001"
    tmp_freeze.mkdir(parents=True)
    (tmp_freeze / "partial").write_text("incomplete", encoding="utf-8")

    result = freeze_context(run_dir)

    assert result["freeze_version"] == "fv_001"
    assert (run_dir / "context/freezes/fv_001/manifest.json").is_file()
    assert not tmp_freeze.exists()
    assert active_freeze_context_path(run_dir) == run_dir / "context/freezes/fv_001/research_context_draft.json"


def test_freeze_refuses_to_overwrite_existing_version(tmp_path: Path):
    run_dir = tmp_path / "run_no_overwrite"
    run_dir.mkdir()
    _write_draft(run_dir)
    _register_source(run_dir)
    freeze_context(run_dir, freeze_version="fv_001")

    with pytest.raises(FileExistsError, match="freeze already exists"):
        freeze_context(run_dir, freeze_version="fv_001")


def test_existing_freeze_dir_refuses_overwrite(tmp_path: Path):
    run_dir = tmp_path / "run_existing_freeze"
    run_dir.mkdir()
    _write_draft(run_dir)
    _register_source(run_dir)
    freeze_context(run_dir, freeze_version="fv_001")

    with pytest.raises(FileExistsError, match="freeze already exists"):
        freeze_context(run_dir, freeze_version="fv_001")
