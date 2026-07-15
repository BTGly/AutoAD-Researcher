from __future__ import annotations

from pathlib import Path

import pytest

from autoad_researcher.assistant.v2.contract_confirmation_service import (
    ConfirmationConflict,
    apply_confirmation_action_proposal,
    load_active_contract_confirmation,
    load_pending_contract_confirmation,
    request_contract_confirmation,
    resolve_contract_confirmation,
)
from autoad_researcher.assistant.v2.contract_hashing import (
    build_confirmation_semantic_projection,
    confirmation_draft_sha256,
    confirmed_contract_sha256,
)
from autoad_researcher.assistant.v2.event_service import event_to_ws_message, load_events_since
from autoad_researcher.assistant.v2.intent_contract import (
    CONTRACT_FILE,
    EvaluationConstraint,
    ResearchIntentContract,
    save_contract_draft,
)
from autoad_researcher.assistant.v2.research_semantics import (
    EvidenceConflict,
    OpenQuestion,
    ResearchModeAssessment,
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


def test_v1_authorization_hash_fixture_remains_byte_stable_and_ignores_v2_fields():
    contract = ResearchIntentContract(
        run_id="run_hash_fixture",
        task_domain="anomaly_detection",
        research_goal="improve PatchCore on MVTec AD",
        baseline="PatchCore",
        dataset="MVTec AD",
        primary_metrics=["image_level_auroc"],
        success_criteria="improve image_level_auroc",
        execution_mode="plan_only",
    )

    assert contract.authorization_schema_version == 1
    assert confirmation_draft_sha256(contract) == "f2c47f14012e271b32c1d9a8851c320b247f8c822a9a41237d907c3e209f7f30"
    assert confirmed_contract_sha256(contract) == "cce2795d6743dfc94be2ec627da7ada39ed7f339fd62b3af0b2fcb536fbbfa2b"

    with_v2_only_values = contract.model_copy(update={
        "task_profile": "systems_optimization",
        "research_object": "AI operator",
        "target_platform": "NVIDIA H100",
        "workload": "attention inference",
    })
    assert confirmation_draft_sha256(with_v2_only_values) == confirmation_draft_sha256(contract)
    assert confirmed_contract_sha256(with_v2_only_values) == confirmed_contract_sha256(contract)


def test_v2_authorization_hash_binds_task_profile_fields():
    contract = ResearchIntentContract(
        authorization_schema_version=2,
        run_id="run_v2_hash",
        task_domain="systems_optimization",
        task_profile="systems_optimization",
        task_profile_source="user",
        task_profile_evidence="我要优化 AI 算子",
        research_goal="优化 AI 算子性能",
        research_object="AI 算子",
        target_platform="NVIDIA H100",
        workload="attention inference",
        primary_metrics=["inference_latency"],
        success_criteria="latency improves by 10%",
    )
    changed = contract.model_copy(update={"target_platform": "NVIDIA A100"})
    constrained = contract.model_copy(update={
        "evaluation_constraints": contract.evaluation_constraints.model_copy(update={
            "preserve_test_set": EvaluationConstraint(
                value=True,
                source="user",
                evidence_quote="保持测试集不变",
            ),
        }),
    })

    projection = build_confirmation_semantic_projection(contract).model_dump(mode="json")
    assert projection["authorization_schema_version"] == 2
    assert projection["research_object"] == "AI 算子"
    assert confirmation_draft_sha256(changed) != confirmation_draft_sha256(contract)
    assert confirmed_contract_sha256(changed) != confirmed_contract_sha256(contract)
    assert confirmation_draft_sha256(constrained) != confirmation_draft_sha256(contract)


def test_v3_authorization_hash_binds_generic_semantics_and_system_policy():
    contract = ResearchIntentContract(
        schema_version=2,
        authorization_schema_version=3,
        run_id="run_v3_hash",
        task_domain=None,
        research_goal="复现 Library-A",
        research_object="Library-A",
        success_criteria="输出与参考实现一致",
        allowed_change_scope=[],
        forbidden_change_scope=[],
        research_modes=ResearchModeAssessment(
            primary_mode="reproduction",
            secondary_modes=["feasibility_assessment"],
            confidence=0.9,
            rationale="先复现，再评估。",
        ),
    )

    mode_changed = contract.model_copy(update={
        "research_modes": contract.research_modes.model_copy(update={
            "primary_mode": "feasibility_assessment",
        }),
    })
    question_added = contract.model_copy(update={
        "open_questions": [OpenQuestion(
            category="evaluation",
            question="如何判断一致？",
            required_now=True,
        )],
    })
    conflict_added = contract.model_copy(update={
        "evidence_conflicts": [EvidenceConflict(
            claim="目标平台是否受支持",
            status="blocking",
            evidence_refs=["ev_repo_1"],
            explanation="仓库文档与目标环境冲突。",
        )],
    })
    policy_changed = contract.model_copy(update={
        "system_safety_policy": [*contract.system_safety_policy, "require_human_approval"],
    })

    projection = build_confirmation_semantic_projection(contract).model_dump(mode="json")
    assert projection["authorization_schema_version"] == 3
    assert projection["research_modes"]["primary_mode"] == "reproduction"
    assert confirmation_draft_sha256(mode_changed) != confirmation_draft_sha256(contract)
    assert confirmation_draft_sha256(question_added) != confirmation_draft_sha256(contract)
    assert confirmation_draft_sha256(conflict_added) != confirmation_draft_sha256(contract)
    assert confirmation_draft_sha256(policy_changed) != confirmation_draft_sha256(contract)


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
        draft_sha256=changed["draft_hash"],
        decision="rejected",
    )

    assert resolved["status"] == "rejected"
    assert load_pending_contract_confirmation(run_dir) is None
    events = load_events_since(run_dir)
    assert [event["type"] for event in events if event["type"].startswith("contract.confirmation")] == [
        "contract.confirmation.requested",
        "contract.confirmation.superseded",
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


def test_confirmation_can_suspend_and_resume_without_changing_identity_or_hash(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()
    contract = _ready_contract(run_dir.name)
    save_contract_draft(run_dir, contract)
    pending = request_contract_confirmation(run_dir, contract)

    suspended = apply_confirmation_action_proposal(
        run_dir,
        action="suspend",
        confirmation_id=pending["confirmation_id"],
        draft_sha256=pending["draft_hash"],
        user_text="我先聊晚餐，研究任务稍后继续",
        evidence_quote="我先聊晚餐",
    )

    assert suspended["status"] == "needs_clarification"
    assert suspended["confirmation_id"] == pending["confirmation_id"]
    assert suspended["draft_hash"] == pending["draft_hash"]
    assert load_pending_contract_confirmation(run_dir) is None
    assert load_active_contract_confirmation(run_dir)["status"] == "needs_clarification"

    resumed = apply_confirmation_action_proposal(
        run_dir,
        action="resume",
        confirmation_id=pending["confirmation_id"],
        draft_sha256=pending["draft_hash"],
        user_text="继续刚才的研究任务",
        evidence_quote="继续刚才的研究任务",
    )

    assert resumed["status"] == "pending"
    assert resumed["confirmation_id"] == pending["confirmation_id"]
    assert resumed["draft_hash"] == pending["draft_hash"]
    lifecycle_events = [
        event["type"]
        for event in load_events_since(run_dir)
        if event["type"] in {
            "contract.confirmation.suspended",
            "contract.confirmation.resumed",
        }
    ]
    assert lifecycle_events == [
        "contract.confirmation.suspended",
        "contract.confirmation.resumed",
    ]


def test_confirmation_action_requires_exact_current_turn_evidence(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()
    contract = _ready_contract(run_dir.name)
    save_contract_draft(run_dir, contract)
    pending = request_contract_confirmation(run_dir, contract)

    with pytest.raises(ConfirmationConflict) as exc_info:
        apply_confirmation_action_proposal(
            run_dir,
            action="supersede",
            confirmation_id=pending["confirmation_id"],
            draft_sha256=pending["draft_hash"],
            user_text="继续当前任务",
            evidence_quote="用户明确换题",
        )

    assert exc_info.value.code == "confirmation_state_conflict"
    assert load_pending_contract_confirmation(run_dir)["confirmation_id"] == pending["confirmation_id"]


def test_confirmation_supersede_is_terminal_and_audited(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()
    contract = _ready_contract(run_dir.name)
    save_contract_draft(run_dir, contract)
    pending = request_contract_confirmation(run_dir, contract)

    superseded = apply_confirmation_action_proposal(
        run_dir,
        action="supersede",
        confirmation_id=pending["confirmation_id"],
        draft_sha256=pending["draft_hash"],
        user_text="放弃这个研究方向",
        evidence_quote="放弃这个研究方向",
    )

    assert superseded["status"] == "superseded"
    assert load_active_contract_confirmation(run_dir) is None
    assert [
        event["type"]
        for event in load_events_since(run_dir)
        if event["type"] == "contract.confirmation.superseded"
    ] == ["contract.confirmation.superseded"]


def test_confirmed_contract_rejects_lifecycle_changes(tmp_path: Path):
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()
    contract = _ready_contract(run_dir.name)
    save_contract_draft(run_dir, contract)
    pending = request_contract_confirmation(run_dir, contract)
    resolve_contract_confirmation(
        run_dir,
        confirmation_id=pending["confirmation_id"],
        draft_sha256=pending["draft_hash"],
        decision="approved",
    )

    with pytest.raises(ConfirmationConflict) as exc_info:
        apply_confirmation_action_proposal(
            run_dir,
            action="supersede",
            confirmation_id=pending["confirmation_id"],
            draft_sha256=pending["draft_hash"],
            user_text="换一个研究方向",
            evidence_quote="换一个研究方向",
        )

    assert exc_info.value.code == "confirmed_contract_immutable"


@pytest.mark.asyncio
async def test_confirmation_route_approves_current_ready_draft(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(draft_route, "RUNS_ROOT", str(tmp_path))
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()
    contract = _ready_contract(run_dir.name)
    save_contract_draft(run_dir, contract)
    pending = request_contract_confirmation(run_dir, contract)

    with pytest.raises(draft_route.HTTPException) as exc_info:
        await draft_route.decide_contract_confirmation(
            run_dir.name,
            draft_route.ContractConfirmationDecision(
                confirmation_id=pending["confirmation_id"],
                draft_sha256="0" * 64,
                decision="approved",
            ),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "confirmation_stale"
    assert not (run_dir / CONTRACT_FILE).exists()

    result = await draft_route.decide_contract_confirmation(
        run_dir.name,
        draft_route.ContractConfirmationDecision(
            confirmation_id=pending["confirmation_id"],
            draft_sha256=pending["draft_hash"],
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
            draft_sha256=pending["draft_hash"],
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
    pending = request_contract_confirmation(run_dir, contract)

    with pytest.raises(draft_route.HTTPException) as exc_info:
        await draft_route.decide_contract_confirmation(
            run_dir.name,
            draft_route.ContractConfirmationDecision(
                confirmation_id="contract_confirmation_stale",
                draft_sha256=pending["draft_hash"],
                decision="approved",
            ),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {
        "code": "confirmation_stale",
        "message": "contract confirmation is stale",
    }
    assert not (run_dir / CONTRACT_FILE).exists()


@pytest.mark.asyncio
async def test_confirmation_route_returns_structured_state_conflict_for_suspended_draft(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setattr(draft_route, "RUNS_ROOT", str(tmp_path))
    run_dir = tmp_path / "run_contract"
    run_dir.mkdir()
    contract = _ready_contract(run_dir.name)
    save_contract_draft(run_dir, contract)
    pending = request_contract_confirmation(run_dir, contract)
    apply_confirmation_action_proposal(
        run_dir,
        action="suspend",
        confirmation_id=pending["confirmation_id"],
        draft_sha256=pending["draft_hash"],
        user_text="先暂停确认",
        evidence_quote="先暂停确认",
    )

    with pytest.raises(draft_route.HTTPException) as exc_info:
        await draft_route.decide_contract_confirmation(
            run_dir.name,
            draft_route.ContractConfirmationDecision(
                confirmation_id=pending["confirmation_id"],
                draft_sha256=pending["draft_hash"],
                decision="approved",
            ),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail["code"] == "confirmation_state_conflict"
