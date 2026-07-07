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
from autoad_researcher.ui.research_chat import (
    build_research_chat_messages,
    build_search_unavailable_material_request_reply,
    build_url_source_material_request_reply,
    create_search_unavailable_material_request,
    create_url_source_material_request,
    _save_assistant_reply_and_mark_notifications,
)
from autoad_researcher.ui.sources import load_source_registry
from autoad_researcher.ui.subagent_inbox import load_uninjected_notifications, post_subagent_notification
from autoad_researcher.ui.sync_web_search import execute_sync_web_search


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
    assert request["status"] == "queued"
    assert request["kind"] == "web_search"
    loaded = load_material_requests(run_dir)
    rows = build_material_request_rows(run_dir)
    assert loaded[0]["user_message"] == "搜索 PatchCore 可迁移改进"
    assert rows[0]["request_id"] == "mr_000001"
    assert "资料搜集请求面板" in build_material_request_reply(request)
    assert "通知区" in build_material_request_reply(request)


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


def test_sync_web_search_unavailable_creates_material_request_without_auto_failure(tmp_path: Path):
    run_dir = tmp_path / "run_search_unavailable"
    run_dir.mkdir()

    result = execute_sync_web_search(run_dir, query="搜索 MVTec AD 最新可迁移到 PatchCore 的方法")
    request = create_search_unavailable_material_request(
        run_dir,
        user_input="搜索 MVTec AD 最新可迁移到 PatchCore 的方法",
        search_result=result,
    )

    loaded = load_material_requests(run_dir)
    assert result["status"] == "search_unavailable"
    assert request["request_id"] == "mr_000001"
    assert loaded[0]["kind"] == "web_search"
    assert loaded[0]["status"] == "queued"
    assert loaded[0]["payload"]["query"] == "搜索 MVTec AD 最新可迁移到 PatchCore 的方法"
    assert loaded[0]["evidence_role"] == "candidate_source_only"


def test_search_unavailable_material_request_fallback_to_manual(tmp_path: Path):
    run_dir = tmp_path / "run_search_unavailable_reply"
    run_dir.mkdir()

    result = execute_sync_web_search(run_dir, query="搜索论文")
    request = create_search_unavailable_material_request(
        run_dir,
        user_input="搜索论文",
        search_result=result,
    )
    reply = build_search_unavailable_material_request_reply(result, request, runs=None)

    assert "search_unavailable" in reply
    assert "mr_000001" in reply
    assert "通知区" in reply


def test_url_input_registers_source_and_creates_web_fetch_request(tmp_path: Path):
    run_dir = tmp_path / "run_url"
    run_dir.mkdir()

    intake = create_url_source_material_request(
        run_dir,
        "https://example.com/paper",
        user_message="下载并解析 https://example.com/paper",
    )

    registry = load_source_registry(run_dir)
    requests = load_material_requests(run_dir)
    assert registry["sources"][0]["source_id"] == intake["source"]["source_id"]
    assert registry["sources"][0]["kind"] == "webpage"
    assert requests[0]["kind"] == "material_acquisition"
    assert requests[0]["payload"] == {
        "tool": "web_fetch",
        "url": "https://example.com/paper",
        "source_id": intake["source"]["source_id"],
    }
    assert requests[0]["evidence_role"] == "source_acquired_unparsed"


def test_repeated_url_reuses_source_and_material_request(tmp_path: Path):
    run_dir = tmp_path / "run_repeat_url"
    run_dir.mkdir()

    first = create_url_source_material_request(run_dir, "https://arxiv.org/abs/2303.15140")
    second = create_url_source_material_request(run_dir, "https://arxiv.org/abs/2303.15140")

    registry = load_source_registry(run_dir)
    requests = load_material_requests(run_dir)
    assert len(registry["sources"]) == 1
    assert len(requests) == 1
    assert second["source"]["source_id"] == first["source"]["source_id"]
    assert second["request"]["request_id"] == first["request"]["request_id"]
    assert second["existing_request"] is True


def test_arxiv_url_creates_web_fetch_material_request(tmp_path: Path):
    run_dir = tmp_path / "run_arxiv_url"
    run_dir.mkdir()

    intake = create_url_source_material_request(run_dir, "https://arxiv.org/abs/2303.15140")
    request = intake["request"]
    reply = build_url_source_material_request_reply(intake)

    assert request["kind"] == "material_acquisition"
    assert request["payload"]["tool"] == "web_fetch"
    assert request["payload"]["url"] == "https://arxiv.org/abs/2303.15140"
    assert request["evidence_role"] == "source_acquired_unparsed"
    assert "已登记 URL source" in reply
    assert "还不是 supported facts" in reply


def test_github_url_creates_repository_discovery_request(tmp_path: Path):
    run_dir = tmp_path / "run_github_url"
    run_dir.mkdir()

    intake = create_url_source_material_request(run_dir, "https://github.com/example/repo")
    request = intake["request"]

    assert intake["source"]["kind"] == "github_repo"
    assert request["kind"] == "repository_discovery"
    assert request["payload"] == {
        "url": "https://github.com/example/repo",
        "source_id": intake["source"]["source_id"],
    }
    assert request["evidence_role"] == "candidate_source_only"


def test_repository_discovery_default_evidence_role_is_candidate_source_only(tmp_path: Path):
    run_dir = tmp_path / "run_repo_role"
    run_dir.mkdir()

    request = append_material_request(
        run_dir,
        user_message="找一下 GitHub 官方代码仓库",
        kind="repository_discovery",
    )

    assert request["evidence_role"] == "candidate_source_only"
    assert request["evidence_role"] != "repo_acquired"


def test_material_request_reply_mentions_panel_worker_and_inbox(tmp_path: Path):
    run_dir = tmp_path / "run_reply"
    run_dir.mkdir()
    request = append_material_request(run_dir, user_message="搜索 PatchCore 方法")
    reply = build_material_request_reply(request)

    assert "资料搜集请求面板" in reply
    assert "worker" in reply
    assert "通知区" in reply
    assert "刷新页面或继续对话" in reply


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
    assert "extract_first_url(user_input)" in body
    assert body.index("extract_first_url(user_input)") < body.index("detect_sync_web_search_intent(user_input)")
    assert body.index("detect_sync_web_search_intent(user_input)") < body.index("build_pdf_parse_action(run_dir, user_input")
    assert "detect_material_request_intent(user_input)" in body
    assert body.index("detect_material_request_intent(user_input)") < body.index("call_research_chat(")
    url_block = body[body.index("if url:"):body.index("if detect_sync_web_search_intent(user_input):")]
    assert "request_ids={request_id}" in url_block
    assert "st.rerun()" in url_block


def test_material_request_panel_can_execute_pending_web_search():
    source = Path("src/autoad_researcher/ui/research_chat.py").read_text(encoding="utf-8")
    body = source[source.index("def _render_material_request_panel("):source.index("def _render_freeze_panel(")]

    assert "运行资料搜集 subagent" in body
    assert "run_pending_material_subagents(run_dir)" in body
    assert "execute_sync_web_search(run_dir, query=query)" not in body
    assert "subagent_run_id" in body


def test_notification_marked_injected_only_after_successful_reply():
    source = Path("src/autoad_researcher/ui/research_chat.py").read_text(encoding="utf-8")
    body = source[source.index("def _handle_chat_input("):source.index("def _chat_input_submission(")]
    error_block = body[body.index('if result["error"]'):body.index("evidence_context = build_research_chat_evidence_context")]
    assert "mark_notifications_injected" not in error_block

    helper = source[source.index("def _save_assistant_reply_and_mark_notifications("):source.index("def _chat_input_submission(")]
    assert helper.index("save_transcript(") < helper.index("mark_notifications_injected(")


def test_deterministic_reply_does_not_mark_notifications_injected():
    source = Path("src/autoad_researcher/ui/research_chat.py").read_text(encoding="utf-8")
    body = source[source.index("def _handle_chat_input("):source.index("messages = build_research_chat_messages(")]

    assert "notifications=notifications" not in body
    assert body.count("notifications=None") >= 6


def test_llm_reply_marks_notifications_injected_after_context_use(tmp_path: Path):
    run_dir = tmp_path / "run_mark_after_llm"
    run_dir.mkdir()
    post_subagent_notification(run_dir, {
        "subagent_kind": "material_discovery",
        "request_id": "mr_000001",
        "status": "completed",
        "severity": "info",
        "evidence_role": "candidate_source_only",
        "summary": "找到候选来源",
        "artifact_paths": ["ui_chat/sync_web_search_results.jsonl"],
    })
    notifications = load_uninjected_notifications(run_dir)

    _save_assistant_reply_and_mark_notifications(
        run_dir,
        "intent_clarification",
        "我已读取通知上下文。",
        notifications=notifications,
    )

    assert load_uninjected_notifications(run_dir) == []
