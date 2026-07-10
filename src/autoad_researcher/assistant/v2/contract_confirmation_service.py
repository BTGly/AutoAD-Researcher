"""Persisted confirmation state for a ready research intent contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from autoad_researcher.assistant.v2.event_service import append_typed_event, load_events_since
from autoad_researcher.assistant.v2.intent_contract import ResearchIntentContract


def request_contract_confirmation(
    run_dir: Path,
    contract: ResearchIntentContract,
) -> dict[str, Any]:
    draft_hash = _contract_hash(contract)
    pending = load_pending_contract_confirmation(run_dir)
    if pending is not None and pending.get("draft_hash") == draft_hash:
        return pending

    confirmation_id = f"contract_confirmation_{uuid4().hex}"
    event = append_typed_event(run_dir, "contract.confirmation.requested", {
        "confirmation_id": confirmation_id,
        "draft_hash": draft_hash,
        "ready_for_plan": contract.ready_for_plan,
        "ready_for_repo_analysis": contract.ready_for_repo_analysis,
        "ready_for_experiment_agents": contract.ready_for_experiment_agents,
        "missing_required_fields": list(contract.missing_required_fields),
        "primary_metrics_count": len(contract.primary_metrics),
        "has_baseline_repo": bool(contract.baseline_repo),
    })
    return _pending_state(event)


def load_pending_contract_confirmation(run_dir: Path) -> dict[str, Any] | None:
    pending: dict[str, Any] | None = None
    for event in load_events_since(run_dir):
        event_type = event.get("type")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        confirmation_id = payload.get("confirmation_id")
        if event_type == "contract.confirmation.requested" and isinstance(confirmation_id, str):
            pending = _pending_state(event)
        elif (
            event_type == "contract.confirmation.resolved"
            and pending is not None
            and confirmation_id == pending.get("confirmation_id")
        ):
            pending = None
    return pending


def resolve_contract_confirmation(
    run_dir: Path,
    *,
    confirmation_id: str,
    decision: Literal["approved", "rejected"],
) -> dict[str, Any]:
    pending = load_pending_contract_confirmation(run_dir)
    if pending is None:
        raise ValueError("no pending contract confirmation")
    if pending.get("confirmation_id") != confirmation_id:
        raise ValueError("contract confirmation is stale")

    event = append_typed_event(run_dir, "contract.confirmation.resolved", {
        "confirmation_id": confirmation_id,
        "decision": decision,
    })
    return {
        "confirmation_id": confirmation_id,
        "status": decision,
        "resolved_at": event["created_at"],
    }


def resolve_pending_contract_confirmation(
    run_dir: Path,
    *,
    decision: Literal["approved", "rejected"],
) -> dict[str, Any] | None:
    pending = load_pending_contract_confirmation(run_dir)
    if pending is None:
        return None
    return resolve_contract_confirmation(
        run_dir,
        confirmation_id=str(pending["confirmation_id"]),
        decision=decision,
    )


def _pending_state(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    return {
        "confirmation_id": payload.get("confirmation_id"),
        "draft_hash": payload.get("draft_hash"),
        "status": "pending",
        "requested_at": event.get("created_at"),
    }


def _contract_hash(contract: ResearchIntentContract) -> str:
    serialized = json.dumps(
        contract.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
