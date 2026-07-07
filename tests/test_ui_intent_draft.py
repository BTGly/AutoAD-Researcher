"""Tests for Phase 2B UI research intent draft artifacts."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from autoad_researcher.ui.intent_draft import (
    INTENT_CONFIRMATION_JSON,
    INTENT_DRAFT_JSON,
    APPROVALS_DIR,
    CLARIFICATION_INPUT_JSON,
    INTENT_DRAFT_DIR,
    ResearchIntentDraft,
    extract_json_object,
    intent_draft_prompt_payload,
    intent_draft_to_clarification_input,
    load_intent_confirmation,
    load_intent_draft,
    parse_intent_draft_response,
    save_clarification_input,
    save_intent_confirmation,
    save_intent_draft,
)


def _payload(**overrides):
    payload = {
        "research_goal": "Reduce PatchCore runtime and GPU memory without meaningful AUROC degradation.",
        "problem_type": "resource_efficiency",
        "primary_metrics": ["wall_time_seconds", "peak_gpu_memory_mb"],
        "guardrail_metrics": ["instance_auroc"],
        "allowed_change_scope": ["patchcore/sampler.py"],
        "forbidden_change_scope": ["configs/", "tests/", "evaluator"],
        "benchmark_scope": {"dataset": "MVTec AD", "category": "bottle", "baseline": "PatchCore"},
        "success_criteria": "Runtime and memory decrease while instance_auroc does not meaningfully regress.",
        "risks": ["AUROC regression"],
        "open_questions": ["What regression margin is acceptable?"],
    }
    payload.update(overrides)
    return payload


def _draft(run_id="run_ui_001"):
    return ResearchIntentDraft(run_id=run_id, **_payload())


def test_parse_intent_draft_response_accepts_raw_json():
    text = json.dumps(_payload(), ensure_ascii=False)

    draft = parse_intent_draft_response(text, run_id="run_ui_001")

    assert draft.run_id == "run_ui_001"
    assert draft.problem_type == "resource_efficiency"
    assert "wall_time_seconds" in draft.primary_metrics


def test_parse_intent_draft_response_accepts_fenced_json():
    text = "```json\n" + json.dumps(_payload(), ensure_ascii=False) + "\n```"

    draft = parse_intent_draft_response(text, run_id="run_ui_001")

    assert draft.research_goal.startswith("Reduce PatchCore")


def test_malformed_llm_json_fails_safely():
    with pytest.raises(ValueError, match="invalid intent draft JSON|did not contain"):
        parse_intent_draft_response("{bad json", run_id="run_ui_001")


def test_intent_draft_rejects_api_key_like_secret():
    with pytest.raises(ValidationError, match="API-key-like"):
        ResearchIntentDraft(run_id="run_ui_001", **_payload(research_goal="use sk-secret12345"))


def test_save_intent_draft_writes_json_and_markdown(tmp_path: Path):
    run_dir = tmp_path / "run_ui_001"
    draft = _draft()

    path = save_intent_draft(run_dir, draft)

    assert path == run_dir / INTENT_DRAFT_DIR / INTENT_DRAFT_JSON
    assert path.is_file()
    assert (run_dir / INTENT_DRAFT_DIR / "intent_draft.md").is_file()
    loaded = load_intent_draft(run_dir)
    assert loaded == draft
    assert "sk-" not in path.read_text(encoding="utf-8")


def test_prompt_payload_redacts_secret_like_content():
    messages = intent_draft_prompt_payload(
        run_id="run_ui_001",
        transcript_tail=[{"role": "user", "content": "my key is sk-secret12345"}],
        context={"note": "sk-secret67890"},
    )

    payload = json.dumps(messages, ensure_ascii=False)
    assert "sk-secret12345" not in payload
    assert "sk-secret67890" not in payload
    assert "sk-***REDACTED***" in payload


def test_intent_draft_prompt_defers_file_level_scope():
    messages = intent_draft_prompt_payload(
        run_id="run_ui_001",
        transcript_tail=[{"role": "user", "content": "MVTec，baseline 是 PatchCore"}],
        context={},
    )

    payload = json.dumps(messages, ensure_ascii=False)
    assert "patch_scope_status" in payload
    assert "defer_to_patch_planner_after_repo_inspection" in payload
    assert "functional research-level constraint only; no file paths" in payload
    assert "path_or_module" not in payload
    assert "patch hooks" in payload


def test_intent_draft_maps_patch_scope_status_to_clarification_hints():
    draft = _draft()

    mapped = intent_draft_to_clarification_input(draft)

    assert mapped["clarification_hints"]["patch_scope_status"] == "defer_to_patch_planner_after_repo_inspection"
    assert "patch_scope_status: defer_to_patch_planner_after_repo_inspection" in mapped["input_task"]["constraints"]


def test_intent_draft_maps_to_clarification_input_shape():
    draft = _draft()

    mapped = intent_draft_to_clarification_input(draft)

    assert mapped["source"] == "ui_intent_draft"
    assert mapped["draft_ref"] == "ui_chat/intent_draft.json"
    assert mapped["input_task"]["run_id"] == draft.run_id
    assert mapped["input_task"]["baseline"] == "PatchCore"
    assert mapped["input_task"]["dataset"] == "MVTec AD"
    assert mapped["clarification_hints"]["primary_metrics"] == draft.primary_metrics


def test_save_clarification_input_writes_file(tmp_path: Path):
    run_dir = tmp_path / "run_ui_001"
    draft = _draft()

    path = save_clarification_input(run_dir, draft)

    assert path == run_dir / INTENT_DRAFT_DIR / CLARIFICATION_INPUT_JSON
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["input_task"]["request"] == draft.research_goal


def test_confirmation_requires_existing_intent_draft(tmp_path: Path):
    with pytest.raises(ValueError, match="intent_draft.json is required"):
        save_intent_confirmation(tmp_path / "run_ui_001", decision="approved")


def test_confirmation_writes_three_decision_states(tmp_path: Path):
    run_dir = tmp_path / "run_ui_001"
    save_intent_draft(run_dir, _draft())

    for decision in ("approved", "rejected", "needs_revision"):
        path = save_intent_confirmation(run_dir, decision=decision, comment="checked")
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["decision"] == decision
        assert data["checkpoint"] == "intent_confirmation"
        assert data["source_artifact"] == "ui_chat/intent_draft.json"

    loaded = load_intent_confirmation(run_dir)
    assert loaded is not None
    assert loaded.decision == "needs_revision"
    assert path == run_dir / APPROVALS_DIR / INTENT_CONFIRMATION_JSON


def test_confirmation_rejects_api_key_like_comment(tmp_path: Path):
    run_dir = tmp_path / "run_ui_001"
    save_intent_draft(run_dir, _draft())

    with pytest.raises(ValidationError, match="API-key-like"):
        save_intent_confirmation(run_dir, decision="approved", comment="sk-secret12345")


def test_invalid_run_id_does_not_write_confirmation(tmp_path: Path):
    run_dir = tmp_path / ".."
    with pytest.raises(ValueError, match="dot-only"):
        save_intent_confirmation(run_dir, decision="approved")
