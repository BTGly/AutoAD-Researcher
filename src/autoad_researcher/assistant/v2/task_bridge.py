"""Approved bridge from dialogue summary state to Pipeline intake."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
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
from autoad_researcher.experiment.session import ExecutionMode
from autoad_researcher.schemas.intake import InputTask
from autoad_researcher.ui.sources import load_source_registry


BRIDGE_DIR = "task_bridge"
PENDING_TASK_FILE = "pending_experiment_task.json"
TASK_REPORT_FILE = "experiment_task_source_report.json"
INPUT_TASK_FILE = "input_task.yaml"
_SECRET_LIKE_RE = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")
TaskPreparationDisposition = Literal[
    "created",
    "reused",
    "replaced",
    "already_materialized",
    "recovery_required",
]


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
    execution_mode: ExecutionMode = "plan_only"
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


class ExperimentTaskConfirmationResult(BaseModel):
    """Confirm response with optional experiment-control-plane references."""

    model_config = ConfigDict(extra="forbid")

    task: ExperimentTaskDraft
    session_id: str | None = None
    session_status: str | None = None
    environment_job_id: str | None = None
    disposition: Literal["plan_only", "created", "repaired", "reused"]


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
        with _confirm_lock(run_dir):
            if (run_dir / INPUT_TASK_FILE).is_file():
                raise FileExistsError("input_task.yaml already exists")
            return _build_and_write_pending_task(
                run_dir,
                user_input=user_input,
                transcript_tail=transcript_tail,
            )

    @classmethod
    def prepare_or_reuse_experiment_task(
        cls,
        run_dir: Path,
        *,
        user_input: str,
        transcript_tail: list[dict[str, Any]] | None = None,
    ) -> tuple[ExperimentTaskDraft | None, TaskPreparationDisposition]:
        """Create, return, or safely refresh the one confirmable task draft."""
        with _confirm_lock(run_dir):
            if (run_dir / INPUT_TASK_FILE).is_file():
                return None, "already_materialized"

            summary = _require_preparable_summary(run_dir)
            pending_path = run_dir / BRIDGE_DIR / PENDING_TASK_FILE
            if pending_path.is_file():
                pending = _load_pending_task(run_dir)
                if pending.status == "confirmed":
                    return pending, "recovery_required"
                if pending.summary_sha256 == _summary_sha256(summary):
                    return pending, "reused"
                return (
                    _build_and_write_pending_task(
                        run_dir,
                        user_input=user_input,
                        transcript_tail=transcript_tail,
                        summary=summary,
                    ),
                    "replaced",
                )

            return (
                _build_and_write_pending_task(
                    run_dir,
                    user_input=user_input,
                    transcript_tail=transcript_tail,
                    summary=summary,
                ),
                "created",
            )

    @classmethod
    def confirm_or_load_existing(
        cls,
        run_dir: Path,
        *,
        task_id: str,
        execution_mode: ExecutionMode,
    ) -> ExperimentTaskDraft:
        run_id = _validate_run_dir(run_dir)
        with _confirm_lock(run_dir):
            draft = _load_pending_task(run_dir)
            if draft.task_id != task_id:
                raise ValueError("task_id does not match pending experiment task")

            if draft.status == "pending_confirmation":
                summary = load_research_intent_summary(run_dir)
                if summary is None or _summary_sha256(summary) != draft.summary_sha256:
                    raise ValueError("research summary changed after task preparation")
                _validate_existing_input_task(run_dir, draft)
                confirmed = draft.model_copy(
                    update={
                        "status": "confirmed",
                        "execution_mode": execution_mode,
                        "confirmed_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
                # The confirmed draft is the durable write-ahead record. All
                # materialized files below can be reconstructed from it.
                _write_json_atomic(
                    run_dir / BRIDGE_DIR / PENDING_TASK_FILE,
                    confirmed.model_dump(mode="json"),
                )
            else:
                confirmed = draft

            _materialize_input_task(run_dir, confirmed)
            _materialize_source_report(run_dir, run_id, confirmed)
            return confirmed

    @classmethod
    def confirm_experiment_task(
        cls,
        run_dir: Path,
        *,
        task_id: str,
        execution_mode: ExecutionMode,
    ) -> ExperimentTaskDraft:
        """Backward-compatible name for the reconcile-style confirmation API."""
        return cls.confirm_or_load_existing(
            run_dir,
            task_id=task_id,
            execution_mode=execution_mode,
        )


def _validate_run_dir(run_dir: Path) -> str:
    validate_run_id(run_dir.parent, run_dir.name)
    return run_dir.name


def _require_preparable_summary(run_dir: Path) -> ResearchIntentSummary:
    summary = load_research_intent_summary(run_dir)
    if summary is None or not summary.goal.strip():
        raise ValueError("research summary goal is required")
    if summary.blocking_question is not None:
        raise ValueError("blocking question must be resolved before task preparation")
    return summary


def _build_and_write_pending_task(
    run_dir: Path,
    *,
    user_input: str,
    transcript_tail: list[dict[str, Any]] | None,
    summary: ResearchIntentSummary | None = None,
) -> ExperimentTaskDraft:
    run_id = _validate_run_dir(run_dir)
    summary = summary or _require_preparable_summary(run_dir)
    request = _original_user_request(user_input, transcript_tail)
    input_task = InputTask(
        run_id=run_id,
        request=request,
        source_ids=_registered_source_ids(run_dir),
        user_idea=summary.goal,
        constraints=list(summary.confirmed_facts),
    )
    summary_sha256 = _summary_sha256(summary)
    draft = ExperimentTaskDraft(
        task_id=f"task_{summary_sha256[:16]}",
        run_id=run_id,
        input_task=input_task,
        evidence_refs=_evidence_refs(run_dir),
        summary_sha256=summary_sha256,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    _write_json_atomic(run_dir / BRIDGE_DIR / PENDING_TASK_FILE, draft.model_dump(mode="json"))
    return draft


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


def _materialize_input_task(run_dir: Path, confirmed: ExperimentTaskDraft) -> None:
    output_path = run_dir / INPUT_TASK_FILE
    output_text = yaml.safe_dump(
        confirmed.input_task.model_dump(mode="json", exclude_none=True),
        allow_unicode=True,
        sort_keys=False,
    )
    _reject_secret_like_text(output_text)
    if output_path.exists():
        _validate_existing_input_task(run_dir, confirmed)
        return
    _write_text_atomic(output_path, output_text)


def _validate_existing_input_task(run_dir: Path, draft: ExperimentTaskDraft) -> None:
    path = run_dir / INPUT_TASK_FILE
    if not path.exists():
        return
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        existing = InputTask.model_validate(data)
    except Exception as exc:
        raise ValueError("existing input_task.yaml is invalid") from exc
    if existing.model_dump(mode="json", exclude_none=True) != draft.input_task.model_dump(
        mode="json",
        exclude_none=True,
    ):
        raise ValueError("existing input_task.yaml conflicts with confirmed task")


def _materialize_source_report(
    run_dir: Path,
    run_id: str,
    confirmed: ExperimentTaskDraft,
) -> None:
    if confirmed.confirmed_at is None:
        raise ValueError("confirmed task is missing confirmed_at")
    report = ExperimentTaskSourceReport(
        run_id=run_id,
        task_id=confirmed.task_id,
        source_sha256=confirmed.summary_sha256,
        evidence_refs=confirmed.evidence_refs,
        confirmed_at=confirmed.confirmed_at,
    )
    path = run_dir / BRIDGE_DIR / TASK_REPORT_FILE
    if path.exists():
        try:
            existing = ExperimentTaskSourceReport.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError("existing experiment task source report is invalid") from exc
        if existing != report:
            raise ValueError("existing experiment task source report conflicts with confirmed task")
        return
    _write_json_atomic(path, report.model_dump(mode="json"))


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


@contextmanager
def _confirm_lock(run_dir: Path, timeout: float = 5.0):
    lock_path = run_dir / BRIDGE_DIR / ".confirm.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    fd = None
    while time.monotonic() < deadline:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
            break
        except FileExistsError:
            time.sleep(0.05)
    if fd is None:
        raise TimeoutError(f"Could not acquire confirm lock for {run_dir} within {timeout}s")
    try:
        yield
    finally:
        os.close(fd)
        try:
            os.unlink(lock_path)
        except OSError:
            pass
