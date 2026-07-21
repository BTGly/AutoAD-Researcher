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

from autoad_researcher.reporting.evidence import EvidenceIndex
from autoad_researcher.reporting.store import ReportStore

MAX_TURNS = 40
MAX_MESSAGE_CHARS = 8000


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


def respond_to_turn(run_dir: Path, *, report_id: str, turn_id: str, api_key: str, provider_url: str, model: str) -> DiscussionTurn:
    """Complete one persisted turn using only frozen report summaries and evidence."""
    turn = _require_turn(load_turns(run_dir, report_id=report_id), turn_id)
    if turn.status != "pending": return turn
    if not api_key or not model:
        return fail_turn(run_dir, report_id=report_id, turn_id=turn_id, error="report discussion model is not configured")
    directory = run_dir / "reports" / report_id
    digest = json.loads((directory / "report_digest.json").read_text(encoding="utf-8"))
    index = EvidenceIndex.model_validate_json((directory / "evidence_index.json").read_text(encoding="utf-8"))
    evidence = [{"evidence_id": item.evidence_id, "kind": item.evidence_kind, "summary": item.summary, "attempt_id": item.attempt_id, "idea_id": item.idea_id} for item in index.entries[:24]]
    messages = [
        {"role": "system", "content": "You answer only from the frozen report context. Return one JSON object with answer, response_kind, evidence_ids, unsupported_claims. Never claim execution, file access, new metrics, or unlisted evidence."},
        {"role": "user", "content": json.dumps({"report_id": report_id, "snapshot_sha256": turn.snapshot_content_sha256, "digest": digest, "evidence": evidence, "question": turn.user_message}, ensure_ascii=False)},
    ]
    from autoad_researcher.ui.chat_client import call_research_chat
    result = call_research_chat(api_key, provider_url, messages, model=model, timeout_s=30, priority="interactive", response_format_json=True, max_tokens=700, temperature=0.1)
    try:
        if result.get("error"): raise ValueError(str(result["error"]))
        response = DiscussionResponse.model_validate_json(str(result.get("reply") or ""))
        _validated_evidence_ids(run_dir, report_id, response.evidence_ids)
    except Exception as exc:
        return fail_turn(run_dir, report_id=report_id, turn_id=turn_id, error=str(exc))
    return complete_turn(run_dir, report_id=report_id, turn_id=turn_id, response=response)


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
