from __future__ import annotations

import json
from pathlib import Path

from autoad_researcher.assistant.v2.dialogue_state import (
    append_dialogue_transition,
    build_dialogue_state_projection,
)
from autoad_researcher.assistant.v2.job_service import append_pipeline_job
from autoad_researcher.assistant.v2.research_dialogue_agent import (
    GatedDialogueDecision,
    ResearchPolicyAssessment,
    SourceInstruction,
)
from autoad_researcher.assistant.v2.research_intent_summary import ResearchIntentSummary


def _decision() -> GatedDialogueDecision:
    return GatedDialogueDecision(
        dialogue_mode="act_request",
        policy_assessment=ResearchPolicyAssessment(
            decision="allow",
            category="none",
            reason="",
            safe_alternative="",
        ),
        source_action=SourceInstruction(
            action="request_source_reparse",
            source_id="src_paper",
        ),
        source_permission={"permission_decision": "allow"},
    )


def test_projection_uses_persisted_transition_and_bounded_run_state(tmp_path: Path):
    (tmp_path / "sources").mkdir()
    (tmp_path / "sources" / "source_references.json").write_text(
        json.dumps({"sources": [{
            "source_id": "src_paper",
            "kind": "paper_pdf",
            "status": "parsed",
            "active_parse_attempt_id": "pa_02",
            "parse_attempts": [{"parse_attempt_id": "pa_01"}, {"parse_attempt_id": "pa_02"}],
        }]}),
        encoding="utf-8",
    )
    append_pipeline_job(
        tmp_path,
        source_id="src_paper",
        job_type="paper_parse_mineru",
        payload={"requested_action": "request_source_reparse"},
    )
    (tmp_path / "task_bridge").mkdir()
    (tmp_path / "task_bridge" / "pending_experiment_task.json").write_text("{}\n", encoding="utf-8")
    transition = append_dialogue_transition(
        tmp_path,
        decision=_decision(),
        summary=ResearchIntentSummary(goal="重新解析论文"),
    )

    projection = build_dialogue_state_projection(tmp_path)

    assert projection.previous_decision == transition
    assert projection.previous_decision is not None
    assert projection.previous_decision.source_permission_decision == "allow"
    assert projection.pending_source_actions[0].action == "request_source_reparse"
    assert projection.task_state == "pending_confirmation"
    assert projection.sources[0].active_parse_attempt_id == "pa_02"
    assert projection.sources[0].parse_attempt_count == 2


def test_confirmed_task_precedes_pending_task_in_projection(tmp_path: Path):
    (tmp_path / "task_bridge").mkdir()
    (tmp_path / "task_bridge" / "pending_experiment_task.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "input_task.yaml").write_text("run_id: run_demo\n", encoding="utf-8")

    assert build_dialogue_state_projection(tmp_path).task_state == "confirmed"
