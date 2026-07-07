"""Tests for Research Chat material request handling."""

from pathlib import Path

from autoad_researcher.assistant.research_context_builder import ResearchChatEvidenceContext
from autoad_researcher.assistant.response_guard import guard_research_chat_reply
from autoad_researcher.ui.material_requests import (
    append_material_request,
    build_material_request_reply,
    build_material_request_rows,
    classify_material_request_kind,
    detect_material_request_intent,
    load_material_requests,
    update_material_request_status,
)
from autoad_researcher.ui.research_chat import build_research_chat_messages


def test_detect_material_request_intent_for_search_and_repo_requests():
    assert detect_material_request_intent("搜索 MVTec AD 上能迁移到 PatchCore 的最新方法")
    assert detect_material_request_intent("你能网络上搜索欧氏距离应用到异常检测的方法吗")
    assert detect_material_request_intent("找一下官方代码仓库")
    assert not detect_material_request_intent("mvtec，baseline 是 patchcore")

    assert classify_material_request_kind("找一下官方代码仓库") == "repository_discovery"
    assert classify_material_request_kind("搜索最新 SOTA 方法") == "web_search"


def test_append_material_request_writes_pending_jsonl(tmp_path: Path):
    run_dir = tmp_path / "run_requests"
    run_dir.mkdir()

    request = append_material_request(run_dir, user_message="搜索 PatchCore 可迁移改进")

    assert request["request_id"] == "mr_000001"
    assert request["status"] == "pending"
    assert request["kind"] == "web_search"
    loaded = load_material_requests(run_dir)
    rows = build_material_request_rows(run_dir)
    assert loaded[0]["user_message"] == "搜索 PatchCore 可迁移改进"
    assert rows[0]["request_id"] == "mr_000001"
    assert "不会在后台静默执行网络搜索" in build_material_request_reply(request)


def test_update_material_request_status_rewrites_existing_request(tmp_path: Path):
    run_dir = tmp_path / "run_requests"
    run_dir.mkdir()
    append_material_request(run_dir, user_message="搜索 PatchCore 可迁移改进")

    updated = update_material_request_status(
        run_dir,
        request_id="mr_000001",
        status="search_unavailable",
        error_message="web_search provider is not configured",
    )

    loaded = load_material_requests(run_dir)
    rows = build_material_request_rows(run_dir)
    assert updated is not None
    assert len(loaded) == 1
    assert loaded[0]["status"] == "search_unavailable"
    assert loaded[0]["error_message"] == "web_search provider is not configured"
    assert rows[0]["status"] == "search_unavailable"


def test_research_chat_prompt_forbids_background_search_promises(tmp_path: Path):
    run_dir = tmp_path / "run_prompt"
    run_dir.mkdir()

    messages = build_research_chat_messages(
        run_dir=run_dir,
        mode="intent_clarification",
        user_input="搜索 MVTec AD 最新方法",
        context_data={},
        transcript_tail=[],
    )
    system_text = "\n".join(m["content"] for m in messages if m["role"] == "system")

    assert "Material acquisition boundary" in system_text
    assert "no background worker" in system_text
    assert "candidate_source_only" in system_text
    assert "search_unavailable" in system_text


def test_response_guard_rewrites_background_search_promise():
    guarded = guard_research_chat_reply(
        reply="我现在开始搜索 MVTec AD 的最新 SOTA 方法，预计 5-10 分钟完成后主动回复你。",
        user_input="你想办法给我一些能迁移的方法",
        evidence_context=ResearchChatEvidenceContext(),
    )

    assert "background_material_acquisition_promise" in guarded.violations
    assert "不能在后台执行网络搜索" in guarded.reply
    assert "资料搜集请求" in guarded.reply


def test_handle_chat_input_intercepts_material_request_before_llm_call():
    source = Path("src/autoad_researcher/ui/research_chat.py").read_text(encoding="utf-8")
    body = source[source.index("def _handle_chat_input("):source.index("def _chat_input_submission(")]
    assert "detect_sync_web_search_intent(user_input)" in body
    assert body.index("detect_sync_web_search_intent(user_input)") < body.index("build_pdf_parse_action(run_dir, user_input")
    assert "detect_material_request_intent(user_input)" in body
    assert body.index("detect_material_request_intent(user_input)") < body.index("call_research_chat(")


def test_material_request_panel_can_execute_pending_web_search():
    source = Path("src/autoad_researcher/ui/research_chat.py").read_text(encoding="utf-8")
    body = source[source.index("def _render_material_request_panel("):source.index("def _render_freeze_panel(")]

    assert "运行资料搜集 subagent" in body
    assert "run_pending_material_subagents(run_dir)" in body
    assert "execute_sync_web_search(run_dir, query=query)" not in body
    assert "subagent_run_id" in body
