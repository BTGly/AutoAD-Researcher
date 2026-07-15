"""Approved bridge from dialogue summary state to Pipeline intake."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.assistant.v2.evidence_service import load_usable_evidence
from autoad_researcher.assistant.v2.research_intent_summary import (
    ResearchIntentSummary,
    load_research_intent_summary,
)
from autoad_researcher.core.run_id import validate_run_id
from autoad_researcher.schemas.intake import InputTask
from autoad_researcher.ui.sources import load_source_registry


BRIDGE_DIR = "task_bridge"
PENDING_TASK_FILE = "pending_experiment_task.json"
TASK_REPORT_FILE = "experiment_task_source_report.json"
INPUT_TASK_FILE = "input_task.yaml"
_SECRET_LIKE_RE = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")


class TaskInstruction(BaseModel):
    """Request to prepare, but not execute, a Pipeline intake task."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["prepare_experiment_task"]


class ExperimentTaskDraft(BaseModel):
    """User-confirmable projection into the existing InputTask contract."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    status: Literal["pending_confirmation", "confirmed"] = "pending_confirmation"
    execution_mode: Literal["plan_only"] = "plan_only"
    input_task: InputTask
    evidence_refs: list[str] = Field(default_factory=list)
    summary_sha256: str = Field(min_length=64, max_length=64)
    created_at: str
    confirmed_at: str | None = None


class ExperimentTaskSourceReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str
    task_id: str
    source: Literal["summary.json"] = "summary.json"
    source_sha256: str = Field(min_length=64, max_length=64)
    created_output: Literal["input_task.yaml"] = "input_task.yaml"
    evidence_refs: list[str] = Field(default_factory=list)
    confirmed_at: str


class TaskBridge:
    """Build and confirm a plan-only Pipeline intake without running it."""

    @classmethod
    def build_experiment_task(
        cls,
        run_dir: Path,
        *,
        user_input: str,
        transcript_tail: list[dict[str, Any]] | None = None,
    ) -> ExperimentTaskDraft:
        run_id = _validate_run_dir(run_dir)
        if (run_dir / INPUT_TASK_FILE).is_file():
            raise FileExistsError("input_task.yaml already exists")
        summary = load_research_intent_summary(run_dir)
        if summary is None or not summary.goal.strip():
            raise ValueError("research summary goal is required")
        if summary.blocking_question is not None:
            raise ValueError("blocking question must be resolved before task preparation")

        request = _original_user_request(user_input, transcript_tail)
        source_ids = _registered_source_ids(run_dir)
        input_task = InputTask(
            run_id=run_id,
            request=request,
            source_ids=source_ids,
            user_idea=summary.goal,
            constraints=list(summary.confirmed_facts),
        )
        summary_sha256 = _summary_sha256(summary)
        task_id = f"task_{summary_sha256[:16]}"
        draft = ExperimentTaskDraft(
            task_id=task_id,
            run_id=run_id,
            input_task=input_task,
            evidence_refs=_evidence_refs(run_dir),
            summary_sha256=summary_sha256,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        _write_json_atomic(run_dir / BRIDGE_DIR / PENDING_TASK_FILE, draft.model_dump(mode="json"))
        return draft

    @classmethod
    def confirm_experiment_task(
        cls,
        run_dir: Path,
        *,
        task_id: str,
    ) -> ExperimentTaskDraft:
        run_id = _validate_run_dir(run_dir)
        output_path = run_dir / INPUT_TASK_FILE
        if output_path.exists():
            raise FileExistsError("input_task.yaml already exists")
        draft = _load_pending_task(run_dir)
        if draft.task_id != task_id:
            raise ValueError("task_id does not match pending experiment task")
        summary = load_research_intent_summary(run_dir)
        if summary is None or _summary_sha256(summary) != draft.summary_sha256:
            raise ValueError("research summary changed after task preparation")

        output_text = yaml.safe_dump(
            draft.input_task.model_dump(mode="json", exclude_none=True),
            allow_unicode=True,
            sort_keys=False,
        )
        _reject_secret_like_text(output_text)
        _write_text_atomic(output_path, output_text)
        confirmed_at = datetime.now(timezone.utc).isoformat()
        confirmed = draft.model_copy(update={"status": "confirmed", "confirmed_at": confirmed_at})
        report = ExperimentTaskSourceReport(
            run_id=run_id,
            task_id=task_id,
            source_sha256=draft.summary_sha256,
            evidence_refs=draft.evidence_refs,
            confirmed_at=confirmed_at,
        )
        _write_json_atomic(
            run_dir / BRIDGE_DIR / TASK_REPORT_FILE,
            report.model_dump(mode="json"),
        )
        _write_json_atomic(
            run_dir / BRIDGE_DIR / PENDING_TASK_FILE,
            confirmed.model_dump(mode="json"),
        )
        return confirmed


def _validate_run_dir(run_dir: Path) -> str:
    validate_run_id(run_dir.parent, run_dir.name)
    return run_dir.name


def _original_user_request(
    user_input: str,
    transcript_tail: list[dict[str, Any]] | None,
) -> str:
    turns = [
        str(item.get("content") or "").strip()
        for item in (transcript_tail or [])[-12:]
        if item.get("role") == "user" and str(item.get("content") or "").strip()
    ]
    current = user_input.strip()
    if current:
        turns.append(current)
    request = "\n\n".join(turns)
    if not request:
        raise ValueError("at least one original user message is required")
    _reject_secret_like_text(request)
    return request


def _registered_source_ids(run_dir: Path) -> list[str]:
    sources = load_source_registry(run_dir).get("sources", [])
    return [
        str(source.get("source_id"))
        for source in sources
        if isinstance(source, dict) and source.get("source_id")
    ]


def _evidence_refs(run_dir: Path) -> list[str]:
    return list(dict.fromkeys(
        str(item.get("artifact_path"))
        for item in load_usable_evidence(run_dir)
        if item.get("artifact_path")
    ))


def _summary_sha256(summary: ResearchIntentSummary) -> str:
    payload = json.dumps(
        summary.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _load_pending_task(run_dir: Path) -> ExperimentTaskDraft:
    path = run_dir / BRIDGE_DIR / PENDING_TASK_FILE
    if not path.is_file():
        raise FileNotFoundError("pending experiment task not found")
    _reject_secret_like_text(path.read_text(encoding="utf-8"))
    return ExperimentTaskDraft.model_validate_json(path.read_text(encoding="utf-8"))


def _reject_secret_like_text(text: str) -> None:
    if _SECRET_LIKE_RE.search(text):
        raise ValueError("secret-like content forbidden")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _write_text_atomic(path, text)


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with tmp.open("wb") as handle:
            handle.write(text.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
