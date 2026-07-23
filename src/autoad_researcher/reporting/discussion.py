"""Durable, report-bound discussion turns with replay-safe JSONL persistence."""

from __future__ import annotations

import json
import hashlib
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.assistant.model_routing import ModelRoute
from autoad_researcher.reporting.evidence import EvidenceIndex
from autoad_researcher.reporting.store import ReportStore
from autoad_researcher.reporting.tools import MAX_TOOL_CALLS, ReportToolCall, execute_tools, load_verified_report_context, native_tool_definitions

REPORT_REQUEST_TIMEOUT_SECONDS = 60
DISCUSSION_RESPONSE_CONTRACT = (
    "Return exactly one JSON object with exactly these keys: answer, response_kind, evidence_ids, "
    "unsupported_claims. response_kind must be exactly one of explain, verify, compare, evidence, "
    "next_step, or insufficient_evidence. Do not use answer_type or any other key."
)
DISCUSSION_EVIDENCE_RULE = (
    "For requests about direct failure evidence, stderr or stdout details, root cause, or a minimal repair, "
    "first use a registered read-only Attempt or log tool such as get_outcome_card, search_log, or read_log_range. "
    "Do not infer an error class from a task profile or generic failure pattern. If the tool result does not support "
    "a claim, say that the evidence is insufficient."
)
RESPONSE_CAPACITY_ERROR = "报告讨论当前繁忙，请稍后重试。"


class DiscussionCapacityBusy(RuntimeError):
    """The durable turn remains pending until its request can acquire a slot."""

    def __init__(self, turn_id: str) -> None:
        self.turn_id = turn_id
        super().__init__(RESPONSE_CAPACITY_ERROR)


class ReportDiscussionBudget(BaseModel):
    """Transport timeout for one report-bound request; model output is provider-controlled."""

    model_config = ConfigDict(extra="forbid")

    max_wall_time_seconds: int = Field(default=60, ge=1, le=120)


class DiscussionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str = Field(min_length=1)
    response_kind: Literal["explain", "verify", "compare", "evidence", "next_step", "insufficient_evidence"]
    evidence_ids: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)


class DiscussionTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn_id: str
    request_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]+$")
    report_id: str
    snapshot_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    user_message: str = Field(min_length=1)
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
        raise DiscussionCapacityBusy(turn.turn_id)
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
    budget: ReportDiscussionBudget = ReportDiscussionBudget(),
    model_route: ModelRoute | None = None,
) -> DiscussionTurn:
    _facts, index, digest_model, _markdown = load_verified_report_context(
        run_dir,
        report_id,
        snapshot_content_sha256_expected=turn.snapshot_content_sha256,
    )
    digest = digest_model.model_dump(mode="json")
    evidence = [{"evidence_id": item.evidence_id, "kind": item.evidence_kind, "summary": item.summary, "attempt_id": item.attempt_id, "idea_id": item.idea_id, "artifact_ref": item.artifact_ref.model_dump(mode="json"), "field_path": item.field_path} for item in index.entries]
    messages = [
        {"role": "system", "content": f"You answer only from frozen report context. {DISCUSSION_RESPONSE_CONTRACT} {DISCUSSION_EVIDENCE_RULE} Use the registered read-only tools when the digest and evidence index are insufficient; after tool results, cite only registered evidence_ids. Never claim file access, execution, or unlisted evidence."},
        {"role": "user", "content": json.dumps({"report_id": report_id, "snapshot_sha256": turn.snapshot_content_sha256, "digest": digest, "evidence": evidence}, ensure_ascii=False)},
        {"role": "assistant", "content": "I will use only this frozen report context and registered Evidence."},
        *_recent_history(load_turns(run_dir, report_id=report_id), current_turn_id=turn.turn_id, context_window=model_route.context_window if model_route is not None else 1_000_000),
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
        response_format_json=False,
        temperature=0.1,
        thinking_type=model_route.thinking_type if model_route is not None else None,
        reasoning_effort=model_route.reasoning_effort if model_route is not None else None,
        tools=native_tool_definitions(),
    )
    try:
        if result.get("error"): raise ValueError(str(result["error"]))
        reply = str(result.get("reply") or "")
        raw_tool_calls = result.get("tool_calls") or []
        if raw_tool_calls:
            native_calls = _native_tool_calls(raw_tool_calls)
            tool_results = execute_tools(
                run_dir,
                report_id=report_id,
                calls=[item[1] for item in native_calls],
                snapshot_content_sha256_expected=turn.snapshot_content_sha256,
            )
            assistant_message: dict[str, Any] = {
                "role": "assistant",
                "content": reply or None,
                "tool_calls": [item[0] for item in native_calls],
            }
            reasoning = result.get("reasoning")
            if isinstance(reasoning, str) and reasoning:
                assistant_message["reasoning_content"] = reasoning
            tool_messages = [
                {
                    "role": "tool",
                    "tool_call_id": raw_call["id"],
                    "content": json.dumps(tool_result, ensure_ascii=False),
                }
                for raw_call, tool_result in zip((item[0] for item in native_calls), tool_results, strict=True)
            ]
            final = call_research_chat(
                api_key,
                provider_url,
                [*messages, assistant_message, *tool_messages],
                model=model,
                timeout_s=budget.max_wall_time_seconds,
                priority="interactive",
                response_format_json=True,
                temperature=0.1,
                thinking_type=model_route.thinking_type if model_route is not None else None,
                reasoning_effort=model_route.reasoning_effort if model_route is not None else None,
            )
            if final.get("error"):
                raise ValueError(str(final["error"]))
            final_reply = str(final.get("reply") or "")
            try:
                response = DiscussionResponse.model_validate_json(final_reply)
            except ValueError as exc:
                repair = call_research_chat(
                    api_key,
                    provider_url,
                    [
                        *messages,
                        assistant_message,
                        *tool_messages,
                        {"role": "assistant", "content": final_reply},
                        {"role": "user", "content": _discussion_repair_prompt(str(exc))},
                    ],
                    model=model,
                    timeout_s=budget.max_wall_time_seconds,
                    priority="interactive",
                    response_format_json=True,
                    temperature=0.1,
                    thinking_type=model_route.thinking_type if model_route is not None else None,
                    reasoning_effort=model_route.reasoning_effort if model_route is not None else None,
                )
                if repair.get("error"):
                    raise ValueError(str(repair["error"])) from exc
                response = DiscussionResponse.model_validate_json(str(repair.get("reply") or ""))
        else:
            try:
                response = DiscussionResponse.model_validate_json(reply)
            except ValueError as exc:
                repair = call_research_chat(
                    api_key,
                    provider_url,
                    [
                        *messages,
                        {"role": "assistant", "content": reply},
                        {"role": "user", "content": _discussion_repair_prompt(str(exc))},
                    ],
                    model=model,
                    timeout_s=budget.max_wall_time_seconds,
                    priority="interactive",
                    response_format_json=True,
                    temperature=0.1,
                    thinking_type=model_route.thinking_type if model_route is not None else None,
                    reasoning_effort=model_route.reasoning_effort if model_route is not None else None,
                )
                if repair.get("error"):
                    raise ValueError(str(repair["error"])) from exc
                response = DiscussionResponse.model_validate_json(str(repair.get("reply") or ""))
        _validated_evidence_ids(run_dir, report_id, response.evidence_ids)
        if response.response_kind in {"explain", "verify", "compare"} and not response.evidence_ids:
            raise ValueError("factual report discussion responses require Evidence IDs")
    except Exception as exc:
        return fail_turn(run_dir, report_id=report_id, turn_id=turn.turn_id, error=str(exc))
    return complete_turn(run_dir, report_id=report_id, turn_id=turn.turn_id, response=response)


def _discussion_repair_prompt(diagnostic: str) -> str:
    return (
        "The previous response did not match DiscussionResponse JSON. "
        f"{DISCUSSION_RESPONSE_CONTRACT} Preserve the answer's meaning, cite only registered "
        "Evidence IDs, and use response_kind=insufficient_evidence when no supported factual answer is available. "
        f"Validation diagnostic: {diagnostic}"
    )


def _recent_history(turns: list[DiscussionTurn], *, current_turn_id: str, context_window: int = 1_000_000) -> list[dict[str, str]]:
    """Keep full history until the selected provider context requires traceable compression."""

    messages: list[dict[str, str]] = []
    for item in turns:
        if item.turn_id == current_turn_id or item.status != "completed" or item.response is None:
            continue
        messages.extend((
            {"role": "user", "content": item.user_message},
            {"role": "assistant", "content": item.response.answer},
        ))
    limit_bytes = max(1, context_window * 4)
    total = sum(len(item["content"].encode("utf-8")) for item in messages)
    if total <= limit_bytes:
        return messages
    completed = [item for item in turns if item.turn_id != current_turn_id and item.status == "completed" and item.response is not None]
    result = list(messages)
    replaced = 0
    for index, item in enumerate(completed):
        if total <= limit_bytes:
            break
        compressed = {
            "role": "system",
            "content": json.dumps({
                "compressed_turn": item.turn_id,
                "snapshot_content_sha256": item.snapshot_content_sha256,
                "evidence_ids": sorted(set([*item.evidence_ids, *item.response.evidence_ids])),
                "user_message_sha256": hashlib.sha256(item.user_message.encode("utf-8")).hexdigest(),
                "assistant_answer_sha256": hashlib.sha256(item.response.answer.encode("utf-8")).hexdigest(),
                "source": "full turn remains in the durable discussion transcript and can be re-read by turn_id",
            }, ensure_ascii=False, sort_keys=True),
        }
        pair_start = index * 2 - replaced
        old_size = sum(len(result[position]["content"].encode("utf-8")) for position in (pair_start, pair_start + 1))
        result[pair_start:pair_start + 2] = [compressed]
        total += len(compressed["content"].encode("utf-8")) - old_size
        replaced += 1
    return result


def _native_tool_calls(raw_tool_calls: Any) -> list[tuple[dict[str, Any], ReportToolCall]]:
    if not isinstance(raw_tool_calls, list) or not raw_tool_calls:
        raise ValueError("native report tool calls must be a non-empty list")
    if len(raw_tool_calls) > MAX_TOOL_CALLS:
        raise ValueError("report discussion requested too many typed tools")
    parsed: list[tuple[dict[str, Any], ReportToolCall]] = []
    for raw_call in raw_tool_calls:
        if not isinstance(raw_call, dict) or not isinstance(raw_call.get("id"), str) or not raw_call["id"]:
            raise ValueError("native report tool call lacks a stable id")
        function = raw_call.get("function")
        if not isinstance(function, dict) or not isinstance(function.get("name"), str):
            raise ValueError("native report tool call lacks a function name")
        raw_arguments = function.get("arguments", "{}")
        if isinstance(raw_arguments, str):
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError as exc:
                raise ValueError("native report tool arguments are not valid JSON") from exc
        else:
            arguments = raw_arguments
        if not isinstance(arguments, dict):
            raise ValueError("native report tool arguments must be a JSON object")
        call = ReportToolCall.model_validate({"name": function["name"], "arguments": arguments})
        parsed.append((raw_call, call))
    return parsed


def load_turns(run_dir: Path, *, report_id: str) -> list[DiscussionTurn]:
    _manifest(run_dir, report_id)
    return _load_turns_unlocked(run_dir, report_id)


def load_messages(run_dir: Path, *, report_id: str) -> list[DiscussionMessage]:
    messages: list[DiscussionMessage] = []
    for turn in load_turns(run_dir, report_id=report_id):
        messages.append(DiscussionMessage(message_id=f"{turn.turn_id}:user", report_id=report_id, snapshot_content_sha256=turn.snapshot_content_sha256, role="user", content=turn.user_message, evidence_ids=turn.evidence_ids, created_at=turn.created_at))
        if turn.response is not None:
            messages.append(DiscussionMessage(message_id=f"{turn.turn_id}:assistant", report_id=report_id, snapshot_content_sha256=turn.snapshot_content_sha256, role="assistant", content=turn.response.answer, evidence_ids=turn.response.evidence_ids, created_at=turn.completed_at or turn.created_at))
    return messages


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
            if time.time() - path.stat().st_mtime > REPORT_REQUEST_TIMEOUT_SECONDS + 5:
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
