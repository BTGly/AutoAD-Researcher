"""Durable, report-bound discussion turns with replay-safe JSONL persistence."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.assistant.model_routing import ModelRoute
from autoad_researcher.reporting.evidence import EvidenceIndex
from autoad_researcher.reporting.store import ReportStore
from autoad_researcher.reporting.tools import MAX_TOOL_CALLS, TOOL_CATALOG, ReportToolCall, execute_tools, load_verified_report_context

MAX_TURNS = 40
MAX_MESSAGE_CHARS = 8000
MAX_HISTORY_MESSAGES = 12
MAX_HISTORY_BYTES = 16 * 1024


class ReportDiscussionBudget(BaseModel):
    """Service-side limits for one report-bound LLM response."""

    model_config = ConfigDict(extra="forbid")

    max_llm_calls: int = Field(default=2, ge=1, le=2)
    max_output_tokens: int = Field(default=700, ge=64, le=2000)
    max_wall_time_seconds: int = Field(default=30, ge=1, le=60)
    max_concurrent_requests: int = Field(default=1, ge=1, le=1)
    max_retries: int = Field(default=0, ge=0, le=1)


class DiscussionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    response_kind: Literal["explain", "verify", "compare", "evidence", "next_step", "insufficient_evidence"]
    evidence_ids: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)


class DiscussionTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn_id: str
    request_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]+$")
    report_id: str
    snapshot_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    user_message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    response: DiscussionResponse | None = None
    status: Literal["pending", "completed", "failed"]
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: str
    completed_at: str | None = None
    error: str | None = None


class DiscussionMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: str
    report_id: str
    snapshot_content_sha256: str
    role: Literal["user", "assistant"]
    content: str
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: str


def start_turn(run_dir: Path, *, report_id: str, request_id: str, content: str, evidence_ids: list[str] | None = None) -> DiscussionTurn:
    manifest = _manifest(run_dir, report_id)
    ids = _validated_evidence_ids(run_dir, report_id, evidence_ids or [])
    with _lock(run_dir, report_id):
        turns = _load_turns_unlocked(run_dir, report_id)
        existing = next((item for item in turns if item.request_id == request_id), None)
        if existing is not None:
            if (existing.user_message, existing.evidence_ids) != (content, ids):
                raise ValueError("discussion request_id conflicts with an existing turn")
            return existing
        if len(turns) >= MAX_TURNS:
            raise ValueError("discussion transcript reached its turn limit")
        turn = DiscussionTurn(turn_id=f"turn_{uuid4().hex}", request_id=request_id, report_id=report_id, snapshot_content_sha256=manifest.source_snapshot_content_sha256, user_message=content, evidence_ids=ids, status="pending", created_at=_utc_now())
        _append_unlocked(_path(run_dir, report_id), turn)
        return turn


def complete_turn(run_dir: Path, *, report_id: str, turn_id: str, response: DiscussionResponse) -> DiscussionTurn:
    _validated_evidence_ids(run_dir, report_id, response.evidence_ids)
    with _lock(run_dir, report_id):
        turn = _require_turn(_load_turns_unlocked(run_dir, report_id), turn_id)
        if turn.status == "completed": return turn
        if turn.status == "failed": raise ValueError("failed discussion turn cannot be completed")
        completed = turn.model_copy(update={"status": "completed", "response": response, "completed_at": _utc_now()})
        _append_unlocked(_path(run_dir, report_id), completed)
        return completed


def fail_turn(run_dir: Path, *, report_id: str, turn_id: str, error: str) -> DiscussionTurn:
    with _lock(run_dir, report_id):
        turn = _require_turn(_load_turns_unlocked(run_dir, report_id), turn_id)
        if turn.status != "pending": return turn
        failed = turn.model_copy(update={"status": "failed", "error": error[:500], "completed_at": _utc_now()})
        _append_unlocked(_path(run_dir, report_id), failed)
        return failed


def respond_to_turn(
    run_dir: Path,
    *,
    report_id: str,
    turn_id: str,
    api_key: str,
    provider_url: str,
    model: str,
    budget: ReportDiscussionBudget | None = None,
    model_route: ModelRoute | None = None,
) -> DiscussionTurn:
    """Complete one persisted turn using only frozen report summaries and evidence."""
    turn = _require_turn(load_turns(run_dir, report_id=report_id), turn_id)
    if turn.status != "pending": return turn
    if not api_key or not model:
        return fail_turn(run_dir, report_id=report_id, turn_id=turn_id, error="report discussion model is not configured")
    limits = budget or ReportDiscussionBudget()
    if not _try_response_slot(run_dir, report_id):
        return turn
    try:
        return _respond_with_slot(
            run_dir,
            report_id=report_id,
            turn=turn,
            api_key=api_key,
            provider_url=provider_url,
            model=model,
            budget=limits,
            model_route=model_route,
        )
    finally:
        _release_response_slot(run_dir, report_id)


def _respond_with_slot(
    run_dir: Path,
    *,
    report_id: str,
    turn: DiscussionTurn,
    api_key: str,
    provider_url: str,
    model: str,
    budget: ReportDiscussionBudget,
    model_route: ModelRoute | None = None,
) -> DiscussionTurn:
    _facts, index, digest_model, _markdown = load_verified_report_context(
        run_dir,
        report_id,
        snapshot_content_sha256_expected=turn.snapshot_content_sha256,
    )
    digest = digest_model.model_dump(mode="json")
    evidence = [{"evidence_id": item.evidence_id, "kind": item.evidence_kind, "summary": item.summary, "attempt_id": item.attempt_id, "idea_id": item.idea_id} for item in index.entries[:24]]
    messages = [
        {"role": "system", "content": "You answer only from frozen report context. Return either DiscussionResponse JSON, or {tool_calls:[{name,arguments}]}. Use typed tools for deep details; never claim file access, execution, or unlisted evidence."},
        {"role": "user", "content": json.dumps({"report_id": report_id, "snapshot_sha256": turn.snapshot_content_sha256, "digest": digest, "evidence": evidence, "tool_catalog": TOOL_CATALOG, "max_tool_calls": MAX_TOOL_CALLS}, ensure_ascii=False)},
        {"role": "assistant", "content": "I will use only this frozen report context and registered Evidence."},
        *_recent_history(load_turns(run_dir, report_id=report_id), current_turn_id=turn.turn_id),
        {"role": "user", "content": turn.user_message},
    ]
    from autoad_researcher.ui.chat_client import call_research_chat
    result = call_research_chat(
        api_key,
        provider_url,
        messages,
        model=model,
        timeout_s=budget.max_wall_time_seconds,
        priority="interactive",
        response_format_json=True,
        max_tokens=budget.max_output_tokens,
        temperature=0.1,
        thinking_type=model_route.thinking_type if model_route is not None else None,
        reasoning_effort=model_route.reasoning_effort if model_route is not None else None,
    )
    try:
        if result.get("error"): raise ValueError(str(result["error"]))
        reply = str(result.get("reply") or "")
        try:
            response = DiscussionResponse.model_validate_json(reply)
        except Exception:
            plan = _tool_plan(reply)
            if budget.max_llm_calls < 2:
                raise ValueError("report discussion budget does not allow a typed deep-read response")
            tool_results = execute_tools(
                run_dir,
                report_id=report_id,
                calls=plan,
                snapshot_content_sha256_expected=turn.snapshot_content_sha256,
            )
            final = call_research_chat(
                api_key,
                provider_url,
                [*messages, {"role": "tool", "content": json.dumps({"tool_results": tool_results}, ensure_ascii=False)}, {"role": "system", "content": "Return only DiscussionResponse JSON. Cite only evidence_ids from the frozen report."}],
                model=model,
                timeout_s=budget.max_wall_time_seconds,
                priority="interactive",
                response_format_json=True,
                max_tokens=budget.max_output_tokens,
                temperature=0.1,
                thinking_type=model_route.thinking_type if model_route is not None else None,
                reasoning_effort=model_route.reasoning_effort if model_route is not None else None,
            )
            if final.get("error"):
                raise ValueError(str(final["error"]))
            response = DiscussionResponse.model_validate_json(str(final.get("reply") or ""))
        _validated_evidence_ids(run_dir, report_id, response.evidence_ids)
        if response.response_kind in {"explain", "verify", "compare"} and not response.evidence_ids:
            raise ValueError("factual report discussion responses require Evidence IDs")
    except Exception as exc:
        return fail_turn(run_dir, report_id=report_id, turn_id=turn.turn_id, error=str(exc))
    return complete_turn(run_dir, report_id=report_id, turn_id=turn.turn_id, response=response)


def _recent_history(turns: list[DiscussionTurn], *, current_turn_id: str) -> list[dict[str, str]]:
    """Keep the latest completed dialogue as data within a fixed request budget."""

    messages: list[dict[str, str]] = []
    for item in turns:
        if item.turn_id == current_turn_id or item.status != "completed" or item.response is None:
            continue
        messages.extend((
            {"role": "user", "content": item.user_message},
            {"role": "assistant", "content": item.response.answer},
        ))
    selected: list[dict[str, str]] = []
    used = 0
    for item in reversed(messages[-MAX_HISTORY_MESSAGES:]):
        size = len(item["content"].encode("utf-8"))
        if selected and used + size > MAX_HISTORY_BYTES:
            break
        if size > MAX_HISTORY_BYTES:
            selected.append({"role": item["role"], "content": item["content"].encode("utf-8")[-MAX_HISTORY_BYTES:].decode("utf-8", errors="ignore")})
            break
        selected.append(item)
        used += size
    selected.reverse()
    if len(selected) < len(messages):
        return [{"role": "system", "content": "Earlier report discussion turns were omitted to preserve the bounded context window."}, *selected]
    return selected


def _tool_plan(reply: str) -> list[ReportToolCall]:
    raw = json.loads(reply)
    if not isinstance(raw, dict) or not isinstance(raw.get("tool_calls"), list):
        raise ValueError("discussion response was neither a structured answer nor a typed tool plan")
    calls = [ReportToolCall.model_validate(item) for item in raw["tool_calls"]]
    if not calls:
        raise ValueError("typed tool plan must request at least one tool")
    return calls


def load_turns(run_dir: Path, *, report_id: str) -> list[DiscussionTurn]:
    _manifest(run_dir, report_id)
    return _load_turns_unlocked(run_dir, report_id)


def load_messages(run_dir: Path, *, report_id: str) -> list[DiscussionMessage]:
    messages: list[DiscussionMessage] = []
    for turn in load_turns(run_dir, report_id=report_id):
        messages.append(DiscussionMessage(message_id=f"{turn.turn_id}:user", report_id=report_id, snapshot_content_sha256=turn.snapshot_content_sha256, role="user", content=turn.user_message, evidence_ids=turn.evidence_ids, created_at=turn.created_at))
        if turn.response is not None:
            messages.append(DiscussionMessage(message_id=f"{turn.turn_id}:assistant", report_id=report_id, snapshot_content_sha256=turn.snapshot_content_sha256, role="assistant", content=turn.response.answer, evidence_ids=turn.response.evidence_ids, created_at=turn.completed_at or turn.created_at))
    return messages[-MAX_TURNS * 2:]


def append_message(run_dir: Path, *, report_id: str, role: str, content: str, evidence_ids: list[str] | None = None) -> DiscussionMessage:
    """Compatibility helper for internal callers; API uses ``start_turn``."""
    if role != "user": raise ValueError("standalone assistant messages are not supported")
    turn = start_turn(run_dir, report_id=report_id, request_id=f"legacy.{uuid4().hex}", content=content, evidence_ids=evidence_ids)
    return load_messages(run_dir, report_id=report_id)[-1]


def _load_turns_unlocked(run_dir: Path, report_id: str) -> list[DiscussionTurn]:
    path = _path(run_dir, report_id)
    if not path.is_file(): return []
    records = path.read_text(encoding="utf-8").splitlines()
    by_id: dict[str, DiscussionTurn] = {}
    for index, line in enumerate(records):
        if not line.strip(): continue
        try: item = DiscussionTurn.model_validate_json(line)
        except Exception as exc:
            if index == len(records) - 1: break
            raise ValueError("discussion transcript contains a corrupt non-tail record") from exc
        if item.report_id != report_id: raise ValueError("discussion transcript report identity mismatch")
        by_id[item.turn_id] = item
    return sorted(by_id.values(), key=lambda item: (item.created_at, item.turn_id))


def _validated_evidence_ids(run_dir: Path, report_id: str, ids: list[str]) -> list[str]:
    known = {item.evidence_id for item in EvidenceIndex.model_validate_json((run_dir / "reports" / report_id / "evidence_index.json").read_text(encoding="utf-8")).entries}
    if not set(ids).issubset(known): raise ValueError("discussion references unknown Evidence IDs")
    return ids


def _require_turn(turns: list[DiscussionTurn], turn_id: str) -> DiscussionTurn:
    found = next((item for item in turns if item.turn_id == turn_id), None)
    if found is None: raise FileNotFoundError("discussion turn not found")
    return found


def _append_unlocked(path: Path, value: DiscussionTurn) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(value.model_dump_json() + "\n"); handle.flush(); os.fsync(handle.fileno())


def _manifest(run_dir: Path, report_id: str): return ReportStore().load_manifest(run_dir, report_id)
def _path(run_dir: Path, report_id: str) -> Path: return run_dir / "reports" / report_id / "discussion" / "turns.jsonl"
def _utc_now() -> str: return datetime.now(timezone.utc).isoformat()


def _response_lock_path(run_dir: Path, report_id: str) -> Path:
    return run_dir / "reports" / report_id / "discussion" / ".response.lock"


def _try_response_slot(run_dir: Path, report_id: str) -> bool:
    path = _response_lock_path(run_dir, report_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
    except FileExistsError:
        try:
            if time.time() - path.stat().st_mtime > ReportDiscussionBudget().max_wall_time_seconds + 5:
                path.unlink()
                return _try_response_slot(run_dir, report_id)
        except OSError:
            pass
        return False
    os.close(fd)
    return True


def _release_response_slot(run_dir: Path, report_id: str) -> None:
    try:
        _response_lock_path(run_dir, report_id).unlink()
    except OSError:
        pass


@contextmanager
def _lock(run_dir: Path, report_id: str, timeout: float = 5.0):
    path = run_dir / "reports" / report_id / "discussion" / ".turns.lock"; path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout; fd = None
    while time.monotonic() < deadline:
        try: fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR); break
        except FileExistsError: time.sleep(.05)
    if fd is None: raise TimeoutError("could not acquire discussion lock")
    try: yield
    finally:
        os.close(fd)
        try: path.unlink()
        except OSError: pass
