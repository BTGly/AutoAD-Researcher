"""Application-owned, bounded transcript for a frozen report discussion."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.reporting.evidence import EvidenceIndex
from autoad_researcher.reporting.store import ReportStore

MAX_MESSAGES = 40
MAX_MESSAGE_CHARS = 8000


class DiscussionMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message_id: str = Field(min_length=1)
    report_id: str
    snapshot_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    role: str
    content: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: str


def load_messages(run_dir: Path, *, report_id: str) -> list[DiscussionMessage]:
    _manifest(run_dir, report_id)
    path = _path(run_dir, report_id)
    if not path.is_file():
        return []
    values = [DiscussionMessage.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return values[-MAX_MESSAGES:]


def append_message(run_dir: Path, *, report_id: str, role: str, content: str, evidence_ids: list[str] | None = None) -> DiscussionMessage:
    manifest = _manifest(run_dir, report_id)
    if role not in {"user", "assistant"}:
        raise ValueError("discussion role must be user or assistant")
    index = EvidenceIndex.model_validate_json((run_dir / "reports" / report_id / "evidence_index.json").read_text(encoding="utf-8"))
    ids = evidence_ids or []
    if not set(ids).issubset({item.evidence_id for item in index.entries}):
        raise ValueError("discussion references unknown Evidence IDs")
    message = DiscussionMessage(
        message_id=f"message_{uuid4().hex}", report_id=report_id,
        snapshot_content_sha256=manifest.source_snapshot_content_sha256, role=role,
        content=content, evidence_ids=ids, created_at=datetime.now(timezone.utc).isoformat(),
    )
    path = _path(run_dir, report_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(message.model_dump_json() + "\n")
        handle.flush(); os.fsync(handle.fileno())
    return message


def _manifest(run_dir: Path, report_id: str):
    return ReportStore().load_manifest(run_dir, report_id)


def _path(run_dir: Path, report_id: str) -> Path:
    return run_dir / "reports" / report_id / "discussion" / "messages.jsonl"
