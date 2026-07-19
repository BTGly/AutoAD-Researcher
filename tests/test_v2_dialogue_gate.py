from pathlib import Path

from autoad_researcher.assistant.v2.dialogue_gate import DialogueGate
from autoad_researcher.assistant.v2.research_dialogue_agent import (
    DialogueDecision,
    ResearchPolicyAssessment,
    SourceInstruction,
    TargetSpec,
)
from autoad_researcher.assistant.v2.research_intent_summary import ResearchIntentSummary


def _allow_policy() -> ResearchPolicyAssessment:
    return ResearchPolicyAssessment(
        decision="allow",
        category="none",
        reason="",
        safe_alternative="",
    )


def _valid(decision: DialogueDecision) -> DialogueDecision:
    decision._is_valid = True
    return decision


def test_gate_forces_reject_policy_and_removes_all_actions(tmp_path: Path):
    decision = _valid(DialogueDecision(
        dialogue_mode="plan",
        policy_assessment=ResearchPolicyAssessment(
            decision="reject",
            category="evaluation_leakage",
            reason="test labels enter training",
            safe_alternative="use validation labels",
        ),
        source_action=SourceInstruction(
            action="request_source_removal",
            source_id="src_repo",
        ),
        task_action="prepare_experiment_task",
        target_spec=TargetSpec(
            adapter_id="kernelbench",
            selectors={"level": 2, "problem_id": 40},
        ),
    ))

    gated = DialogueGate.validate(
        decision,
        run_dir=tmp_path,
        registered_sources=[{"source_id": "src_repo"}],
    )

    assert gated.dialogue_mode == "plan"
    assert gated.policy == "deny"
    assert gated.source_action is None
    assert gated.task_action is None
    assert gated.target_spec is None


def test_gate_checks_contract_state_for_act_request(tmp_path: Path):
    decision = _valid(DialogueDecision(
        dialogue_mode="act_request",
        policy_assessment=_allow_policy(),
    ))

    missing = DialogueGate.validate(
        decision,
        run_dir=tmp_path,
        registered_sources=[],
    )
    (tmp_path / "input_task.yaml").write_text("run_id: run_demo\n", encoding="utf-8")
    present = DialogueGate.validate(
        decision,
        run_dir=tmp_path,
        registered_sources=[],
    )

    assert missing.execution_gate == "blocked_missing_contract"
    assert present.execution_gate == "blocked_dialogue_only"


def test_gate_validates_source_id_and_adapter_selectors(tmp_path: Path):
    decision = _valid(DialogueDecision(
        dialogue_mode="plan",
        policy_assessment=_allow_policy(),
        source_action=SourceInstruction(
            action="request_source_removal",
            source_id="src_missing",
        ),
        target_spec=TargetSpec(
            adapter_id="kernelbench",
            selectors={"level": 2},
        ),
    ))

    gated = DialogueGate.validate(
        decision,
        run_dir=tmp_path,
        registered_sources=[{"source_id": "src_repo"}],
    )

    assert gated.source_action is None
    assert gated.target_spec is None
    assert "unregistered_source_action_removed" in gated.gate_notes
    assert "invalid_target_spec_removed" in gated.gate_notes


def test_gate_keeps_valid_source_or_target_candidate(tmp_path: Path):
    source = _valid(DialogueDecision(
        dialogue_mode="ask",
        policy_assessment=_allow_policy(),
        source_action=SourceInstruction(
            action="request_source_removal",
            source_id="src_repo",
        ),
    ))
    target = _valid(DialogueDecision(
        dialogue_mode="plan",
        policy_assessment=_allow_policy(),
        target_spec=TargetSpec(
            adapter_id="kernelbench",
            selectors={"level": 2, "problem_id": 40},
        ),
    ))

    gated_source = DialogueGate.validate(
        source,
        run_dir=tmp_path,
        registered_sources=[{"source_id": "src_repo"}],
    )
    gated_target = DialogueGate.validate(
        target,
        run_dir=tmp_path,
        registered_sources=[{"source_id": "src_repo"}],
    )

    assert gated_source.source_action == source.source_action
    assert gated_target.target_spec == target.target_spec


def test_gate_allows_registered_pdf_reparse_and_audits_permission(tmp_path: Path):
    decision = _valid(DialogueDecision(
        dialogue_mode="act_request",
        policy_assessment=_allow_policy(),
        source_action=SourceInstruction(
            action="request_source_reparse",
            source_id="src_paper",
        ),
    ))

    gated = DialogueGate.validate(
        decision,
        run_dir=tmp_path,
        registered_sources=[{
            "source_id": "src_paper",
            "kind": "paper_pdf",
            "stored_path": "sources/src_paper/paper.pdf",
        }],
    )

    assert gated.source_action is not None
    assert gated.source_action.action == "request_source_reparse"
    assert gated.action_scope == "source"
    assert gated.source_permission is not None
    assert gated.source_permission["permission_decision"] == "allow"
    assert gated.execution_gate == "not_requested"
    assert (tmp_path / "assistant" / "permission_decisions.jsonl").is_file()


def test_gate_retains_evidence_and_feasibility_axes(tmp_path: Path):
    decision = _valid(DialogueDecision(
        dialogue_mode="plan",
        evidence_status="insufficient",
        conversation_transition="revise",
        feasibility="infeasible_as_stated",
        numeric_claim_allowed=False,
        policy_assessment=_allow_policy(),
    ))

    gated = DialogueGate.validate(decision, run_dir=tmp_path, registered_sources=[])

    assert gated.action_scope == "none"
    assert gated.policy == "allow"
    assert gated.evidence_status == "insufficient"
    assert gated.conversation_transition == "revise"
    assert gated.feasibility == "infeasible_as_stated"
    assert gated.numeric_claim_allowed is False


def test_gate_rejects_reparse_without_registered_pdf_input(tmp_path: Path):
    decision = _valid(DialogueDecision(
        dialogue_mode="act_request",
        policy_assessment=_allow_policy(),
        source_action=SourceInstruction(
            action="request_source_reparse",
            source_id="src_summary_only",
        ),
    ))

    gated = DialogueGate.validate(
        decision,
        run_dir=tmp_path,
        registered_sources=[{
            "source_id": "src_summary_only",
            "kind": "paper_pdf",
            "stored_path": "",
        }],
    )

    assert gated.source_action is None
    assert gated.source_permission is None
    assert "source_reparse_unavailable" in gated.gate_notes


def test_explicit_task_action_requires_goal_but_allows_an_open_question(tmp_path: Path):
    decision = _valid(DialogueDecision(
        dialogue_mode="plan",
        policy_assessment=_allow_policy(),
        task_action="prepare_experiment_task",
    ))
    gated = DialogueGate.validate(
        decision,
        run_dir=tmp_path,
        registered_sources=[],
    )

    assert DialogueGate.task_action_allowed(
        gated,
        ResearchIntentSummary(goal="复现实验", blocking_question=None),
    ) is True
    assert DialogueGate.task_action_allowed(
        gated,
        ResearchIntentSummary(goal="", blocking_question=None),
    ) is False
    assert DialogueGate.task_action_allowed(
        gated,
        ResearchIntentSummary(goal="复现实验", blocking_question="缺少数据"),
    ) is True


def test_missing_contract_execution_can_prepare_task_without_task_action(tmp_path: Path):
    decision = _valid(DialogueDecision(
        dialogue_mode="act",
        policy_assessment=_allow_policy(),
    ))
    gated = DialogueGate.validate(
        decision,
        run_dir=tmp_path,
        registered_sources=[],
    )

    assert gated.task_action is None
    assert gated.execution_gate == "blocked_missing_contract"
    assert DialogueGate.missing_contract_execution_can_prepare_task(
        gated,
        ResearchIntentSummary(goal="复现实验", blocking_question=None),
    ) is True


def test_act_keeps_task_action_hint_but_removes_repository_target(tmp_path: Path):
    decision = _valid(DialogueDecision(
        dialogue_mode="act",
        policy_assessment=_allow_policy(),
        task_action="prepare_experiment_task",
        target_spec=TargetSpec(
            adapter_id="kernelbench",
            selectors={"level": 2, "problem_id": 40},
        ),
    ))

    gated = DialogueGate.validate(
        decision,
        run_dir=tmp_path,
        registered_sources=[],
    )

    assert gated.task_action is not None
    assert gated.target_spec is None


def test_invalid_semantic_decision_cannot_produce_actions(tmp_path: Path):
    decision = DialogueDecision(
        dialogue_mode="plan",
        policy_assessment=_allow_policy(),
        task_action="prepare_experiment_task",
        target_spec=TargetSpec(
            adapter_id="kernelbench",
            selectors={"level": 2, "problem_id": 40},
        ),
    )

    gated = DialogueGate.validate(
        decision,
        run_dir=tmp_path,
        registered_sources=[{"source_id": "src_repo"}],
    )

    assert gated.task_action is None
    assert gated.target_spec is None


def test_allow_policy_cannot_use_reject_mode_or_retain_actions(tmp_path: Path):
    decision = _valid(DialogueDecision(
        dialogue_mode="reject",
        policy_assessment=_allow_policy(),
        task_action="prepare_experiment_task",
    ))

    gated = DialogueGate.validate(
        decision,
        run_dir=tmp_path,
        registered_sources=[],
    )

    assert gated.dialogue_mode == "ask"
    assert gated.task_action is None
    assert "reject_mode_without_reject_policy_removed" in gated.gate_notes


def test_source_action_is_mutually_exclusive_with_other_actions(tmp_path: Path):
    decision = _valid(DialogueDecision(
        dialogue_mode="plan",
        policy_assessment=_allow_policy(),
        source_action=SourceInstruction(
            action="request_source_removal",
            source_id="src_repo",
        ),
        task_action="prepare_experiment_task",
        target_spec=TargetSpec(
            adapter_id="kernelbench",
            selectors={"level": 2, "problem_id": 40},
        ),
    ))

    gated = DialogueGate.validate(
        decision,
        run_dir=tmp_path,
        registered_sources=[{"source_id": "src_repo"}],
    )

    assert gated.source_action is not None
    assert gated.task_action is None
    assert gated.target_spec is None


def test_pending_task_does_not_remove_task_action_hint(tmp_path: Path):
    pending = tmp_path / "task_bridge" / "pending_experiment_task.json"
    pending.parent.mkdir(parents=True)
    pending.write_text("{}\n", encoding="utf-8")
    decision = _valid(DialogueDecision(
        dialogue_mode="plan",
        policy_assessment=_allow_policy(),
        task_action="prepare_experiment_task",
    ))

    gated = DialogueGate.validate(
        decision,
        run_dir=tmp_path,
        registered_sources=[],
    )

    assert gated.task_action is not None
