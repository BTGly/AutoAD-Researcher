"""Material acquisition subagents for Research Chat requests."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autoad_researcher.ui.material_requests import (
    claim_material_request,
    complete_material_request,
    fail_material_request,
    load_material_requests,
)
from autoad_researcher.ui.sources import update_source_intake_result
from autoad_researcher.ui.subagent_inbox import post_subagent_notification
from autoad_researcher.ui.sync_web_search import (
    SYNC_SEARCH_FILE,
    WebSearchProvider,
    build_sync_web_search_reply,
    execute_sync_web_search,
)


MATERIAL_SUBAGENT_RUNS_DIR = "ui_chat"
MATERIAL_SUBAGENT_RUNS_FILE = "material_subagent_runs.jsonl"
MATERIAL_DISCOVERY_SUBAGENT = "material_discovery_subagent"


def run_pending_material_subagents(
    run_dir: Path,
    *,
    provider: WebSearchProvider | None = None,
    worker_id: str = "ui_button",
    request_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Run eligible queued material requests through material subagents."""
    runs: list[dict[str, Any]] = []
    for request in load_material_requests(run_dir):
        if request.get("status") not in {"queued", "pending"}:
            continue
        request_id = str(request.get("request_id", ""))
        if request_ids is not None and request_id not in request_ids:
            continue
        if not claim_material_request(run_dir, request_id=request_id, worker_id=worker_id):
            continue
        claimed = next(
            (item for item in load_material_requests(run_dir) if item.get("request_id") == request_id),
            request,
        )
        kind = str(request.get("kind", ""))
        if kind == "web_search":
            runs.append(run_material_discovery_subagent(run_dir, request=claimed, provider=provider))
        elif kind == "material_acquisition":
            runs.append(run_web_fetch_subagent(run_dir, request=claimed))
        elif kind == "repository_discovery":
            runs.append(run_repository_discovery_subagent(run_dir, request=claimed))
    return runs


def run_material_discovery_subagent(
    run_dir: Path,
    *,
    request: dict[str, Any],
    provider: WebSearchProvider | None = None,
) -> dict[str, Any]:
    """Run a single web_search material request as a discovery subagent."""
    request_id = str(request.get("request_id", ""))
    payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}
    query = str(payload.get("query") or request.get("user_message", ""))
    subagent_run_id = _next_subagent_run_id(run_dir)
    started_at = datetime.now(timezone.utc).isoformat()

    search_result = execute_sync_web_search(run_dir, query=query, provider=provider)
    search_status = str(search_result.get("status", "search_unavailable"))
    request_status = "failed" if search_status == "search_unavailable" else "completed"
    result_ref = f"ui_chat/{SYNC_SEARCH_FILE}" if search_status in {"ok", "no_results"} else None
    error_message = str(search_result.get("reason", "")) if search_status == "search_unavailable" else None
    evidence_role = str(request.get("evidence_role") or "candidate_source_only")
    result_count = len(search_result.get("results", [])) if isinstance(search_result.get("results"), list) else 0
    summary = _summary_for_web_search(search_status, result_count, error_message)

    record = {
        "subagent_run_id": subagent_run_id,
        "subagent_name": MATERIAL_DISCOVERY_SUBAGENT,
        "request_id": request_id,
        "kind": "web_search",
        "query": query,
        "status": request_status,
        "search_status": search_status,
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "result_ref": result_ref,
        "error_message": error_message,
        "reply": build_sync_web_search_reply(search_result),
    }
    notification_id = post_subagent_notification(
        run_dir,
        {
            "subagent_kind": "material_discovery",
            "request_id": request_id,
            "status": request_status,
            "severity": "error" if request_status == "failed" else "info",
            "evidence_role": evidence_role,
            "summary": summary,
            "artifact_paths": [result_ref] if result_ref else [],
        },
    )
    record["notification_id"] = notification_id
    _append_subagent_run(run_dir, record)
    if request_status == "completed":
        complete_material_request(run_dir, request_id=request_id, notification_id=notification_id)
    else:
        fail_material_request(
            run_dir,
            request_id=request_id,
            error_code="web_search_unavailable",
            error_message=error_message or "web_search failed",
            retryable=True,
        )
    return record


def load_material_subagent_runs(run_dir: Path) -> list[dict[str, Any]]:
    path = _runs_path(run_dir)
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _append_subagent_run(run_dir: Path, record: dict[str, Any]) -> None:
    path = _runs_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        handle.write("\n")


def _runs_path(run_dir: Path) -> Path:
    return run_dir / MATERIAL_SUBAGENT_RUNS_DIR / MATERIAL_SUBAGENT_RUNS_FILE


def _next_subagent_run_id(run_dir: Path) -> str:
    max_seen = 0
    for record in load_material_subagent_runs(run_dir):
        run_id = record.get("subagent_run_id")
        if not isinstance(run_id, str):
            continue
        match = re.fullmatch(r"msa_(\d{6})", run_id)
        if match:
            max_seen = max(max_seen, int(match.group(1)))
    return f"msa_{max_seen + 1:06d}"


def _summary_for_web_search(status: str, result_count: int, error_message: str | None) -> str:
    if status == "ok":
        return f"找到 {result_count} 个候选来源"
    if status == "no_results":
        return "web_search 已完成，但没有返回候选来源"
    return f"web_search failed: {error_message or 'provider unavailable'}"


def run_web_fetch_subagent(
    run_dir: Path,
    *,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Run a web_fetch material acquisition request as a subagent."""
    request_id = str(request.get("request_id", ""))
    payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}
    url = str(payload.get("url", ""))
    source_id = str(payload.get("source_id", ""))
    evidence_role = str(request.get("evidence_role") or "source_acquired_unparsed")
    subagent_run_id = _next_subagent_run_id(run_dir)
    started_at = datetime.now(timezone.utc).isoformat()

    try:
        from autoad_researcher.tools.providers import SecureWebFetchProvider

        provider = SecureWebFetchProvider()
        result = provider.fetch(url)
        output_dir = run_dir / "sources" / source_id
        output_dir.mkdir(parents=True, exist_ok=True)
        html_path = output_dir / "raw.html"
        html_path.write_text(result.content, encoding="utf-8")
        artifact_paths = [str(html_path.relative_to(run_dir))]
        if source_id:
            update_source_intake_result(
                run_dir,
                source_id,
                status="uploaded_not_parsed",
                stored_path=artifact_paths[0],
                intake_status="ok",
                clear_intake_error=True,
            )
        req_status = "completed"
        summary = f"已下载 {url}（{len(result.content)} 字节）"
        error_message = None
    except Exception as exc:
        artifact_paths = []
        req_status = "failed"
        summary = f"web_fetch failed: {exc}"
        error_message = str(exc)[:500]
        if source_id:
            update_source_intake_result(
                run_dir,
                source_id,
                status="failed",
                intake_status="failed",
                intake_error={
                    "error_code": "web_fetch_failed",
                    "error_message": error_message,
                },
                error_message=error_message,
            )

    record = {
        "subagent_run_id": subagent_run_id,
        "subagent_name": "web_fetch_subagent",
        "request_id": request_id,
        "kind": "material_acquisition",
        "url": url,
        "status": req_status,
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "result_ref": artifact_paths[0] if artifact_paths else None,
        "error_message": error_message,
        "reply": summary,
    }
    notification_id = post_subagent_notification(
        run_dir,
        {
            "subagent_kind": "web_fetch",
            "request_id": request_id,
            "status": req_status,
            "severity": "error" if req_status == "failed" else "info",
            "evidence_role": evidence_role,
            "summary": summary,
            "artifact_paths": artifact_paths,
            "source_ids": [source_id] if source_id else [],
        },
    )
    record["notification_id"] = notification_id
    _append_subagent_run(run_dir, record)
    if req_status == "completed":
        complete_material_request(
            run_dir,
            request_id=request_id,
            notification_id=notification_id,
            result_ref=artifact_paths[0] if artifact_paths else None,
        )
    else:
        fail_material_request(
            run_dir,
            request_id=request_id,
            error_code="web_fetch_failed",
            error_message=error_message or "web_fetch failed",
            retryable=True,
        )
    return record


def run_repository_discovery_subagent(
    run_dir: Path,
    *,
    request: dict[str, Any],
) -> dict[str, Any]:
    """Run a repository_discovery material request as a subagent."""
    request_id = str(request.get("request_id", ""))
    payload = request.get("payload") if isinstance(request.get("payload"), dict) else {}
    url = str(payload.get("url", ""))
    source_id = str(payload.get("source_id", ""))
    evidence_role = str(request.get("evidence_role") or "candidate_source_only")
    subagent_run_id = _next_subagent_run_id(run_dir)
    started_at = datetime.now(timezone.utc).isoformat()

    summary = f"已登记 GitHub 仓库候选：{url}（尚未 clone）。repo clone 是后续阶段操作。"
    if source_id:
        update_source_intake_result(
            run_dir,
            source_id,
            status="user_provided_not_ingested",
            intake_status="ok",
            clear_intake_error=True,
        )
    record = {
        "subagent_run_id": subagent_run_id,
        "subagent_name": "repository_discovery_subagent",
        "request_id": request_id,
        "kind": "repository_discovery",
        "url": url,
        "status": "completed",
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "result_ref": None,
        "error_message": None,
        "reply": summary,
    }
    notification_id = post_subagent_notification(
        run_dir,
        {
            "subagent_kind": "repository_discovery",
            "request_id": request_id,
            "status": "completed",
            "severity": "info",
            "evidence_role": evidence_role,
            "summary": summary,
            "artifact_paths": [],
            "source_ids": [source_id] if source_id else [],
        },
    )
    record["notification_id"] = notification_id
    _append_subagent_run(run_dir, record)
    complete_material_request(run_dir, request_id=request_id, notification_id=notification_id)
    return record
