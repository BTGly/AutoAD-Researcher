"""Append-only observability for Coordinator and specialist model usage."""
from __future__ import annotations

import json
import hashlib
import os
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.assistant.v2.event_service import append_event

COGNITIVE_USAGE_DIR = "experiments/cognition"


class CognitiveUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    cycle_id: str = Field(min_length=1)
    cycle_kind: Literal["compact", "exploratory"]
    role: Literal["coordinator", "idea_explorer"]
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    wall_seconds: float = Field(ge=0)
    created_at: str


class CognitiveUsageReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recorded: bool = True
    usage_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class CognitiveUsageStore:
    """Append usage records; this ledger never gates a model call."""

    def load(self, run_dir: Path, *, session_id: str) -> list[CognitiveUsage]:
        path = self._path(run_dir, session_id)
        if not path.is_file():
            return []
        return [CognitiveUsage.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def append(self, run_dir: Path, *, session_id: str, usage: CognitiveUsage) -> CognitiveUsageReceipt:
        with self._lock(run_dir, session_id):
            path = self._path(run_dir, session_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(usage.model_dump_json() + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        append_event(run_dir, "experiment.cognitive_usage.recorded", {"session_id": session_id, **usage.model_dump(mode="json"), "usage_sha256": digest})
        return CognitiveUsageReceipt(usage_sha256=digest)

    @staticmethod
    def _path(run_dir: Path, session_id: str) -> Path:
        return run_dir / COGNITIVE_USAGE_DIR / session_id / "llm_usage.jsonl"

    @staticmethod
    @contextmanager
    def _lock(run_dir: Path, session_id: str, timeout: float = 5.0):
        path = run_dir / COGNITIVE_USAGE_DIR / session_id / ".llm_usage.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout
        fd: int | None = None
        while time.monotonic() < deadline:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                break
            except FileExistsError:
                time.sleep(0.05)
        if fd is None:
            raise TimeoutError("could not acquire CognitiveUsage lock")
        try:
            yield
        finally:
            os.close(fd)
            try:
                path.unlink()
            except OSError:
                pass


def new_usage(*, cycle_id: str, cycle_kind: Literal["compact", "exploratory"], role: Literal["coordinator", "idea_explorer"], input_tokens: int, output_tokens: int, wall_seconds: float) -> CognitiveUsage:
    return CognitiveUsage(cycle_id=cycle_id, cycle_kind=cycle_kind, role=role, input_tokens=input_tokens, output_tokens=output_tokens, wall_seconds=wall_seconds, created_at=datetime.now(timezone.utc).isoformat())
