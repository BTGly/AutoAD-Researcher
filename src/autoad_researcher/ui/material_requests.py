"""Research Chat material acquisition request queue."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from autoad_researcher.ui.chat_transcript import redact_secrets


MATERIAL_REQUESTS_DIR = "ui_chat"
MATERIAL_REQUESTS_FILE = "material_requests.jsonl"
MaterialRequestKind = Literal["web_search", "repository_discovery", "material_acquisition"]


def detect_material_request_intent(message: str) -> bool:
    text = message.strip()
    if not text:
        return False
    lowered = text.lower()
    if any(token in lowered for token in ("web_search", "web search", "web_fetch", "web fetch")):
        return True
    return any(
        token in text
        for token in (
            "网络上搜索",
            "网上搜索",
            "搜索",
            "搜一下",
            "搜集",
            "查一下",
            "查找",
            "找资料",
            "找论文",
            "找方法",
            "最新",
            "SOTA",
            "官方代码",
            "代码仓库",
        )
    )


def classify_material_request_kind(message: str) -> MaterialRequestKind:
    lowered = message.lower()
    if "github" in lowered or "repo" in lowered or "repository" in lowered or any(
        token in message for token in ("代码仓库", "官方代码", "仓库")
    ):
        return "repository_discovery"
    if any(token in lowered for token in ("web_search", "web search", "web_fetch", "web fetch")) or any(
        token in message for token in ("搜索", "搜一下", "查一下", "查找", "最新", "SOTA")
    ):
        return "web_search"
    return "material_acquisition"


def append_material_request(run_dir: Path, *, user_message: str) -> dict[str, Any]:
    request = {
        "request_id": _next_material_request_id(run_dir),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
        "kind": classify_material_request_kind(user_message),
        "user_message": redact_secrets(user_message),
        "stage": "discovery_acquisition_pending",
    }
    path = _requests_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(request, ensure_ascii=False, sort_keys=True))
        handle.write("\n")
    return request


def load_material_requests(run_dir: Path) -> list[dict[str, Any]]:
    path = _requests_path(run_dir)
    if not path.is_file():
        return []
    requests: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            requests.append(payload)
    return requests


def build_material_request_rows(run_dir: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for request in load_material_requests(run_dir):
        rows.append({
            "request_id": str(request.get("request_id", "")),
            "status": str(request.get("status", "")),
            "kind": str(request.get("kind", "")),
            "created_at": str(request.get("created_at", "")),
            "user_message": str(request.get("user_message", ""))[:120],
            "result_ref": str(request.get("result_ref", "")),
        })
    return rows


def update_material_request_status(
    run_dir: Path,
    *,
    request_id: str,
    status: str,
    result_ref: str | None = None,
    error_message: str | None = None,
    assigned_agent: str | None = None,
    subagent_run_id: str | None = None,
) -> dict[str, Any] | None:
    requests = load_material_requests(run_dir)
    updated: dict[str, Any] | None = None
    for request in requests:
        if request.get("request_id") != request_id:
            continue
        request["status"] = status
        request["updated_at"] = datetime.now(timezone.utc).isoformat()
        if result_ref:
            request["result_ref"] = result_ref
        if error_message:
            request["error_message"] = error_message[:200]
        if assigned_agent:
            request["assigned_agent"] = assigned_agent
        if subagent_run_id:
            request["subagent_run_id"] = subagent_run_id
        updated = request
        break
    if updated is None:
        return None
    _write_material_requests(run_dir, requests)
    return updated


def build_material_request_reply(request: dict[str, Any]) -> str:
    request_id = str(request.get("request_id", "material_request"))
    kind = str(request.get("kind", "material_acquisition"))
    return (
        f"已登记资料搜集请求 `{request_id}`（{kind}）。\n"
        "当前 Research Chat 不会在后台静默执行网络搜索，也不能承诺几分钟后主动发新消息。\n"
        "下一步需要进入 discovery/acquisition 阶段，由相应 agent 使用 web_search/web_fetch/git_clone 产出 artifacts；完成后我再基于 artifacts 汇总。"
    )


def _requests_path(run_dir: Path) -> Path:
    return run_dir / MATERIAL_REQUESTS_DIR / MATERIAL_REQUESTS_FILE


def _write_material_requests(run_dir: Path, requests: list[dict[str, Any]]) -> None:
    path = _requests_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for request in requests:
            handle.write(json.dumps(request, ensure_ascii=False, sort_keys=True))
            handle.write("\n")


def _next_material_request_id(run_dir: Path) -> str:
    max_seen = 0
    for request in load_material_requests(run_dir):
        request_id = request.get("request_id")
        if not isinstance(request_id, str):
            continue
        match = re.fullmatch(r"mr_(\d{6})", request_id)
        if match:
            max_seen = max(max_seen, int(match.group(1)))
    return f"mr_{max_seen + 1:06d}"
