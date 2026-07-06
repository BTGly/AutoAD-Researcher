"""Tests for research_chat.py safety boundaries."""

import json
from pathlib import Path

from autoad_researcher.ui.chat_prompts import INTENT_CLARIFICATION_PROMPT
from autoad_researcher.assistant.intent_action import ActionDecision
from autoad_researcher.ui.intent_draft import (
    ResearchIntentDraft,
    save_clarification_input,
    save_intent_confirmation,
    save_intent_draft,
)
from autoad_researcher.ui.research_chat import (
    _MODE_LABELS,
    _SAFETY_WARNING,
    _chat_input_submission,
    _execute_or_report_pdf_parse_action,
    _extract_intent_draft,
    _split_visible_transcript,
    build_research_chat_messages,
    build_freeze_panel_state,
    build_developer_info_payload,
    build_pipeline_input_action,
    build_research_assistant_overview,
    build_source_card_rows,
    build_user_flow_steps,
    render_intent_draft_markdown,
)
from autoad_researcher.ui.task_profile import TaskProfile, save_task_profile
from autoad_researcher.core.events import EventStore
from autoad_researcher.research_context.freeze import freeze_context
from autoad_researcher.ui.sources import append_source_ref, load_source_registry


def test_safety_warning_not_empty():
    assert len(_SAFETY_WARNING) > 10
    assert "不会修改" in _SAFETY_WARNING or "不" in _SAFETY_WARNING


def test_mode_labels_have_all_modes():
    assert set(_MODE_LABELS.keys()) == {
        "intent_clarification", "run_explanation", "next_experiment",
    }


def test_mode_labels_are_chinese():
    for label in _MODE_LABELS.values():
        assert any('\u4e00' <= c <= '\u9fff' for c in label)


def test_extract_intent_draft_parses_research_goal():
    text = """研究目标：降低 PatchCore 运行时峰值显存。

优化指标：
- peak_gpu_memory_mb
- wall_time_seconds

禁止修改范围：
- configs/
- tests/
"""
    draft = _extract_intent_draft(text)
    assert "PatchCore" in draft["research_goal"]
    assert "peak_gpu_memory_mb" in draft["primary_metrics"]
    assert "configs/" in draft["forbidden_change_scope"]


def test_extract_intent_draft_empty_for_none():
    text = "hello world"
    draft = _extract_intent_draft(text)
    assert draft["research_goal"] == ""
    assert draft["primary_metrics"] == []


def test_extract_intent_draft_always_returns_all_keys():
    draft = _extract_intent_draft("")
    expected = {"research_goal", "primary_metrics", "guardrail_metrics",
                "allowed_change_scope", "forbidden_change_scope",
                "success_criteria", "constraints", "user_idea"}
    assert set(draft.keys()) == expected


def _draft(run_id: str = "run_20260704_0250_ee23") -> ResearchIntentDraft:
    return ResearchIntentDraft(
        run_id=run_id,
        research_goal="在 MVTec AD bottle 类别上复现 PatchCore baseline。",
        problem_type="accuracy_improvement",
        primary_metrics=["instance_auroc", "full_pixel_auroc"],
        guardrail_metrics=["anomaly_pixel_auroc"],
        allowed_change_scope=["PatchCore sampler / coreset 相关逻辑", "训练与推理脚本"],
        forbidden_change_scope=["测试代码", "评估器"],
        benchmark_scope={"dataset": "MVTec AD bottle", "baseline": "PatchCore"},
        success_criteria="运行完整、日志可追溯，指标与 baseline 结果进行对比。",
        risks=[],
        open_questions=[],
    )


def test_transcript_display_helper_does_not_require_mode_label():
    # The UI renderer now writes chat content directly; mode remains stored only in transcript metadata.
    source = Path("src/autoad_researcher/ui/research_chat.py").read_text(encoding="utf-8")
    assert "[{entry_mode}]" not in source
    assert "[{mode}]" not in source


def test_transcript_display_defaults_to_recent_tail():
    transcript = [{"role": "user", "content": f"m{i}"} for i in range(12)]

    older, visible = _split_visible_transcript(transcript)

    assert len(older) == 4
    assert len(visible) == 8
    assert visible[0]["content"] == "m4"
    assert visible[-1]["content"] == "m11"


def test_research_chat_ui_keeps_upload_button_primary_and_path_advanced():
    source = Path("src/autoad_researcher/ui/research_chat.py").read_text(encoding="utf-8")

    assert "st.file_uploader" in source
    assert "添加到当前任务" in source
    assert "accept_file" in source
    assert "高级：从服务器路径添加" in source
    assert "st.checkbox" in source
    assert source.index("st.file_uploader") < source.index("服务器本地文件路径")


def test_sources_expander_does_not_nest_expanders():
    source = Path("src/autoad_researcher/ui/research_chat.py").read_text(encoding="utf-8")
    start = source.index('with st.expander("📎 当前资料 / Sources", expanded=False):')
    end = source.index("    _render_source_cards_panel(run_dir)")
    sources_block = source[start:end]

    assert sources_block.count("with st.expander(") == 1


def test_chat_input_file_support_is_signature_compatible(monkeypatch):
    import autoad_researcher.ui.research_chat as research_chat

    class OldSt:
        def __init__(self):
            self.calls = []

        def chat_input(self, placeholder, *, key=None):
            self.calls.append({"placeholder": placeholder, "key": key})
            return "ok"

    old_st = OldSt()
    monkeypatch.setattr(research_chat, "st", old_st)
    assert _chat_input_submission() == "ok"
    assert old_st.calls[0]["key"] == "_chat_input"
    assert "accept_file" not in old_st.calls[0]

    class NewSt:
        def __init__(self):
            self.calls = []

        def chat_input(self, placeholder, *, key=None, accept_file=None, file_type=None):
            self.calls.append({
                "placeholder": placeholder,
                "key": key,
                "accept_file": accept_file,
                "file_type": file_type,
            })
            return {"text": "ok", "files": []}

    new_st = NewSt()
    monkeypatch.setattr(research_chat, "st", new_st)
    assert _chat_input_submission() == {"text": "ok", "files": []}
    assert new_st.calls[0]["accept_file"] == "multiple"
    assert "pdf" in new_st.calls[0]["file_type"]


def test_research_chat_ui_does_not_render_developer_info_on_main_page():
    source = Path("src/autoad_researcher/ui/research_chat.py").read_text(encoding="utf-8")
    render_body = source[source.index("def render_research_chat():"):source.index("def _resolve_run_dir")]

    assert "_render_developer_info(" not in render_body


def test_research_chat_messages_include_identity_and_approval_role(tmp_path: Path):
    run_dir = tmp_path / "run_identity"
    run_dir.mkdir()

    messages = build_research_chat_messages(
        run_dir=run_dir,
        mode="intent_clarification",
        user_input="读论文",
        context_data={},
        transcript_tail=[],
    )
    system_text = "\n".join(m["content"] for m in messages if m["role"] == "system")

    assert "AutoAD Research Assistant" in system_text
    assert "读清楚论文" in system_text
    assert "请求用户审批" in system_text
    assert "web_search" in system_text
    assert "git_clone" in system_text


def test_user_friendly_intent_draft_markdown_has_no_json_indices():
    markdown = render_intent_draft_markdown(_draft())

    assert "0:" not in markdown
    assert "1:" not in markdown
    assert "图像级 AUROC：instance_auroc" in markdown
    assert "像素级 AUROC：full_pixel_auroc" in markdown
    assert "ui_chat/intent_draft.json" not in markdown


def test_pipeline_input_action_hides_artifact_status_table(tmp_path: Path):
    run_dir = tmp_path / "run_20260704_0250_ee23"

    action = build_pipeline_input_action(run_dir)

    assert action["button_enabled"] is False
    message = action["message"]
    assert "请先确认研究目标" in message
    assert "clarification_input.json" not in message
    assert "intent_confirmation.json" not in message
    assert "input_task.yaml" not in message


def test_pipeline_input_action_enables_after_confirmed_intent(tmp_path: Path):
    run_dir = tmp_path / "run_20260704_0250_ee23"
    draft = _draft(run_id=run_dir.name)
    save_intent_draft(run_dir, draft)
    save_clarification_input(run_dir, draft)
    save_intent_confirmation(run_dir, decision="approved")

    action = build_pipeline_input_action(run_dir)

    assert action["button_enabled"] is True
    assert action["message"] == "研究目标已确认。下一步可以生成实验输入。"


def test_user_flow_hides_raw_gate_stage_names(tmp_path: Path):
    run_dir = tmp_path / "run_20260704_0250_ee23"

    steps = build_user_flow_steps(run_dir)
    text = json.dumps(steps, ensure_ascii=False)

    assert "patch_planner" not in text
    assert "runner_execute" not in text
    assert steps[0]["label"] == "确认研究目标"
    assert steps[0]["state"] == "current"


def test_overview_hides_raw_ids_but_developer_info_keeps_them(tmp_path: Path):
    run_dir = tmp_path / "run_20260704_0250_ee23"
    save_task_profile(
        run_dir,
        TaskProfile(
            run_id=run_dir.name,
            task_title="PatchCore 基线复现",
            task_summary="在 MVTec AD bottle 上复现 PatchCore baseline。",
            source="manual",
        ),
    )
    context = {"available_stages": ["stage3_acceptance"]}

    overview = build_research_assistant_overview(
        run_dir,
        dataset_root="/root/autodl-tmp/mvtec",
        provider_url="https://api.deepseek.com",
        context_data=context,
    )
    public_text = json.dumps({k: v for k, v in overview.items() if k != "developer"}, ensure_ascii=False)
    developer = build_developer_info_payload(
        run_dir,
        overview=overview,
        provider_url="https://api.deepseek.com",
        dataset_root="/root/autodl-tmp/mvtec",
        context_data=context,
    )

    assert "run_20260704_0250_ee23" not in public_text
    assert "/root/autodl-tmp/mvtec" not in public_text
    assert "stage3_acceptance" not in public_text
    assert overview["task_title"] == "PatchCore 基线复现"
    assert overview["dataset_status"] == "已配置"
    assert developer["run_id"] == "run_20260704_0250_ee23"
    assert "ui_chat/intent_draft.json" in developer["raw_artifacts"]
    assert "approval_gate_report.json" in developer["raw_artifacts"]


def test_overview_bad_task_profile_falls_back_without_crashing(tmp_path: Path):
    run_dir = tmp_path / "run_bad_profile"
    profile_path = run_dir / "ui_chat" / "task_profile.json"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text("{not json", encoding="utf-8")

    overview = build_research_assistant_overview(
        run_dir,
        dataset_root="/root/autodl-tmp/mvtec",
        provider_url="https://api.deepseek.com",
        context_data=None,
    )

    assert overview["task_title"] == "run_bad_profile"
    assert overview["task_summary"]
    assert overview["developer"]["run_id"] == "run_bad_profile"


def test_missing_patch_approval_request_raw_warning_removed():
    source = Path("src/autoad_researcher/ui/research_chat.py").read_text(encoding="utf-8")
    assert "尚无 patch_planner_approval_request.json" not in source
    assert "尚无 patch_runner_handoff.json" not in source


def test_intent_prompt_discourages_unsupported_hard_thresholds():
    assert "不要无依据地给出硬阈值" in INTENT_CLARIFICATION_PROMPT
    assert "只有 WhatWeKnow 或用户明确提供数值时" in INTENT_CLARIFICATION_PROMPT


def test_research_chat_action_guard_rejects_non_whitelisted_execution(tmp_path: Path, monkeypatch):
    import autoad_researcher.ui.research_chat as research_chat

    class StStub:
        def warning(self, _message: str) -> None:
            return None

    run_dir = tmp_path / "run_guard"
    run_dir.mkdir()
    monkeypatch.setattr(research_chat, "st", StStub())

    reply = _execute_or_report_pdf_parse_action(
        run_dir,
        {"action": "runner_execute", "message": "run benchmark"},
    )

    assert "工具隔离已拒绝" in reply
    events = EventStore(runs_root=tmp_path).read_events("run_guard")
    assert events[-1].event_type == "tool_guard_rejected"
    assert events[-1].payload["action"] == "runner_execute"


def test_parse_partial_artifacts_use_natural_reply_without_marking_source_failed(tmp_path: Path, monkeypatch):
    import autoad_researcher.ui.research_chat as research_chat

    class SpinnerStub:
        def __enter__(self):
            return None

        def __exit__(self, _exc_type, _exc, _tb):
            return False

    class StStub:
        def spinner(self, _message: str):
            return SpinnerStub()

        def warning(self, _message: str) -> None:
            return None

        def success(self, _message: str) -> None:
            return None

        def error(self, _message: str) -> None:
            return None

    run_dir = tmp_path / "run_partial_parse"
    run_dir.mkdir()
    pdf_path = run_dir / "sources" / "src_pdf" / "paper.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_text("fake pdf", encoding="utf-8")
    append_source_ref(
        run_dir,
        source_id="src_pdf",
        kind="paper_pdf",
        user_label="paper.pdf",
        stored_path="sources/src_pdf/paper.pdf",
        status="uploaded_not_parsed",
    )

    def fake_run(_run_id, _pdf_path):
        parse_dir = run_dir / "paper" / "parse"
        parse_dir.mkdir(parents=True)
        (parse_dir / "blocks.jsonl").write_text(
            json.dumps({"type": "text", "text": "This paper proposes a practical anomaly detection method."}) + "\n",
            encoding="utf-8",
        )
        return {"status": "parsed"}

    captured_decisions: list[ActionDecision] = []

    def fake_natural_reply(*, run_dir, decision, api_key, provider_url, user_input, history_tail=None):
        captured_decisions.append(decision)
        return "natural partial reply"

    monkeypatch.setattr(research_chat, "st", StStub())
    monkeypatch.setattr(research_chat, "_run_paper_intelligence", fake_run)
    monkeypatch.setattr(research_chat, "_natural_reply_for_decision", fake_natural_reply)

    decision = ActionDecision(
        snapshot_sha256="aa" * 32,
        selected_action="parse_uploaded_pdf",
        response_mode="material_auto_parse_started",
        reason="test",
        source_id="src_pdf",
        stored_path="sources/src_pdf/paper.pdf",
    )
    reply = _execute_or_report_pdf_parse_action(
        run_dir,
        {
            "action": "parse",
            "pdf_path": str(pdf_path),
            "source_id": "src_pdf",
            "action_decision": decision,
        },
        api_key="sk-test",
        provider_url="https://test",
        user_input="读一下这篇论文",
    )

    registry = load_source_registry(run_dir)
    assert reply == "natural partial reply"
    assert registry["sources"][0]["status"] == "parsed"
    assert "error_message" not in registry["sources"][0]
    assert captured_decisions[-1].execution_status == "executed_success"
    assert captured_decisions[-1].response_mode == "parsed_artifact_insufficient"
    assert captured_decisions[-1].error_code == "PAPER_ARTIFACTS_PARTIAL_METADATA"


def test_parse_failure_uses_natural_reply_with_history_path(tmp_path: Path, monkeypatch):
    import autoad_researcher.ui.research_chat as research_chat

    class SpinnerStub:
        def __enter__(self):
            return None

        def __exit__(self, _exc_type, _exc, _tb):
            return False

    class StStub:
        def spinner(self, _message: str):
            return SpinnerStub()

        def warning(self, _message: str) -> None:
            return None

        def success(self, _message: str) -> None:
            return None

        def error(self, _message: str) -> None:
            return None

    run_dir = tmp_path / "run_parse_failed"
    run_dir.mkdir()
    pdf_path = run_dir / "sources" / "src_pdf" / "paper.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_text("fake pdf", encoding="utf-8")
    append_source_ref(
        run_dir,
        source_id="src_pdf",
        kind="paper_pdf",
        user_label="paper.pdf",
        stored_path="sources/src_pdf/paper.pdf",
        status="uploaded_not_parsed",
    )

    captured_decisions: list[ActionDecision] = []

    def fake_natural_reply(*, run_dir, decision, api_key, provider_url, user_input, history_tail=None):
        captured_decisions.append(decision)
        return "natural failure reply"

    monkeypatch.setattr(research_chat, "st", StStub())
    monkeypatch.setattr(research_chat, "_run_paper_intelligence", lambda _run_id, _pdf_path: {"status": "failed", "error": "bad pdf"})
    monkeypatch.setattr(research_chat, "_natural_reply_for_decision", fake_natural_reply)

    decision = ActionDecision(
        snapshot_sha256="aa" * 32,
        selected_action="parse_uploaded_pdf",
        response_mode="material_auto_parse_started",
        reason="test",
        source_id="src_pdf",
        stored_path="sources/src_pdf/paper.pdf",
    )
    reply = _execute_or_report_pdf_parse_action(
        run_dir,
        {
            "action": "parse",
            "pdf_path": str(pdf_path),
            "source_id": "src_pdf",
            "action_decision": decision,
        },
        api_key="sk-test",
        provider_url="https://test",
        user_input="读一下这篇论文",
    )

    registry = load_source_registry(run_dir)
    assert reply == "natural failure reply"
    assert registry["sources"][0]["status"] == "failed"
    assert registry["sources"][0]["error_message"] == "bad pdf"
    assert captured_decisions[-1].execution_status == "executed_failed"
    assert captured_decisions[-1].response_mode == "parsing_failed_status"
    assert captured_decisions[-1].error_code == "PAPER_PARSE_FAILED"


def test_source_cards_show_status_and_parse_attempts(tmp_path: Path):
    run_dir = tmp_path / "run_source_cards"
    run_dir.mkdir()
    append_source_ref(
        run_dir,
        source_id="src_pdf",
        kind="paper_pdf",
        user_label="paper.pdf",
        stored_path="sources/src_pdf/paper.pdf",
        status="parsed",
        active_parse_attempt_id="pa_000001",
        parse_attempts=[
            {"parse_attempt_id": "pa_000001", "status": "ok", "parser": "mineru_pipeline_v1"},
            {"parse_attempt_id": "pa_000002", "status": "failed", "parser": "mineru_pipeline_v1"},
        ],
    )

    rows = build_source_card_rows(run_dir)

    assert rows[0]["source_id"] == "src_pdf"
    assert rows[0]["active_parse_attempt_id"] == "pa_000001"
    assert rows[0]["parse_attempt_count"] == 2
    assert rows[0]["attempts"][0]["active"] is True


def test_freeze_panel_shows_active_freeze_version(tmp_path: Path):
    run_dir = tmp_path / "run_freeze_panel"
    run_dir.mkdir()
    (run_dir / "context").mkdir()
    (run_dir / "context" / "research_context_draft.json").write_text(
        json.dumps({
            "schema_version": 1,
            "run_id": run_dir.name,
            "context_id": f"ctx_{run_dir.name}_0",
            "context_version": 0,
            "task": {"task_id": f"task_{run_dir.name}", "goal": "test"},
            "sources": {},
            "facts": [],
            "gaps": [],
            "conflicts": [],
            "readiness": {"status": "needs_clarification", "next_stage": "3.3_context_repair"},
            "context_sha256": "0" * 64,
        }),
        encoding="utf-8",
    )

    before = build_freeze_panel_state(run_dir)
    freeze_context(run_dir)
    after = build_freeze_panel_state(run_dir)

    assert before["button_enabled"] is True
    assert after["active_freeze_version"] == "fv_001"
    assert after["freezes"][0]["freeze_version"] == "fv_001"
