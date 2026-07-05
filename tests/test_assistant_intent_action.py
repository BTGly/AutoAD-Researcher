"""P0 scenario tests for intent-alignment action repair v1.4."""

from __future__ import annotations

import io
import json
from pathlib import Path
from shutil import copytree

from autoad_researcher.assistant.intent_action import (
    ActionDecision,
    append_action_decision,
    build_research_context_snapshot,
    evaluate_paper_artifact_quality,
    infer_intent_signal,
    render_response_for_decision,
    resolve_material_auto_action,
)
from autoad_researcher.assistant.task_artifacts import AssistantTaskArtifactService
from autoad_researcher.assistant.probe import silent_probe
from autoad_researcher.assistant.session import AutoADAssistantSession
from autoad_researcher.ui.sources import append_source_ref, save_uploaded_file, update_source_status


FIXTURE = Path("tests/fixtures/silent_probe_fixture")


def _make_upload(name: str, content: bytes = b"%PDF fake"):
    uploaded = io.BytesIO(content)
    uploaded.name = name
    uploaded.getvalue = lambda: content
    return uploaded


def _snapshot_and_signal(run_dir: Path, message: str):
    snapshot = build_research_context_snapshot(run_dir)
    signal = infer_intent_signal(message, snapshot)
    decision = resolve_material_auto_action(snapshot=snapshot, signal=signal)
    return snapshot, signal, decision


def _write_empty_paper_artifacts(run_dir: Path) -> None:
    artifacts = run_dir / "paper" / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "paper_summary.json").write_text(
        json.dumps({
            "schema_version": 1,
            "source_id": "src_bad",
            "title": {"value": ")  W #  p  4&  v  |   V \t  kRLUbC}"},
            "research_problem": [],
            "proposed_method": [],
            "core_components": [],
            "training_objective": [],
            "data_assumptions": [],
            "label_assumptions": [],
            "inference_procedure": [],
            "contributions": [],
            "stated_limitations": [],
            "potential_transfer_points": [],
        }),
        encoding="utf-8",
    )
    (artifacts / "paper_idea_sources.json").write_text("[]", encoding="utf-8")
    (artifacts / "method_components.json").write_text("[]", encoding="utf-8")
    (artifacts / "paper_candidates.json").write_text("[]", encoding="utf-8")


def test_empty_run(tmp_path):
    run_dir = tmp_path / "run_empty"
    run_dir.mkdir()
    snapshot = build_research_context_snapshot(run_dir)
    signal = infer_intent_signal("我想做异常检测，但还没想清楚", snapshot)
    decision = resolve_material_auto_action(snapshot=snapshot, signal=signal)

    assert decision.response_mode == "empty_run_intake"
    assert decision.selected_action == "answer_directly"


def test_reference_identifier(tmp_path):
    run_dir = tmp_path / "run_ref"
    run_dir.mkdir()
    append_source_ref(run_dir, kind="arxiv_id", user_label="2303.15140", stored_path=None, status="user_provided_not_ingested")

    snapshot, _signal, decision = _snapshot_and_signal(run_dir, "论文 SimpleNet arXiv 2303.15140")

    assert snapshot.has_reference_identifier is True
    assert snapshot.has_ingested_source is False
    assert decision.response_mode == "reference_only_status"


def test_uploaded_not_parsed(tmp_path):
    run_dir = tmp_path / "run_pdf"
    run_dir.mkdir()
    save_uploaded_file(run_dir, _make_upload("SimpleNet.pdf"))

    snapshot = build_research_context_snapshot(run_dir)

    assert snapshot.sources[0].derived_status == "uploaded_not_parsed"
    assert snapshot.has_parsed_artifact is False


def test_explicit_parse_trigger(tmp_path):
    run_dir = tmp_path / "run_pdf"
    run_dir.mkdir()
    info = save_uploaded_file(run_dir, _make_upload("SimpleNet.pdf"))
    snapshot = build_research_context_snapshot(run_dir)
    signal = infer_intent_signal(f"读一下 {info['stored_path']}", snapshot)
    decision = resolve_material_auto_action(
        snapshot=snapshot,
        signal=signal,
        explicit_stored_path=str(info["stored_path"]),
    )

    assert decision.selected_action == "parse_uploaded_pdf"
    assert decision.stored_path == info["stored_path"]


def test_artifact_grounded_answer(tmp_path):
    run_dir = tmp_path / "run_known"
    copytree(FIXTURE, run_dir)
    info = save_uploaded_file(run_dir, _make_upload("PatchCore.pdf"))
    update_source_status(run_dir, info["source_id"], "parsed")

    snapshot, _signal, decision = _snapshot_and_signal(run_dir, "你现在基于论文 artifacts 看到了什么")

    assert snapshot.has_parsed_artifact is True
    assert decision.response_mode == "parsed_artifact_summary"


def test_ambiguous_reproduction(tmp_path):
    run_dir = tmp_path / "run_pdf"
    run_dir.mkdir()

    signal = infer_intent_signal("我想复现论文，看看能不能用到我的项目里", build_research_context_snapshot(run_dir))

    assert signal.ambiguous_reproduction_transfer is True


def test_research_task_draft(tmp_path):
    run_dir = tmp_path / "run_known"
    copytree(FIXTURE, run_dir)
    service = AssistantTaskArtifactService(runs_root=tmp_path)
    session = AutoADAssistantSession(session_id="s1", run_id="run_known", mode="intent_structuring")
    what = silent_probe("run_known", runs_root=tmp_path)

    draft, updated = service.create_research_task_draft(
        session=session,
        what_we_know=what,
        metric_command="python eval.py --metric image_auroc",
        metric_name="image_auroc",
        metric_direction="maximize",
        baseline="PatchCore",
        dataset="MVTec AD",
        constraints=[],
        blocking_gaps=[],
    )

    assert draft.confirmation == "draft"
    assert updated.task.ready_for_pipeline is False
    assert updated.task.execution_approved is False
    assert "当前不启动实验" in draft.constraints
    assert "不修改 evaluation 逻辑" in draft.constraints


def test_direct_execution_blocked(tmp_path):
    run_dir = tmp_path / "run_empty"
    run_dir.mkdir()

    _snapshot, _signal, decision = _snapshot_and_signal(run_dir, "直接改代码跑实验")

    assert decision.selected_action == "block_execution_request"
    assert decision.response_mode == "execution_request_blocked"
    assert decision.execution_status == "blocked_by_policy"


def test_confirmation_not_execution(tmp_path):
    run_dir = tmp_path / "run_empty"
    run_dir.mkdir()

    snapshot, signal, decision = _snapshot_and_signal(run_dir, "这个研究目标草案我确认")
    reply = render_response_for_decision(snapshot, decision)

    assert signal.confirms_research_task is True
    assert decision.selected_action == "confirm_research_task"
    assert "不代表已经批准代码修改或实验执行" in reply


def test_auto_parse_trigger(tmp_path):
    run_dir = tmp_path / "run_pdf"
    run_dir.mkdir()
    info = save_uploaded_file(run_dir, _make_upload("SimpleNet.pdf"))

    _snapshot, signal, decision = _snapshot_and_signal(run_dir, "你看看这个 PDF")

    assert signal.asks_for_paper_content is True
    assert decision.selected_action == "parse_uploaded_pdf"
    assert decision.stored_path == info["stored_path"]


def test_multi_pdf_ambiguity(tmp_path):
    run_dir = tmp_path / "run_multi"
    run_dir.mkdir()
    save_uploaded_file(run_dir, _make_upload("A.pdf"))
    save_uploaded_file(run_dir, _make_upload("B.pdf"))

    _snapshot, _signal, decision = _snapshot_and_signal(run_dir, "帮我看看这篇论文")

    assert decision.selected_action == "ask_blocking_gap"
    assert decision.response_mode == "select_pdf_to_parse"
    assert decision.execution_status == "needs_user_input"


def test_parse_auto_continue(tmp_path):
    run_dir = tmp_path / "run_known"
    copytree(FIXTURE, run_dir)

    snapshot = build_research_context_snapshot(run_dir)

    assert (run_dir / "ui_chat" / "research_context_snapshot.json").is_file()
    assert snapshot.paper_artifact_quality == "usable"
    assert snapshot.has_parsed_artifact is True


def test_auto_parse_idempotent(tmp_path):
    run_dir = tmp_path / "run_pdf"
    run_dir.mkdir()
    info = save_uploaded_file(run_dir, _make_upload("SimpleNet.pdf"))
    update_source_status(run_dir, info["source_id"], "parsing")

    _snapshot, _signal, decision = _snapshot_and_signal(run_dir, "你看看这个 PDF")

    assert decision.selected_action == "answer_directly"
    assert decision.response_mode == "parsing_in_progress_status"
    assert decision.execution_status == "skipped_by_idempotency"


def test_parse_failure_path(tmp_path):
    run_dir = tmp_path / "run_bad"
    run_dir.mkdir()
    info = save_uploaded_file(run_dir, _make_upload("Broken.pdf"))
    update_source_status(run_dir, info["source_id"], "parsed")
    _write_empty_paper_artifacts(run_dir)

    quality, warnings = evaluate_paper_artifact_quality(run_dir)
    snapshot, _signal, decision = _snapshot_and_signal(run_dir, "基于论文 artifacts 回答")

    assert quality == "insufficient"
    assert "paper_artifacts_exist_but_no_extractable_claims" in warnings
    assert snapshot.has_parsed_artifact is False
    assert decision.response_mode == "parsed_artifact_insufficient"


def test_repo_no_auto_clone(tmp_path):
    run_dir = tmp_path / "run_repo"
    run_dir.mkdir()
    append_source_ref(
        run_dir,
        kind="github_repo",
        user_label="DonaldRR/SimpleNet",
        stored_path=None,
        status="user_provided_not_ingested",
    )

    snapshot, signal, decision = _snapshot_and_signal(run_dir, "看看这个 GitHub 仓库 DonaldRR/SimpleNet")

    assert signal.mentions_repo is True
    assert snapshot.has_repo_evidence is False
    assert decision.selected_action == "answer_directly"
    assert decision.response_mode == "reference_only_status"


def test_action_decisions_jsonl(tmp_path):
    run_dir = tmp_path / "run_audit"
    run_dir.mkdir()
    decision = ActionDecision(
        snapshot_sha256="0" * 64,
        selected_action="answer_directly",
        response_mode="empty_run_intake",
        reason="test",
        execution_status="skipped_by_idempotency",
    )

    path = append_action_decision(run_dir, decision, user_message_id="msg_1")
    payload = json.loads(path.read_text(encoding="utf-8").splitlines()[0])

    assert payload["user_message_id"] == "msg_1"
    assert payload["selected_action"] == "answer_directly"
