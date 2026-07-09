from __future__ import annotations

import json
from pathlib import Path

from autoad_researcher.tools.providers import WebFetchResult
from autoad_researcher.assistant.material_subagents import (
    MATERIAL_DISCOVERY_SUBAGENT,
    MATERIAL_SUBAGENT_RUNS_FILE,
    load_material_subagent_runs,
    run_pending_material_subagents,
)
from autoad_researcher.tools.providers import RecordedWebSearchProvider, WebSearchResult
from autoad_researcher.ui.material_requests import append_material_request, load_material_requests
from autoad_researcher.ui.sources import load_source_registry, register_url_source
from autoad_researcher.ui.subagent_inbox import load_uninjected_notifications


def test_material_discovery_subagent_processes_pending_web_search(tmp_path: Path):
    run_dir = tmp_path / "run_subagent"
    run_dir.mkdir()
    append_material_request(run_dir, user_message="搜索 MVTec AD 最新方法")
    provider = RecordedWebSearchProvider({
        "搜索 MVTec AD 最新方法": [
            WebSearchResult(title="Paper", url="https://example.com/paper", snippet="candidate")
        ]
    })

    runs = run_pending_material_subagents(run_dir, provider=provider)

    assert len(runs) == 1
    assert runs[0]["subagent_name"] == MATERIAL_DISCOVERY_SUBAGENT
    assert runs[0]["subagent_run_id"] == "msa_000001"
    assert runs[0]["status"] == "completed"
    assert runs[0]["notification_id"] == "ntf_000001"
    requests = load_material_requests(run_dir)
    assert requests[0]["status"] == "completed"
    assert requests[0]["claimed_by"] == "ui_button"
    assert requests[0]["result_notification_id"] == "ntf_000001"
    run_records = load_material_subagent_runs(run_dir)
    assert run_records[0]["request_id"] == "mr_000001"
    assert (run_dir / "ui_chat" / MATERIAL_SUBAGENT_RUNS_FILE).is_file()
    notifications = load_uninjected_notifications(run_dir)
    assert notifications[0]["request_id"] == "mr_000001"
    assert notifications[0]["evidence_role"] == "candidate_source_only"
    assert notifications[0]["artifact_paths"] == ["ui_chat/sync_web_search_results.jsonl"]


def test_material_discovery_subagent_marks_search_unavailable(tmp_path: Path):
    run_dir = tmp_path / "run_subagent"
    run_dir.mkdir()
    append_material_request(run_dir, user_message="搜索 MVTec AD 最新方法")

    runs = run_pending_material_subagents(run_dir)

    assert runs[0]["status"] == "failed"
    assert runs[0]["notification_id"] == "ntf_000001"
    requests = load_material_requests(run_dir)
    assert requests[0]["status"] == "failed"
    assert requests[0]["claimed_by"] == "ui_button"
    notifications = load_uninjected_notifications(run_dir)
    assert notifications[0]["status"] == "failed"
    assert notifications[0]["severity"] == "error"
    assert "web_search failed" in notifications[0]["summary"]


def test_material_discovery_subagent_skips_non_search_requests(tmp_path: Path):
    run_dir = tmp_path / "run_subagent"
    run_dir.mkdir()
    append_material_request(run_dir, user_message="找一下官方代码仓库")

    runs = run_pending_material_subagents(run_dir)

    assert len(runs) == 1
    assert runs[0]["kind"] == "repository_discovery"
    assert runs[0]["status"] == "completed"


def test_material_subagent_runs_jsonl_is_append_only(tmp_path: Path):
    run_dir = tmp_path / "run_subagent"
    run_dir.mkdir()
    append_material_request(run_dir, user_message="搜索 MVTec AD 最新方法")
    append_material_request(run_dir, user_message="搜索方法")
    provider = RecordedWebSearchProvider({
        "搜索 MVTec AD 最新方法": [
            WebSearchResult(title="A", url="https://example.com/a", snippet="candidate")
        ],
        "搜索方法": [
            WebSearchResult(title="B", url="https://example.com/b", snippet="candidate")
        ],
    })

    run_pending_material_subagents(run_dir, provider=provider)

    path = run_dir / "ui_chat" / MATERIAL_SUBAGENT_RUNS_FILE
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [record["subagent_run_id"] for record in records] == ["msa_000001", "msa_000002"]


def test_web_search_result_is_candidate_source_only_in_notification(tmp_path: Path):
    run_dir = tmp_path / "run_subagent"
    run_dir.mkdir()
    append_material_request(run_dir, user_message="搜索方法")
    provider = RecordedWebSearchProvider({
        "搜索方法": [
            WebSearchResult(title="B", url="https://example.com/b", snippet="candidate")
        ],
    })

    run_pending_material_subagents(run_dir, provider=provider)

    notifications = load_uninjected_notifications(run_dir)
    assert notifications[0]["evidence_role"] == "candidate_source_only"
    assert notifications[0]["summary"] == "找到 1 个候选来源"


def test_web_fetch_subagent_updates_source_and_request_result_ref(tmp_path: Path, monkeypatch):
    run_dir = tmp_path / "run_web_fetch"
    run_dir.mkdir()
    source = register_url_source(run_dir, "https://example.com/paper")
    append_material_request(
        run_dir,
        user_message="下载 https://example.com/paper",
        kind="material_acquisition",
        payload={"tool": "web_fetch", "url": "https://example.com/paper", "source_id": source["source_id"]},
        evidence_role="source_acquired_unparsed",
    )

    class FetchProvider:
        def fetch(self, url: str) -> WebFetchResult:
            return WebFetchResult(
                url=url,
                status_code=200,
                content="<html><title>Paper</title></html>",
                content_sha256="a" * 64,
            )

    import autoad_researcher.tools.providers as providers

    monkeypatch.setattr(providers, "SecureWebFetchProvider", FetchProvider)

    runs = run_pending_material_subagents(run_dir)

    assert runs[0]["status"] == "completed"
    requests = load_material_requests(run_dir)
    assert requests[0]["status"] == "completed"
    assert requests[0]["result_ref"] == f"sources/{source['source_id']}/raw.html"
    registry = load_source_registry(run_dir)
    registered = registry["sources"][0]
    assert registered["status"] == "uploaded_not_parsed"
    assert registered["intake_status"] == "ok"
    assert registered["stored_path"] == f"sources/{source['source_id']}/raw.html"


def test_repository_discovery_marks_source_candidate_intake_only(tmp_path: Path):
    run_dir = tmp_path / "run_repo_discovery"
    run_dir.mkdir()
    source = register_url_source(run_dir, "https://github.com/example/repo", force_kind="github_repo")
    append_material_request(
        run_dir,
        user_message="分析 https://github.com/example/repo",
        kind="repository_discovery",
        payload={"url": "https://github.com/example/repo", "source_id": source["source_id"]},
        evidence_role="candidate_source_only",
    )

    runs = run_pending_material_subagents(run_dir)

    assert runs[0]["status"] == "completed"
    registry = load_source_registry(run_dir)
    registered = registry["sources"][0]
    assert registered["kind"] == "github_repo"
    assert registered["status"] == "user_provided_not_ingested"
    assert registered["intake_status"] == "ok"
    notifications = load_uninjected_notifications(run_dir)
    assert notifications[0]["evidence_role"] == "candidate_source_only"
