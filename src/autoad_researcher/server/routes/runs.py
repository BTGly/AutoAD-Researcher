import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.core.run_id import run_dir_path
from autoad_researcher.assistant.v2.task_bridge import ExperimentTaskDraft, TaskBridge
from autoad_researcher.server.config import RUNS_ROOT
from autoad_researcher.server.routes.chat import TRANSCRIPT_RELATIVE_PATH
from autoad_researcher.task_workspace.task_profile import (
    archive_task,
    build_run_id_from_optional_name,
    create_task_profile,
    get_task_display_info,
    list_all_tasks,
    rename_task_title,
    restore_task,
)

router = APIRouter(prefix="/api/runs", tags=["runs"])


class RunInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    created_at: str | None = None
    updated_at: str | None = None
    sources_count: int = 0
    task_title: str
    task_summary: str
    task_source: str
    task_profile_warning: str | None = None
    archived_at: str | None = None


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_title: str | None = Field(default=None, max_length=30)


class RenameRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_title: str = Field(min_length=1, max_length=30)


class TranscriptItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    content: str
    created_at: str | None = None


@router.get("", response_model=list[RunInfo])
async def list_runs(include_archived: bool = Query(default=False)):
    runs_dir = Path(RUNS_ROOT)
    items = list_all_tasks(runs_root=runs_dir, include_archived=include_archived)
    return [_run_info(item.run_dir) for item in items]


@router.post("", response_model=RunInfo)
async def create_run(req: CreateRunRequest | None = None):
    now = datetime.now(timezone.utc)
    task_title = req.task_title if req is not None else None
    run_id = build_run_id_from_optional_name(task_name=task_title, now=now)
    run_dir = run_dir_path(RUNS_ROOT, run_id)
    if run_dir.exists():
        suffix = now.strftime("%f")
        run_id = f"{run_id}_{suffix}"
        run_dir = run_dir_path(RUNS_ROOT, run_id)
    run_dir.mkdir(parents=True, exist_ok=False)
    (run_dir / "sources").mkdir(exist_ok=True)
    (run_dir / "ui_chat").mkdir(exist_ok=True)
    (run_dir / "context").mkdir(exist_ok=True)
    (run_dir / "chat").mkdir(exist_ok=True)
    create_task_profile(run_dir=run_dir, run_id=run_id, task_title=task_title, created_at=now)
    return _run_info(run_dir)


@router.get("/{run_id}", response_model=RunInfo)
async def get_run(run_id: str):
    run_dir = _existing_run_dir(run_id)
    return _run_info(run_dir)


@router.patch("/{run_id}", response_model=RunInfo)
async def rename_run(run_id: str, req: RenameRunRequest):
    run_dir = _existing_run_dir(run_id)
    rename_task_title(
        run_dir=run_dir,
        new_title=req.task_title,
        updated_at=datetime.now(timezone.utc),
    )
    return _run_info(run_dir)


@router.post("/{run_id}/archive", response_model=RunInfo)
async def archive_run(run_id: str):
    run_dir = _existing_run_dir(run_id)
    archive_task(run_dir=run_dir, archived_at=datetime.now(timezone.utc))
    return _run_info(run_dir)


@router.post("/{run_id}/restore", response_model=RunInfo)
async def restore_run(run_id: str):
    run_dir = _existing_run_dir(run_id)
    restore_task(run_dir=run_dir)
    return _run_info(run_dir)


@router.delete("/{run_id}")
async def delete_run(run_id: str):
    run_dir = _existing_run_dir(run_id)
    shutil.rmtree(run_dir)
    return {"run_id": run_id, "deleted": True}


@router.get("/{run_id}/transcript", response_model=list[TranscriptItem])
async def get_run_transcript(run_id: str):
    run_dir = _existing_run_dir(run_id)
    path = run_dir / TRANSCRIPT_RELATIVE_PATH
    if not path.is_file():
        return []
    entries: list[TranscriptItem] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        role = payload.get("role")
        content = payload.get("content")
        if role in {"user", "assistant"} and isinstance(content, str):
            entries.append(TranscriptItem(
                role=role,
                content=content,
                created_at=_optional_str(payload.get("created_at") or payload.get("timestamp")),
            ))
    return entries


@router.post(
    "/{run_id}/experiment-task/{task_id}/confirm",
    response_model=ExperimentTaskDraft,
)
async def confirm_experiment_task(run_id: str, task_id: str):
    run_dir = _existing_run_dir(run_id)
    try:
        return TaskBridge.confirm_experiment_task(run_dir, task_id=task_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (FileExistsError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _existing_run_dir(run_id: str) -> Path:
    try:
        run_dir = run_dir_path(RUNS_ROOT, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not run_dir.is_dir():
        raise HTTPException(status_code=404, detail="run not found")
    return run_dir


def _run_info(run_dir: Path) -> RunInfo:
    overview = get_task_display_info(run_dir)
    return RunInfo(
        run_id=run_dir.name,
        created_at=_datetime_to_iso(_created_time(run_dir)),
        updated_at=_datetime_to_iso(_updated_time(run_dir)),
        sources_count=_sources_count(run_dir),
        task_title=str(overview["task_title"]),
        task_summary=str(overview["task_summary"]),
        task_source=str(overview["task_source"]),
        task_profile_warning=_optional_str(overview.get("task_profile_warning")),
        archived_at=_datetime_to_iso(overview.get("archived_at")),
    )


def _sources_count(run_dir: Path) -> int:
    sources_path = run_dir / "sources" / "source_references.json"
    if not sources_path.is_file():
        return 0
    try:
        reg = json.loads(sources_path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    sources = reg.get("sources", [])
    return len(sources) if isinstance(sources, list) else 0


def _created_time(run_dir: Path) -> datetime:
    return datetime.fromtimestamp(run_dir.stat().st_ctime, tz=timezone.utc)


def _updated_time(run_dir: Path) -> datetime:
    return datetime.fromtimestamp(run_dir.stat().st_mtime, tz=timezone.utc)


def _datetime_to_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
