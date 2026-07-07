"""Material acquisition subagents for Research Chat requests."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autoad_researcher.ui.material_requests import load_material_requests, update_material_request_status
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
) -> list[dict[str, Any]]:
    """Run eligible queued material requests through material subagents."""
    runs: list[dict[str, Any]] = []
    for request in load_material_requests(run_dir):
        if request.get("status") not in {"queued", "pending"}:
            continue
        if request.get("kind") != "web_search":
            continue
        runs.append(run_material_discovery_subagent(run_dir, request=request, provider=provider))
    return runs


def run_material_discovery_subagent(
    run_dir: Path,
    *,
    request: dict[str, Any],
    provider: WebSearchProvider | None = None,
) -> dict[str, Any]:
    """Run a single web_search material request as a discovery subagent."""
    request_id = str(request.get("request_id", ""))
    query = str(request.get("user_message", ""))
    subagent_run_id = _next_subagent_run_id(run_dir)
    started_at = datetime.now(timezone.utc).isoformat()

    search_result = execute_sync_web_search(run_dir, query=query, provider=provider)
    search_status = str(search_result.get("status", "search_unavailable"))
    request_status = "completed" if search_status == "ok" else search_status
    result_ref = f"ui_chat/{SYNC_SEARCH_FILE}" if search_status in {"ok", "no_results"} else None
    error_message = str(search_result.get("reason", "")) if search_status == "search_unavailable" else None

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
    _append_subagent_run(run_dir, record)
    update_material_request_status(
        run_dir,
        request_id=request_id,
        status=request_status,
        result_ref=result_ref,
        error_message=error_message,
        assigned_agent=MATERIAL_DISCOVERY_SUBAGENT,
        subagent_run_id=subagent_run_id,
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
