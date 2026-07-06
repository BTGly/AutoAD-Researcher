"""P0 scenario tests for intent-alignment action repair v1.4."""

from __future__ import annotations

import io
import json
from pathlib import Path
from shutil import copytree

from autoad_researcher.assistant.intent_action import (
    ActionDecision,
    append_action_decision,
    build_paper_artifact_content_preview,
    build_response_context_for_decision,
    build_research_context_snapshot,
    evaluate_paper_artifact_quality,
    has_readable_paper_artifact_content,
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


def test_available_artifacts_include_artifacts_and_parse_outputs(tmp_path):
    run_dir = tmp_path / "run_available_artifacts"
    run_dir.mkdir()
    _write_empty_paper_artifacts(run_dir)
    parse_dir = run_dir / "paper" / "parse"
    parse_dir.mkdir(parents=True)
    (parse_dir / "blocks.jsonl").write_text('{"text":"正文 block"}\n', encoding="utf-8")
    (parse_dir / "sections.json").write_text('{"sections":[{"title":"Method"}]}', encoding="utf-8")

    snapshot, signal, decision = _snapshot_and_signal(run_dir, "基于论文 artifacts 回答")
    context = build_response_context_for_decision(snapshot, decision)

    assert "paper/artifacts/paper_summary.json" in snapshot.available_artifacts
    assert "paper/parse/blocks.jsonl" in snapshot.available_artifacts
    assert "paper/parse/sections.json" in snapshot.available_artifacts
    assert context["facts"]["available_artifacts"] == snapshot.available_artifacts
    assert signal.asks_for_paper_content is True


def test_readable_artifacts_prioritize_structured_outputs_over_blocks(tmp_path):
    run_dir = tmp_path / "run_readable_artifacts"
    run_dir.mkdir()
    artifacts_dir = run_dir / "paper" / "artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "paper_summary.json").write_text(
        json.dumps({
            "title": {"value": "SimpleNet"},
            "abstract": "A paper about anomaly detection.",
            "proposed_method": [{"text": "Use a simple model for industrial anomaly detection."}],
        }),
        encoding="utf-8",
    )
    (artifacts_dir / "paper_reader_result.json").write_text(
        json.dumps({"summary": "Readable reader output"}),
        encoding="utf-8",
    )
    parse_dir = run_dir / "paper" / "parse"
    parse_dir.mkdir(parents=True)
    (parse_dir / "sections.json").write_text(
        json.dumps({"sections": [{"title": "Method", "text": "Readable method section"}]}),
        encoding="utf-8",
    )
    (parse_dir / "blocks.jsonl").write_text(
        json.dumps({"page": 1, "text": "x 350P A]#cS S G"}) + "\n",
        encoding="utf-8",
    )

    snapshot, _signal, decision = _snapshot_and_signal(run_dir, "读论文")
    context = build_response_context_for_decision(snapshot, decision)

    assert "paper/artifacts/paper_summary.json" in context["facts"]["readable_artifacts"]
    assert "paper/artifacts/paper_reader_result.json" in context["facts"]["readable_artifacts"]
    assert "paper/parse/sections.json" in context["facts"]["readable_artifacts"]
    assert "paper/parse/blocks.jsonl" in context["facts"]["available_artifacts"]
    assert "paper/parse/blocks.jsonl" not in context["facts"]["readable_artifacts"]


def test_readable_parse_content_does_not_require_usable_metadata(tmp_path):
    run_dir = tmp_path / "run_partial_parse"
    run_dir.mkdir()
    _write_empty_paper_artifacts(run_dir)

    assert has_readable_paper_artifact_content(run_dir) is False

    parse_dir = run_dir / "paper" / "parse"
    parse_dir.mkdir(parents=True)
    (parse_dir / "blocks.jsonl").write_text(
        json.dumps({"type": "text", "text": "This paper proposes a practical anomaly detection method."}) + "\n",
        encoding="utf-8",
    )

    quality, _warnings = evaluate_paper_artifact_quality(run_dir)
    assert quality == "insufficient"
    assert has_readable_paper_artifact_content(run_dir) is True

    preview = build_paper_artifact_content_preview(run_dir)
    assert "parse_block_snippets" in preview
    assert "anomaly detection method" in preview["parse_block_snippets"][0]


def test_failed_source_with_readable_artifacts_summarizes_instead_of_failed_status(tmp_path):
    run_dir = tmp_path / "run_failed_but_readable"
    run_dir.mkdir()
    append_source_ref(
        run_dir,
        source_id="src_pdf",
        kind="paper_pdf",
        user_label="paper.pdf",
        stored_path="sources/src_pdf/paper.pdf",
        status="failed",
    )
    _write_empty_paper_artifacts(run_dir)
    parse_dir = run_dir / "paper" / "parse"
    parse_dir.mkdir(parents=True)
    (parse_dir / "blocks.jsonl").write_text(
        json.dumps({"type": "text", "text": "The paper introduces a model for industrial anomaly detection."}) + "\n",
        encoding="utf-8",
    )

    snapshot, _signal, decision = _snapshot_and_signal(run_dir, "读论文")
    context = build_response_context_for_decision(snapshot, decision)
    reply = render_response_for_decision(snapshot, decision)

    assert snapshot.has_readable_paper_artifact_content is True
    assert decision.selected_action == "summarize_parsed_artifacts"
    assert decision.response_mode == "parsed_artifact_insufficient"
    assert context["facts"]["has_readable_paper_artifact_content"] is True
    assert "paper_artifact_content_preview" in context["facts"]
    assert "不能基于论文正文" not in reply
    assert "可读取" in reply


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


def test_attachment_only_pdf_auto_parse(tmp_path):
    run_dir = tmp_path / "run_attach"
    run_dir.mkdir()
    info = save_uploaded_file(run_dir, _make_upload("SimpleNet.pdf"))

    attached_sources = [info]
    user_content = f"上传资料：{Path(info['stored_path']).name}"
    snapshot = build_research_context_snapshot(run_dir)
    signal = infer_intent_signal(user_content, snapshot)
    decision = resolve_material_auto_action(
        snapshot=snapshot,
        signal=signal,
        recent_sources=attached_sources,
    )

    assert decision.selected_action == "parse_uploaded_pdf"
    assert decision.stored_path == info["stored_path"]


def test_parse_success_reply_includes_candidate_understanding_or_gaps(tmp_path):
    run_dir = tmp_path / "run_gaps"
    run_dir.mkdir()
    from autoad_researcher.assistant.intent_action import ResearchContextSnapshot
    snapshot = ResearchContextSnapshot(
        run_id="run_gaps",
        has_parsed_artifact=True,
        paper_artifact_quality="usable",
        paper_methods=["SimpleNet", "异常检测"],
        missing_blocking_gaps=["dataset", "primary_metric"],
    )
    decision = ActionDecision(
        snapshot_sha256="aa" * 32,
        selected_action="summarize_parsed_artifacts",
        response_mode="parsed_artifact_summary",
        reason="test",
        execution_status="skipped_by_idempotency",
    )
    reply = render_response_for_decision(snapshot, decision)

    assert "SimpleNet" in reply
    assert "异常检测" in reply
    assert "dataset" in reply or "primary_metric" in reply
    assert "仍缺" in reply


def test_build_response_context_for_decision_contains_policy_fields(tmp_path):
    run_dir = tmp_path / "run_response_context"
    run_dir.mkdir()
    append_source_ref(
        run_dir,
        source_id="src_pdf",
        kind="paper_pdf",
        user_label="paper.pdf",
        stored_path="sources/src_pdf/paper.pdf",
        status="uploaded_not_parsed",
    )
    snapshot = build_research_context_snapshot(run_dir)
    signal = infer_intent_signal("读一下论文", snapshot)
    decision = resolve_material_auto_action(snapshot=snapshot, signal=signal)

    context = build_response_context_for_decision(snapshot, decision)

    assert context["mode"] == decision.response_mode
    assert set(context) == {
        "mode",
        "facts",
        "evidence_boundary",
        "allowed_actions",
        "forbidden_actions",
        "suggested_next_steps",
        "style_constraints",
    }
    assert context["facts"]["source_id"] == "src_pdf"
    assert context["evidence_boundary"]["unparsed_sources"] == ["src_pdf"]
    assert "parse_uploaded_pdf" in context["allowed_actions"]
    assert "runner_execute" in context["forbidden_actions"]
    assert "patch_apply" in context["forbidden_actions"]
    assert "summarize_unparsed_pdf_body" in context["forbidden_actions"]
    assert "parse_selected_pdf" in context["suggested_next_steps"]
    assert "do_not_claim_unparsed_pdf_content" in context["style_constraints"]


def test_render_response_for_decision_preserves_user_visible_return_or_has_fallback(tmp_path):
    from autoad_researcher.assistant.intent_action import ResearchContextSnapshot

    snapshot = ResearchContextSnapshot(run_id="run_render")
    decision = ActionDecision(
        snapshot_sha256="aa" * 32,
        selected_action="answer_directly",
        response_mode="execution_request_blocked",
        reason="test",
        execution_status="blocked_by_policy",
        user_visible_message="blocked visible message",
    )

    assert render_response_for_decision(snapshot, decision) == "blocked visible message"

    fallback = render_response_for_decision(
        snapshot,
        decision.model_copy(update={"user_visible_message": None}),
    )
    assert isinstance(fallback, str)
    assert fallback
    assert "代码修改" in fallback or "实验执行" in fallback


def test_render_response_for_decision_fallback_works(tmp_path):
    from autoad_researcher.assistant.intent_action import ResearchContextSnapshot

    snapshot = ResearchContextSnapshot(run_id="run_render_fallback")
    decision = ActionDecision(
        snapshot_sha256="aa" * 32,
        selected_action="answer_directly",
        response_mode="empty_run_intake",
        reason="test",
        execution_status="skipped_by_idempotency",
    )

    reply = render_response_for_decision(snapshot, decision)

    assert isinstance(reply, str)
    assert reply


def test_ready_for_task_draft_requires_no_blocking_gaps(tmp_path):
    run_dir = tmp_path / "run_draft"
    run_dir.mkdir()
    from autoad_researcher.assistant.intent_action import ResearchContextSnapshot
    snapshot = ResearchContextSnapshot(
        run_id="run_draft",
        has_parsed_artifact=True,
        paper_artifact_quality="usable",
        paper_methods=["SimpleNet"],
        missing_blocking_gaps=["dataset", "baseline_method", "primary_metric", "metric_direction"],
    )
    signal = infer_intent_signal("读一下论文", snapshot)
    assert signal.ready_for_task_draft is False

    snapshot_no_gaps = ResearchContextSnapshot(
        run_id="run_draft2",
        has_parsed_artifact=True,
        paper_artifact_quality="usable",
        paper_methods=["SimpleNet"],
        missing_blocking_gaps=[],
    )
    signal2 = infer_intent_signal("读一下论文", snapshot_no_gaps)
    assert signal2.ready_for_task_draft is True
