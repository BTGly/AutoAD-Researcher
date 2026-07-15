from __future__ import annotations

import json
from pathlib import Path

from autoad_researcher.assistant.v2.contract_hashing import confirmation_draft_sha256
from autoad_researcher.assistant.v2.intent_contract import (
    ResearchIntentContract,
    load_contract_draft,
    save_contract_draft,
)
from autoad_researcher.assistant.v2.intent_mutation_service import interpret_and_apply_intent_mutation


def _call(run_dir: Path, user_input: str, contract: ResearchIntentContract | None = None):
    return interpret_and_apply_intent_mutation(
        run_dir=run_dir,
        user_input=user_input,
        persisted_contract=contract,
        recent_mutation_receipts=[],
        recent_dialogue=[],
        active_sources=[],
        usable_evidence=[],
        unusable_evidence=[],
        jobs=[],
        pending_confirmation=None,
        api_key="sk-test",
        provider_url="https://example.test",
        model="test-model",
    )


def _payload(user_input: str, *, base_hash: str | None) -> dict:
    evidence = "复现 Library-A"
    start = user_input.index(evidence)
    return {
        "research_modes": {
            "primary_mode": "reproduction",
            "secondary_modes": ["feasibility_assessment"],
            "confidence": 0.95,
            "rationale": "Reproduction precedes feasibility assessment.",
        },
        "intent_mutation": {
            "base_draft_sha256": base_hash,
            "full_turn_mutation_evidence": user_input,
            "operations": [{
                "operation": "set",
                "target": "research_goal",
                "proposed_value": evidence,
                "evidence_spans": [{
                    "source": "current_user_turn",
                    "start": start,
                    "end": start + len(evidence),
                    "text": evidence,
                }],
                "confidence": 0.96,
            }],
        },
        "material_observations": [],
        "open_questions": [{
            "category": "evaluation",
            "question": "如何判断复现成功？",
            "required_now": True,
            "rationale": "The criterion is not stated.",
        }],
        "evidence_conflicts": [],
        "advisory_suggestions": [],
    }


def test_interpretation_fields_and_metadata_share_one_atomic_receipt(monkeypatch, tmp_path: Path):
    run_dir = tmp_path / "run_atomic_interpretation"
    run_dir.mkdir()
    user_input = "先复现 Library-A，再评估迁移可行性。"
    monkeypatch.setattr(
        "autoad_researcher.ui.chat_client.call_research_chat",
        lambda *args, **kwargs: {
            "reply": json.dumps(_payload(user_input, base_hash=None), ensure_ascii=False),
            "error": "",
        },
    )

    outcome = _call(run_dir, user_input)

    assert outcome.receipt.status == "applied"
    assert outcome.receipt.changed_fields == [
        "research_goal",
        "research_modes",
        "open_questions",
    ]
    durable = load_contract_draft(run_dir)
    assert durable is not None
    assert durable.research_goal == "复现 Library-A"
    assert durable.research_modes is not None
    assert durable.research_modes.primary_mode == "reproduction"
    assert durable.open_questions[0].required_now is True
    assert outcome.receipt.after_draft_sha256 == confirmation_draft_sha256(durable)


def test_interpreter_failure_leaves_existing_draft_byte_equivalent(monkeypatch, tmp_path: Path):
    run_dir = tmp_path / "run_failed_interpretation"
    original = ResearchIntentContract(run_id=run_dir.name, research_goal="原目标")
    save_contract_draft(run_dir, original)
    before = (run_dir / "research_intent_contract_draft.json").read_bytes()
    monkeypatch.setattr(
        "autoad_researcher.ui.chat_client.call_research_chat",
        lambda *args, **kwargs: {"reply": "", "error": "timeout", "error_type": "timeout"},
    )

    outcome = _call(run_dir, "改成新目标", original)

    assert outcome.receipt.status == "unchanged"
    assert outcome.receipt.reason == "interpreter_provider_error"
    assert (run_dir / "research_intent_contract_draft.json").read_bytes() == before


def test_stale_proposal_rejects_field_and_metadata_together(monkeypatch, tmp_path: Path):
    run_dir = tmp_path / "run_stale_interpretation"
    original = ResearchIntentContract(run_id=run_dir.name)
    save_contract_draft(run_dir, original)
    user_input = "先复现 Library-A，再评估迁移可行性。"
    monkeypatch.setattr(
        "autoad_researcher.ui.chat_client.call_research_chat",
        lambda *args, **kwargs: {
            "reply": json.dumps(_payload(user_input, base_hash="0" * 64), ensure_ascii=False),
            "error": "",
        },
    )

    outcome = _call(run_dir, user_input, original)

    assert outcome.receipt.status == "rejected"
    assert outcome.receipt.reason == "draft_hash_mismatch"
    assert load_contract_draft(run_dir) == original
