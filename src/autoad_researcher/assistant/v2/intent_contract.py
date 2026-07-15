"""Read compatibility for intent-contract artifacts created by older runs.

New research dialogue state is stored in ``summary.json``. This module does
not build, mutate, confirm, or write intent contracts.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict


CONTRACT_DRAFT_FILE = "research_intent_contract_draft.json"
CONTRACT_FILE = "research_intent_contract.json"


class ResearchIntentContract(BaseModel):
    """Permissive read model for legacy contract artifacts."""

    model_config = ConfigDict(extra="allow")

    schema_version: int = 1
    run_id: str = ""


def load_contract_draft(run_dir: Path) -> ResearchIntentContract | None:
    return _load_contract(run_dir / CONTRACT_DRAFT_FILE)


def load_confirmed_contract(run_dir: Path) -> ResearchIntentContract | None:
    return _load_contract(run_dir / CONTRACT_FILE)


def _load_contract(path: Path) -> ResearchIntentContract | None:
    if not path.is_file():
        return None
    try:
        return ResearchIntentContract.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
