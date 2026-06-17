"""Tests for Repository Intelligence R12 clarification handoff."""

import json
from pathlib import Path

from autoad_researcher.repository_intelligence import build_clarification_question_candidates


def write_uncertainties(path: Path, groups: list[dict]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "uncertainties.json").write_text(json.dumps({"schema_version": 1, "groups": groups}), encoding="utf-8")


def test_questions_come_from_uncertainties_and_are_capped(tmp_path: Path):
    write_uncertainties(
        tmp_path,
        [
            {"category": "blocking_environment_plan", "items": [{"evidence_ids": ["ev_env"]}]},
            {"category": "blocking_entrypoint_selection", "items": [{"evidence_ids": ["ev_entry"]}]},
            {"category": "blocking_evaluation_contract", "items": [{"evidence_ids": ["ev_eval"]}]},
            {"category": "blocking_dataset_asset_access", "items": [{"evidence_ids": ["ev_data"]}]},
        ],
    )

    artifact = build_clarification_question_candidates(
        artifact_dir=tmp_path,
        output_path=tmp_path / "clarification_question_candidates.json",
    )

    assert len(artifact.questions) == 3
    assert len(artifact.backlog) == 1
    assert artifact.questions[0].blocking_area == "environment"
    assert artifact.questions[0].evidence_ids == ["ev_env"]
    assert (tmp_path / "clarification_question_candidates.json").is_file()


def test_duplicate_blocking_area_is_not_repeated(tmp_path: Path):
    write_uncertainties(
        tmp_path,
        [
            {"category": "blocking_evaluation_contract", "items": []},
            {"category": "scientific_validity_risks", "items": []},
        ],
    )

    artifact = build_clarification_question_candidates(
        artifact_dir=tmp_path,
        output_path=tmp_path / "clarification_question_candidates.json",
    )

    assert [question.blocking_area for question in artifact.questions] == ["evaluation"]


def test_no_uncertainties_produces_no_questions(tmp_path: Path):
    write_uncertainties(tmp_path, [])

    artifact = build_clarification_question_candidates(
        artifact_dir=tmp_path,
        output_path=tmp_path / "clarification_question_candidates.json",
    )

    assert artifact.questions == []
    assert artifact.backlog == []
