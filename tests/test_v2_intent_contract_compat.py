from __future__ import annotations

import json
from pathlib import Path

from autoad_researcher.assistant.v2.intent_contract import (
    CONTRACT_DRAFT_FILE,
    CONTRACT_FILE,
    load_confirmed_contract,
    load_contract_draft,
)


def test_legacy_contract_artifacts_are_read_only_compatible(tmp_path: Path):
    draft_payload = {
        "schema_version": 1,
        "run_id": "run_legacy",
        "baseline": "PatchCore",
        "primary_metrics": ["image_level_auroc"],
    }
    (tmp_path / CONTRACT_DRAFT_FILE).write_text(
        json.dumps(draft_payload),
        encoding="utf-8",
    )
    confirmed_payload = {**draft_payload, "execution_mode": "plan_only"}
    (tmp_path / CONTRACT_FILE).write_text(
        json.dumps(confirmed_payload),
        encoding="utf-8",
    )

    draft = load_contract_draft(tmp_path)
    confirmed = load_confirmed_contract(tmp_path)

    assert draft is not None
    assert draft.model_dump(mode="json") == draft_payload
    assert confirmed is not None
    assert confirmed.model_dump(mode="json") == confirmed_payload


def test_invalid_legacy_contract_is_ignored(tmp_path: Path):
    (tmp_path / CONTRACT_DRAFT_FILE).write_text("not-json", encoding="utf-8")

    assert load_contract_draft(tmp_path) is None
