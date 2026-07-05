"""Tests for Round 6 assistant task draft and confirmation artifacts."""

import json
from datetime import datetime, timezone

from autoad_researcher.assistant.events import AssistantEvent
from autoad_researcher.assistant.probe import WhatWeKnow
from autoad_researcher.assistant.session import AutoADAssistantSession
from autoad_researcher.assistant.task_artifacts import (
    ASSISTANT_UNDERSTANDING_ARTIFACT,
    TASK_CONFIRMED_JSON_ARTIFACT,
    TASK_DRAFT_JSON_ARTIFACT,
    TASK_DRAFT_MD_ARTIFACT,
    USER_CORRECTIONS_ARTIFACT,
    WHAT_WE_KNOW_ARTIFACT,
    AssistantTaskArtifactService,
    AssistantUnderstandingRecord,
    REQUIRED_TASK_BOUNDARY_CONSTRAINTS,
)


def _session() -> AutoADAssistantSession:
    return AutoADAssistantSession(session_id="s1", run_id="run_001", mode="intent_structuring")


def _what() -> WhatWeKnow:
    return WhatWeKnow(
        run_id="run_001",
        has_baseline_contract=True,
        has_paper_artifacts=True,
        baseline_method="PatchCore",
        dataset="MVTec AD",
        evidence_artifacts=["baseline_architecture_contract.json"],
        missing_fields=["category", "metric_direction"],
    )


def test_create_research_task_draft_writes_json_md_and_session(tmp_path):
    service = AssistantTaskArtifactService(runs_root=tmp_path)

    draft, session = service.create_research_task_draft(
        session=_session(),
        what_we_know=_what(),
        metric_command="python eval.py --metric image_auroc",
        metric_name="image_auroc",
        metric_direction="maximize",
        constraints=["不改 eval 脚本"],
        user_idea="提升异常检测效果",
    )

    run_dir = tmp_path / "run_001"
    assert (run_dir / WHAT_WE_KNOW_ARTIFACT).is_file()
    assert (run_dir / TASK_DRAFT_JSON_ARTIFACT).is_file()
    assert (run_dir / TASK_DRAFT_MD_ARTIFACT).is_file()
    assert draft.baseline == "PatchCore"
    assert draft.dataset == "MVTec AD"
    assert draft.confirmation == "draft"
    assert "不改 eval 脚本" in draft.constraints
    for constraint in REQUIRED_TASK_BOUNDARY_CONSTRAINTS:
        assert constraint in draft.constraints
    assert session.mode == "task_confirmation"
    assert session.task.draft_ref == TASK_DRAFT_JSON_ARTIFACT.as_posix()
    assert session.task.ready_for_pipeline is False
    assert session.task.execution_approved is False


def test_create_research_task_draft_does_not_allow_method_fields(tmp_path):
    service = AssistantTaskArtifactService(runs_root=tmp_path)
    draft, _ = service.create_research_task_draft(
        session=_session(),
        what_we_know=_what(),
        metric_command="python eval.py",
        metric_name="image_auroc",
        metric_direction="maximize",
    )

    payload = json.loads((tmp_path / "run_001" / TASK_DRAFT_JSON_ARTIFACT).read_text())

    assert "method" not in payload
    assert "algorithm" not in payload
    assert "hyperparameters" not in payload
    assert "variant_choice" not in payload
    assert draft.scope == "mixed"


def test_create_research_task_draft_dedupes_required_boundary_constraints(tmp_path):
    service = AssistantTaskArtifactService(runs_root=tmp_path)

    draft, _ = service.create_research_task_draft(
        session=_session(),
        what_we_know=_what(),
        metric_command="python eval.py",
        metric_name="image_auroc",
        metric_direction="maximize",
        constraints=["当前不启动实验", "当前不启动实验"],
    )

    assert draft.constraints.count("当前不启动实验") == 1
    assert "不修改 evaluation 逻辑" in draft.constraints


def test_append_user_correction_and_understanding(tmp_path):
    service = AssistantTaskArtifactService(runs_root=tmp_path)
    event = AssistantEvent(
        event_id="ev_fix",
        event_type="user_input",
        payload={"text": "不是，我想先明确指标"},
        router_labels=["correction"],
    )
    understanding = AssistantUnderstandingRecord(
        run_id="run_001",
        summary="用户纠正为先明确指标。",
        missing_fields=["metric_direction"],
        evidence_artifacts=["conversation/user_corrections.jsonl"],
    )

    service.append_user_correction("run_001", event)
    service.append_assistant_understanding(understanding)

    run_dir = tmp_path / "run_001"
    assert (run_dir / USER_CORRECTIONS_ARTIFACT).is_file()
    assert (run_dir / ASSISTANT_UNDERSTANDING_ARTIFACT).is_file()
    correction_line = json.loads((run_dir / USER_CORRECTIONS_ARTIFACT).read_text().splitlines()[0])
    assert correction_line["event"]["router_labels"] == ["correction"]



def test_create_draft_with_blocking_gaps_keeps_session_blocked(tmp_path):
    service = AssistantTaskArtifactService(runs_root=tmp_path)

    draft, session = service.create_research_task_draft(
        session=_session(),
        what_we_know=_what(),
        metric_command="python eval.py",
        metric_name="image_auroc",
        metric_direction="maximize",
        blocking_gaps=["category"],
    )

    assert draft.confirmation == "draft"
    assert session.mode == "task_confirmation"
    assert session.task.has_blocking_gaps is True
    assert session.task.ready_for_pipeline is False


def test_confirm_research_task_rejects_blocking_gaps(tmp_path):
    service = AssistantTaskArtifactService(runs_root=tmp_path)
    draft, session = service.create_research_task_draft(
        session=_session(),
        what_we_know=_what(),
        metric_command="python eval.py",
        metric_name="image_auroc",
        metric_direction="maximize",
        blocking_gaps=["category"],
    )

    try:
        service.confirm_research_task(
            session=session,
            draft=draft,
            confirmation_evidence_id="ev_user_confirmed",
        )
    except ValueError as exc:
        assert "blocking gaps" in str(exc)
    else:
        raise AssertionError("expected blocking gaps to prevent confirmation")


def test_confirm_research_task_rejects_empty_confirmation_evidence(tmp_path):
    service = AssistantTaskArtifactService(runs_root=tmp_path)
    draft, session = service.create_research_task_draft(
        session=_session(),
        what_we_know=_what(),
        metric_command="python eval.py",
        metric_name="image_auroc",
        metric_direction="maximize",
    )

    try:
        service.confirm_research_task(
            session=session,
            draft=draft,
            confirmation_evidence_id="  ",
        )
    except ValueError as exc:
        assert "confirmation_evidence_id" in str(exc)
    else:
        raise AssertionError("expected empty confirmation evidence to be rejected")


def test_confirm_research_task_writes_confirmed_json_and_sets_ready_only(tmp_path):
    service = AssistantTaskArtifactService(runs_root=tmp_path)
    draft, session = service.create_research_task_draft(
        session=_session(),
        what_we_know=_what(),
        metric_command="python eval.py",
        metric_name="image_auroc",
        metric_direction="maximize",
    )
    confirmed_at = datetime(2026, 7, 4, tzinfo=timezone.utc)

    confirmed, updated = service.confirm_research_task(
        session=session,
        draft=draft,
        confirmation_evidence_id="ev_user_confirmed",
        confirmed_at=confirmed_at,
    )

    confirmed_path = tmp_path / "run_001" / TASK_CONFIRMED_JSON_ARTIFACT
    payload = json.loads(confirmed_path.read_text())
    assert confirmed.confirmation == "confirmed"
    assert payload["confirmation"] == "confirmed"
    assert payload["confirmation_evidence_id"] == "ev_user_confirmed"
    assert updated.mode == "pipeline_ready"
    assert updated.task.confirmed_ref == TASK_CONFIRMED_JSON_ARTIFACT.as_posix()
    assert updated.task.ready_for_pipeline is True
    assert updated.task.execution_approved is False


def test_confirm_research_task_rejects_run_mismatch(tmp_path):
    service = AssistantTaskArtifactService(runs_root=tmp_path)
    draft, _ = service.create_research_task_draft(
        session=_session(),
        what_we_know=_what(),
        metric_command="python eval.py",
        metric_name="image_auroc",
        metric_direction="maximize",
    )
    other_session = AutoADAssistantSession(session_id="s2", run_id="other_run")

    try:
        service.confirm_research_task(
            session=other_session,
            draft=draft,
            confirmation_evidence_id="ev_user_confirmed",
        )
    except ValueError as exc:
        assert "run_id" in str(exc)
    else:
        raise AssertionError("expected run_id mismatch to be rejected")


def test_create_draft_requires_baseline_from_user_or_what_we_know(tmp_path):
    service = AssistantTaskArtifactService(runs_root=tmp_path)
    what = WhatWeKnow(run_id="run_001")

    try:
        service.create_research_task_draft(
            session=_session(),
            what_we_know=what,
            metric_command="python eval.py",
            metric_name="image_auroc",
            metric_direction="maximize",
        )
    except ValueError as exc:
        assert "baseline" in str(exc)
    else:
        raise AssertionError("expected missing baseline to be rejected")
