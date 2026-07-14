"""Durable confirmation Saga for the immutable research authorization contract."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import ValidationError

from autoad_researcher.assistant.v2.contract_hashing import (
    build_confirmation_semantic_projection,
    confirmation_draft_sha256,
    confirmed_contract_sha256,
)
from autoad_researcher.assistant.v2.intent_contract import (
    CONTRACT_DRAFT_FILE,
    CONTRACT_FILE,
    ResearchIntentContract,
    missing_contract_planning_fields,
)
from autoad_researcher.core.control_plane import (
    ControlPlaneError,
    ControlPlaneEventStore,
    CorruptAuthoritativeStore,
)
from autoad_researcher.core.control_plane.io import atomic_write_json
from autoad_researcher.core.control_plane.models import ContractConfirmationProjection
from autoad_researcher.core.control_plane.readiness import ensure_experiment_session_unlocked
from autoad_researcher.core.control_plane.unit_of_work import ControlPlaneUnitOfWork


PROJECTION_FILE = "contract_confirmation.json"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def request_contract_confirmation(
    run_dir: Path,
    contract: ResearchIntentContract,
) -> dict[str, Any]:
    current = _utcnow()
    with ControlPlaneUnitOfWork(run_dir) as uow:
        draft = _load_contract_file_strict(run_dir / CONTRACT_DRAFT_FILE)
        draft_hash = confirmation_draft_sha256(draft)
        if draft_hash != confirmation_draft_sha256(contract):
            raise ValueError("contract confirmation input does not match durable draft")
        projection = _recover_projection_unlocked(run_dir, uow, now=current)
        if projection is not None and projection.status == "confirmed":
            raise ValueError("run already has an immutable confirmed contract")
        if projection is not None and projection.status == "pending" and projection.draft_sha256 == draft_hash:
            return _pending_state(projection, draft)
        projection = ContractConfirmationProjection(
            confirmation_id=f"contract_confirmation_{uuid4().hex}",
            draft_sha256=draft_hash,
            status="pending",
            requested_at=current,
        )
        _write_projection_unlocked(run_dir, projection)

    try:
        ControlPlaneEventStore(run_dir).append_once(
            "contract.confirmation.requested",
            f"contract.confirmation.requested:{projection.confirmation_id}",
            {
                "confirmation_id": projection.confirmation_id,
                "draft_hash": draft_hash,
                "ready_for_plan": draft.ready_for_plan,
                "ready_for_repo_analysis": draft.ready_for_repo_analysis,
                "ready_for_experiment_agents": draft.ready_for_experiment_agents,
                "missing_required_fields": list(draft.missing_required_fields),
                "primary_metrics_count": len(draft.primary_metrics),
                "has_baseline_repo": bool(draft.baseline_repo),
            },
        )
    except (ControlPlaneError, OSError):
        projection = _mark_audit_repair_required(run_dir, projection.confirmation_id)
    return _pending_state(projection, draft)


def load_pending_contract_confirmation(run_dir: Path) -> dict[str, Any] | None:
    if not run_dir.exists():
        return None
    with ControlPlaneUnitOfWork(run_dir) as uow:
        projection = _recover_projection_unlocked(run_dir, uow, now=_utcnow())
        if projection is None or projection.status != "pending":
            return None
        draft = _load_contract_file_strict(run_dir / CONTRACT_DRAFT_FILE)
        if confirmation_draft_sha256(draft) != projection.draft_sha256:
            return None
        return _pending_state(projection, draft)


def decide_contract_confirmation(
    run_dir: Path,
    *,
    confirmation_id: str,
    decision: Literal["approved", "rejected"],
) -> dict[str, Any]:
    current = _utcnow()
    with ControlPlaneUnitOfWork(run_dir) as uow:
        projection = _recover_projection_unlocked(run_dir, uow, now=current)
        if projection is None:
            raise ValueError("no pending contract confirmation")
        if projection.confirmation_id != confirmation_id:
            raise ValueError("contract confirmation is stale")
        if projection.status != "pending":
            expected = "approved" if projection.status == "confirmed" else "rejected"
            if decision != expected:
                raise ValueError("contract confirmation decision conflicts with persisted result")
            return _resolved_state(projection)

        contract_hash: str | None = None
        if decision == "approved":
            draft = _load_contract_file_strict(run_dir / CONTRACT_DRAFT_FILE)
            draft_hash = confirmation_draft_sha256(draft)
            if draft_hash != projection.draft_sha256:
                raise ValueError("contract confirmation draft hash is stale")
            missing = missing_contract_planning_fields(draft)
            if missing:
                raise ValueError(f"contract draft is not ready for confirmation: {', '.join(missing)}")
            contract_hash = confirmed_contract_sha256(draft)
            existing = _load_contract_file_optional_strict(run_dir / CONTRACT_FILE)
            if existing is not None and confirmed_contract_sha256(existing) != contract_hash:
                raise CorruptAuthoritativeStore("one run cannot replace its confirmed contract")
            if existing is None:
                atomic_write_json(run_dir / CONTRACT_FILE, draft.model_dump(mode="json"))
            bridge_error: str | None = None
            try:
                ensure_experiment_session_unlocked(run_dir, uow, now=current)
            except OSError as exc:
                bridge_error = f"experiment_session_bridge_failed:{exc}"
            projection = projection.model_copy(update={
                "status": "confirmed",
                "decision": "approved",
                "contract_sha256": contract_hash,
                "resolved_at": current,
                "inconsistency": bridge_error,
                "audit_repair_required": projection.audit_repair_required or bridge_error is not None,
            })
        else:
            projection = projection.model_copy(update={
                "status": "rejected",
                "decision": "rejected",
                "resolved_at": current,
            })
        _write_projection_unlocked(run_dir, projection)

    try:
        ControlPlaneEventStore(run_dir).append_once(
            "contract.confirmation.resolved",
            f"contract.confirmation.resolved:{confirmation_id}:{decision}",
            {
                "confirmation_id": confirmation_id,
                "decision": decision,
                "draft_sha256": projection.draft_sha256,
                "contract_sha256": projection.contract_sha256,
            },
        )
    except (ControlPlaneError, OSError):
        projection = _mark_audit_repair_required(run_dir, confirmation_id)
    return _resolved_state(projection)


def resolve_contract_confirmation(
    run_dir: Path,
    *,
    confirmation_id: str,
    decision: Literal["approved", "rejected"],
) -> dict[str, Any]:
    return decide_contract_confirmation(
        run_dir,
        confirmation_id=confirmation_id,
        decision=decision,
    )


def resolve_pending_contract_confirmation(
    run_dir: Path,
    *,
    decision: Literal["approved", "rejected"],
) -> dict[str, Any] | None:
    pending = load_pending_contract_confirmation(run_dir)
    if pending is None:
        return None
    return decide_contract_confirmation(
        run_dir,
        confirmation_id=str(pending["confirmation_id"]),
        decision=decision,
    )


def recover_contract_confirmation(run_dir: Path) -> ContractConfirmationProjection | None:
    with ControlPlaneUnitOfWork(run_dir) as uow:
        return _recover_projection_unlocked(run_dir, uow, now=_utcnow())


def mark_confirmation_audit_repaired(run_dir: Path) -> ContractConfirmationProjection | None:
    with ControlPlaneUnitOfWork(run_dir):
        projection = _load_projection_unlocked(run_dir)
        if projection is None or not projection.audit_repair_required:
            return projection
        repaired = projection.model_copy(update={"audit_repair_required": False})
        _write_projection_unlocked(run_dir, repaired)
        return repaired


def _recover_projection_unlocked(
    run_dir: Path,
    uow: ControlPlaneUnitOfWork,
    *,
    now: datetime,
) -> ContractConfirmationProjection | None:
    projection = _load_projection_unlocked(run_dir)
    contract = _load_contract_file_optional_strict(run_dir / CONTRACT_FILE)
    if contract is None:
        if projection is not None and projection.status == "confirmed":
            repaired = projection.model_copy(update={
                "status": "rejected",
                "decision": "rejected",
                "contract_sha256": None,
                "resolved_at": now,
                "inconsistency": "confirmed_projection_without_contract",
                "audit_repair_required": True,
            })
            _write_projection_unlocked(run_dir, repaired)
            return repaired
        return projection

    contract_hash = confirmed_contract_sha256(contract)
    draft_hash = confirmation_draft_sha256(contract)
    if projection is not None and projection.status == "confirmed":
        if projection.contract_sha256 != contract_hash:
            raise CorruptAuthoritativeStore("confirmed contract/projection hash mismatch")
        ensure_experiment_session_unlocked(run_dir, uow, now=now)
        return projection

    inconsistency = None
    if projection is not None:
        inconsistency = f"contract_overrode_{projection.status}_projection"
    recovered = ContractConfirmationProjection(
        confirmation_id=(
            projection.confirmation_id
            if projection is not None
            else f"contract_confirmation_recovered_{contract_hash[:16]}"
        ),
        draft_sha256=draft_hash,
        status="confirmed",
        decision="approved",
        contract_sha256=contract_hash,
        requested_at=projection.requested_at if projection is not None else now,
        resolved_at=projection.resolved_at if projection is not None and projection.resolved_at else now,
        inconsistency=inconsistency,
        audit_repair_required=bool(inconsistency),
    )
    _write_projection_unlocked(run_dir, recovered)
    ensure_experiment_session_unlocked(run_dir, uow, now=now)
    return recovered


def _load_projection_unlocked(run_dir: Path) -> ContractConfirmationProjection | None:
    path = run_dir / PROJECTION_FILE
    if not path.is_file():
        return None
    try:
        return ContractConfirmationProjection.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise CorruptAuthoritativeStore(f"invalid contract confirmation projection: {path}") from exc


def _write_projection_unlocked(run_dir: Path, projection: ContractConfirmationProjection) -> None:
    atomic_write_json(
        run_dir / PROJECTION_FILE,
        projection.model_dump(mode="json", exclude_none=True),
    )


def _load_contract_file_strict(path: Path) -> ResearchIntentContract:
    contract = _load_contract_file_optional_strict(path)
    if contract is None:
        raise ValueError(f"contract file not found: {path.name}")
    return contract


def _load_contract_file_optional_strict(path: Path) -> ResearchIntentContract | None:
    if not path.is_file():
        return None
    try:
        return ResearchIntentContract.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError, ValueError) as exc:
        raise CorruptAuthoritativeStore(f"invalid contract file: {path}") from exc


def _mark_audit_repair_required(
    run_dir: Path,
    confirmation_id: str,
) -> ContractConfirmationProjection:
    with ControlPlaneUnitOfWork(run_dir):
        projection = _load_projection_unlocked(run_dir)
        if projection is None or projection.confirmation_id != confirmation_id:
            raise CorruptAuthoritativeStore("confirmation projection missing during audit degradation")
        if not projection.audit_repair_required:
            projection = projection.model_copy(update={"audit_repair_required": True})
            _write_projection_unlocked(run_dir, projection)
        return projection


def _pending_state(
    projection: ContractConfirmationProjection,
    contract: ResearchIntentContract,
) -> dict[str, Any]:
    return {
        "confirmation_id": projection.confirmation_id,
        "draft_hash": projection.draft_sha256,
        "status": "pending",
        "requested_at": projection.requested_at.isoformat(),
        "repair_required": projection.audit_repair_required,
        "semantic_projection": build_confirmation_semantic_projection(contract).model_dump(
            mode="json"
        ),
    }


def _resolved_state(projection: ContractConfirmationProjection) -> dict[str, Any]:
    return {
        "confirmation_id": projection.confirmation_id,
        "status": "approved" if projection.status == "confirmed" else "rejected",
        "resolved_at": projection.resolved_at.isoformat() if projection.resolved_at else None,
        "repair_required": projection.audit_repair_required,
        "draft_sha256": projection.draft_sha256,
        "contract_sha256": projection.contract_sha256,
    }
