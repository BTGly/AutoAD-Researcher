from __future__ import annotations

import json
from pathlib import Path

from autoad_researcher.tools.providers import RecordedWebSearchProvider, WebSearchResult
from autoad_researcher.ui.sync_web_search import (
    SYNC_SEARCH_FILE,
    build_sync_web_search_reply,
    detect_sync_web_search_intent,
    execute_sync_web_search,
)


def test_search_request_executes_web_search_when_provider_available(tmp_path: Path):
    run_dir = tmp_path / "run_search"
    run_dir.mkdir()
    provider = RecordedWebSearchProvider({
        "搜索 MVTec AD 最新方法": [
            WebSearchResult(
                title="SimpleNet",
                url="https://arxiv.org/abs/2303.15140",
                snippet="Image anomaly detection and localization.",
            )
        ]
    })

    result = execute_sync_web_search(run_dir, query="搜索 MVTec AD 最新方法", provider=provider)

    assert result["status"] == "ok"
    assert result["stage"] == "candidate_source_only"
    assert result["results"][0]["title"] == "SimpleNet"
    path = run_dir / "ui_chat" / SYNC_SEARCH_FILE
    saved = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert saved["results"][0]["evidence_status"] == "not_evidence_until_fetched"


def test_search_result_is_candidate_only(tmp_path: Path):
    run_dir = tmp_path / "run_search"
    run_dir.mkdir()
    provider = RecordedWebSearchProvider({
        "找论文": [
            WebSearchResult(title="Paper", url="https://example.com/paper", snippet="candidate")
        ]
    })

    result = execute_sync_web_search(run_dir, query="找论文", provider=provider)
    reply = build_sync_web_search_reply(result)

    assert "candidate_source_only" in reply
    assert "不是已验证证据" in reply
    assert result["results"][0]["source_status"] == "candidate_source_only"


def test_search_unavailable_when_provider_missing(tmp_path: Path):
    run_dir = tmp_path / "run_search"
    run_dir.mkdir()

    result = execute_sync_web_search(run_dir, query="搜索论文")
    reply = build_sync_web_search_reply(result)

    assert result["status"] == "ok"
    assert len(result["results"]) == 5
    assert (run_dir / "ui_chat" / SYNC_SEARCH_FILE).exists()


def test_detect_sync_web_search_intent_uses_plan_keywords():
    assert detect_sync_web_search_intent("搜索论文")
    assert detect_sync_web_search_intent("搜索方法")
    assert detect_sync_web_search_intent("最新方法")
    assert detect_sync_web_search_intent("找代码")
    assert detect_sync_web_search_intent("github 实现")
    assert not detect_sync_web_search_intent("MVTec，baseline 是 PatchCore")
