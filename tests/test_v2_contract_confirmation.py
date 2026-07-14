from __future__ import annotations

from pathlib import Path

import pytest

from autoad_researcher.assistant.v2.contract_confirmation_service import (
    load_pending_contract_confirmation,
    request_contract_confirmation,
    resolve_contract_confirmation,
)
from autoad_researcher.assistant.v2.contract_hashing import confirmation_draft_sha256
from autoad_researcher.assistant.v2.event_service import event_to_ws_message, load_events_since
from autoad_researcher.assistant.v2.intent_contract import (
    CONTRACT_FILE,
    ResearchIntentContract,
    save_contract_draft,
)
from autoad_researcher.server.routes import draft as draft_route


def _ready_contract(run_id: str, *, goal: str = "提升 PatchCore 在 MVTec AD 上的 image-level AUROC") -> ResearchIntentContract:
    return ResearchIntentContract(
        run_id=run_id,
        research_goal=goal,
        baseline="PatchCore",
        dataset="MVTec AD",
        primary_metrics=["image_level_auroc"],
        success_criteria="improve image-level AUROC under the same evaluation protocol",
        ready_for_plan=True,
    )


def test_contract_confirmation_state_is_persisted_deduplicated_and_replayable(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()
    contract = _ready_contract(run_dir.name)

    save_contract_draft(run_dir, contract)
    first = request_contract_confirmation(run_dir, contract)
    repeated = request_contract_confirmation(run_dir, contract)
    changed_contract = _ready_contract(
        run_dir.name,
        goal="提升 PatchCore 的图像级 AUROC 和稳定性",
    )
    save_contract_draft(run_dir, changed_contract)
    changed = request_contract_confirmation(run_dir, changed_contract)

    assert repeated["confirmation_id"] == first["confirmation_id"]
    assert changed["confirmation_id"] != first["confirmation_id"]
    assert load_pending_contract_confirmation(run_dir) == changed

    resolved = resolve_contract_confirmation(
        run_dir,
        confirmation_id=changed["confirmation_id"],
        decision="rejected",
    )

    assert resolved["status"] == "rejected"
    assert load_pending_contract_confirmation(run_dir) is None
    events = load_events_since(run_dir)
    assert [event["type"] for event in events if event["type"].startswith("contract.confirmation")] == [
        "contract.confirmation.requested",
        "contract.confirmation.requested",
        "contract.confirmation.resolved",
    ]
    assert event_to_ws_message(events[-1]) == {
        "type": "contract.confirmation.resolved",
        "confirmation_id": changed["confirmation_id"],
        "decision": "rejected",
        "draft_sha256": changed["draft_hash"],
        "contract_sha256": None,
    }


def test_confirmation_request_must_match_the_durable_draft(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()
    durable = _ready_contract(run_dir.name)
    save_contract_draft(run_dir, durable)

    with pytest.raises(ValueError, match="does not match durable draft"):
        request_contract_confirmation(
            run_dir,
            _ready_contract(run_dir.name, goal="different authorization"),
        )

    assert confirmation_draft_sha256(durable) == confirmation_draft_sha256(
        _ready_contract(run_dir.name)
    )


@pytest.mark.asyncio
async def test_confirmation_route_approves_current_ready_draft(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(draft_route, "RUNS_ROOT", str(tmp_path))
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()
    contract = _ready_contract(run_dir.name)
    save_contract_draft(run_dir, contract)
    pending = request_contract_confirmation(run_dir, contract)

    result = await draft_route.decide_contract_confirmation(
        run_dir.name,
        draft_route.ContractConfirmationDecision(
            confirmation_id=pending["confirmation_id"],
            decision="approved",
        ),
    )

    assert result["status"] == "approved"
    assert result["draft_sha256"] == pending["draft_hash"]
    assert result["contract_sha256"] is not None
    assert (run_dir / CONTRACT_FILE).is_file()
    assert load_pending_contract_confirmation(run_dir) is None
    resolved_event = [
        event
        for event in load_events_since(run_dir)
        if event["type"] == "contract.confirmation.resolved"
    ][-1]
    assert resolved_event["payload"]["draft_sha256"] == pending["draft_hash"]
    assert resolved_event["payload"]["contract_sha256"] == result["contract_sha256"]

    replay = await draft_route.decide_contract_confirmation(
        run_dir.name,
        draft_route.ContractConfirmationDecision(
            confirmation_id=pending["confirmation_id"],
            decision="approved",
        ),
    )
    assert replay["status"] == "approved"


@pytest.mark.asyncio
async def test_confirmation_route_rejects_stale_confirmation(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(draft_route, "RUNS_ROOT", str(tmp_path))
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()
    contract = _ready_contract(run_dir.name)
    save_contract_draft(run_dir, contract)
    request_contract_confirmation(run_dir, contract)

    with pytest.raises(draft_route.HTTPException) as exc_info:
        await draft_route.decide_contract_confirmation(
            run_dir.name,
            draft_route.ContractConfirmationDecision(
                confirmation_id="contract_confirmation_stale",
                decision="approved",
            ),
        )

    assert exc_info.value.status_code == 409
    assert not (run_dir / CONTRACT_FILE).exists()
