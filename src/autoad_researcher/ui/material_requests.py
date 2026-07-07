"""Research Chat material acquisition request queue."""

from __future__ import annotations

import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from autoad_researcher.ui.chat_transcript import redact_secrets


MATERIAL_REQUESTS_DIR = "ui_chat"
MATERIAL_REQUESTS_FILE = "material_requests.jsonl"
MATERIAL_REQUESTS_LOCK = ".material_requests.lock"
MaterialRequestKind = Literal["web_search", "repository_discovery", "material_acquisition"]
MaterialRequestStatus = Literal["queued", "pending", "running", "completed", "failed", "cancelled"]


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


def append_material_request(
    run_dir: Path,
    *,
    user_message: str,
    kind: MaterialRequestKind | None = None,
    payload: dict[str, Any] | None = None,
    evidence_role: str | None = None,
    created_from_message_id: str | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    request_kind = kind or classify_material_request_kind(user_message)
    request = {
        "schema_version": 1,
        "request_id": _next_material_request_id(run_dir),
        "kind": request_kind,
        "status": "queued",
        "payload": payload if payload is not None else {"query": redact_secrets(user_message)},
        "user_message": redact_secrets(user_message),
        "requested_by": "research_chat",
        "created_from_message_id": created_from_message_id,
        "created_at": now,
        "updated_at": now,
        "attempt_count": 0,
        "claimed_by": None,
        "lease_until": None,
        "result_notification_id": None,
        "last_error": None,
        "evidence_role": evidence_role or _default_evidence_role(request_kind),
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


def claim_material_request(
    run_dir: Path,
    *,
    request_id: str,
    worker_id: str,
    lease_seconds: int = 300,
) -> bool:
    with _material_requests_lock(run_dir):
        requests = load_material_requests(run_dir)
        now = datetime.now(timezone.utc)
        for request in requests:
            if request.get("request_id") != request_id:
                continue
            status = request.get("status")
            lease_until = _parse_datetime(request.get("lease_until"))
            claimable = status in {"queued", "pending"} or (status == "running" and lease_until is not None and lease_until <= now)
            if not claimable:
                return False
            request["status"] = "running"
            request["claimed_by"] = worker_id
            request["lease_until"] = (now + timedelta(seconds=lease_seconds)).isoformat()
            request["attempt_count"] = int(request.get("attempt_count") or 0) + 1
            request["updated_at"] = now.isoformat()
            _write_material_requests(run_dir, requests)
            return True
    return False


def complete_material_request(run_dir: Path, *, request_id: str, notification_id: str | None) -> dict[str, Any] | None:
    with _material_requests_lock(run_dir):
        requests = load_material_requests(run_dir)
        updated: dict[str, Any] | None = None
        now = datetime.now(timezone.utc).isoformat()
        for request in requests:
            if request.get("request_id") != request_id:
                continue
            request["status"] = "completed"
            request["result_notification_id"] = notification_id
            request["lease_until"] = None
            request["updated_at"] = now
            updated = request
            break
        if updated is not None:
            _write_material_requests(run_dir, requests)
        return updated


def fail_material_request(
    run_dir: Path,
    *,
    request_id: str,
    error_code: str,
    error_message: str,
    retryable: bool,
) -> dict[str, Any] | None:
    with _material_requests_lock(run_dir):
        requests = load_material_requests(run_dir)
        updated: dict[str, Any] | None = None
        now = datetime.now(timezone.utc).isoformat()
        for request in requests:
            if request.get("request_id") != request_id:
                continue
            request["status"] = "failed"
            request["lease_until"] = None
            request["updated_at"] = now
            request["last_error"] = {
                "error_code": error_code,
                "error_message": error_message[:500],
                "retryable": retryable,
                "failed_at": now,
            }
            updated = request
            break
        if updated is not None:
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


@contextmanager
def _material_requests_lock(run_dir: Path, *, timeout_seconds: float = 5.0):
    path = run_dir / MATERIAL_REQUESTS_DIR / MATERIAL_REQUESTS_LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    fd: int | None = None
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("ascii", errors="ignore"))
            break
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError("material request lock timeout")
            time.sleep(0.01)
    try:
        yield
    finally:
        if fd is not None:
            os.close(fd)
        try:
            path.unlink()
        except FileNotFoundError:
            pass


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


def _default_evidence_role(kind: str) -> str:
    if kind == "web_search":
        return "candidate_source_only"
    if kind == "repository_discovery":
        return "repo_acquired"
    return "source_acquired_unparsed"


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
