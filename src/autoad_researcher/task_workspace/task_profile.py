"""Task profile — human-readable task title for UI display.

The task profile provides a user-facing task name and summary, generated
from the first research chat message. It does NOT replace run_id — run_id
remains the canonical artifact key for file paths, CLI, and approvals.
"""

from __future__ import annotations

import json
import re
import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from autoad_researcher.core.control_plane import RunMutationLock
from autoad_researcher.core.run_lifecycle import run_operation_lease


_TASK_TITLE_MAX_CHARS = 30
_TASK_SUMMARY_MAX_CHARS = 200
_SK_SECRET_PATTERN = re.compile(r"sk-[a-zA-Z0-9]{8,}")
_RUN_ID_PREFIX_PATTERN = re.compile(r"^run_\d{8}_\d{4}", re.IGNORECASE)
_TASK_PROFILE_FILENAME = "task_profile.json"
_TASK_ARCHIVE_FILENAME = "task_archive.json"
_SLUG_PATTERN = re.compile(r"[^a-z0-9_.-]+")


class TaskProfile(BaseModel):
    """Human-readable task identity, persisted as ui_chat/task_profile.json."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    task_title: str = Field(min_length=1, max_length=_TASK_TITLE_MAX_CHARS)
    task_summary: str = Field(min_length=1, max_length=_TASK_SUMMARY_MAX_CHARS)
    source: Literal["llm_first_user_instruction", "manual", "fallback", "ui", "legacy_import"]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime | None = None

    @field_validator("task_title")
    @classmethod
    def _reject_secrets_and_run_id(cls, v: str) -> str:
        if _SK_SECRET_PATTERN.search(v):
            raise ValueError("task_title must not contain secret-like text (sk-…)")
        if _RUN_ID_PREFIX_PATTERN.match(v):
            raise ValueError("task_title must not be a run_id")
        return v

    @field_validator("task_summary")
    @classmethod
    def _reject_secrets_in_summary(cls, v: str) -> str:
        if _SK_SECRET_PATTERN.search(v):
            raise ValueError("task_summary must not contain secret-like text (sk-…)")
        return v


class TaskListItem(BaseModel):
    """One run directory as shown in the UI task picker."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    task_title: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    profile_path: Path | None = None
    run_dir: Path
    source: Literal["profile", "fallback"] = "fallback"
    profile_warning: str | None = None
    archived_at: datetime | None = None


class TaskArchiveState(BaseModel):
    """Non-destructive archive marker for hiding tasks from the default picker."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    archived_at: datetime


# ---------------------------------------------------------------------------
# load / save
# ---------------------------------------------------------------------------


def _profile_path(run_dir: Path) -> Path:
    return run_dir / "ui_chat" / _TASK_PROFILE_FILENAME


def _archive_path(run_dir: Path) -> Path:
    return run_dir / "ui_chat" / _TASK_ARCHIVE_FILENAME


def load_task_profile(run_dir: Path) -> TaskProfile | None:
    """Return the persisted task profile, or None if not found."""
    path = _profile_path(run_dir)
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return TaskProfile.model_validate(raw)


def safe_load_task_profile(run_dir: Path) -> tuple[TaskProfile | None, str | None]:
    """Return a task profile for UI display without raising on corrupt files."""
    try:
        return load_task_profile(run_dir), None
    except Exception as exc:
        return None, f"task_profile_invalid:{type(exc).__name__}"


def load_task_archive_state(run_dir: Path) -> TaskArchiveState | None:
    """Return the archive marker, or None if this task is not archived."""
    path = _archive_path(run_dir)
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return TaskArchiveState.model_validate(raw)


def safe_load_task_archive_state(run_dir: Path) -> tuple[TaskArchiveState | None, str | None]:
    """Return an archive marker without raising on corrupt archive metadata."""
    try:
        return load_task_archive_state(run_dir), None
    except Exception as exc:
        return None, f"task_archive_invalid:{type(exc).__name__}"


def archive_task(*, run_dir: Path, archived_at: datetime) -> TaskArchiveState:
    """Hide a task from the default picker without deleting its artifacts."""
    state = TaskArchiveState(archived_at=archived_at)
    path = _archive_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)
    return state


def restore_task(*, run_dir: Path) -> None:
    """Remove the archive marker so the task appears in the default picker."""
    path = _archive_path(run_dir)
    try:
        path.unlink()
    except FileNotFoundError:
        return


def delete_archived_task(*, run_dir: Path) -> None:
    """Physically delete an archived task directory."""
    if not run_dir.is_dir():
        raise FileNotFoundError("task directory does not exist")
    archive_state, archive_warning = safe_load_task_archive_state(run_dir)
    if archive_state is None:
        raise ValueError(archive_warning or "task must be archived before deletion")
    shutil.rmtree(run_dir)


def save_task_profile(run_dir: Path, profile: TaskProfile) -> Path:
    """Atomically persist *profile*, refusing to overwrite an existing file."""
    path = _profile_path(run_dir)
    if path.exists():
        raise FileExistsError(f"task profile already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        profile.model_dump_json(indent=2), encoding="utf-8"
    )
    tmp.replace(path)
    return path


def _write_task_profile(run_dir: Path, profile: TaskProfile) -> Path:
    """Atomically persist *profile*, allowing overwrite."""
    path = _profile_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def slugify_task_name(task_name: str) -> str:
    """Return a run_id-safe slug for a human task name."""
    slug = _SLUG_PATTERN.sub("_", task_name.strip().lower()).strip("_.-")
    slug = re.sub(r"_+", "_", slug)
    return slug[:48].strip("_.-") or "task"


def build_run_id_from_optional_name(*, task_name: str | None, now: datetime) -> str:
    """Build a canonical run_id without changing the human title."""
    timestamp = now.strftime("%Y%m%d_%H%M")
    digest = hashlib.md5(f"{task_name or ''}:{now.isoformat()}".encode("utf-8")).hexdigest()[:4]
    if task_name and task_name.strip():
        return f"{slugify_task_name(task_name)}_{now.strftime('%H%M')}_{digest}"
    return f"run_{timestamp}_{digest}"


def create_task_profile(
    *,
    run_dir: Path,
    run_id: str,
    task_title: str | None,
    created_at: datetime,
) -> TaskProfile:
    """Create and persist a UI task profile for a new run."""
    title = task_title.strip() if task_title and task_title.strip() else "未命名研究任务"
    profile = TaskProfile(
        run_id=run_id,
        task_title=title,
        task_summary="用户创建的研究任务。",
        source="ui",
        created_at=created_at,
        updated_at=created_at,
    )
    _write_task_profile(run_dir, profile)
    return profile


def ensure_legacy_task_profile(run_dir: Path) -> TaskProfile:
    """Give an imported pre-UI Run a safe display identity without exposing run_id."""

    existing, warning = safe_load_task_profile(run_dir)
    if existing is not None:
        return existing
    if warning is not None:
        raise ValueError(warning)
    timestamp = datetime.fromtimestamp(run_dir.stat().st_ctime, tz=timezone.utc)
    profile = TaskProfile(
        run_id=run_dir.name,
        task_title="历史研究任务",
        task_summary="从旧版本运行目录导入的研究任务。",
        source="legacy_import",
        created_at=timestamp,
        updated_at=timestamp,
    )
    try:
        save_task_profile(run_dir, profile)
        return profile
    except FileExistsError:
        loaded = load_task_profile(run_dir)
        if loaded is None:
            raise
        return loaded


def rename_task_title(*, run_dir: Path, new_title: str, updated_at: datetime) -> TaskProfile:
    """Rename a task profile without changing run_id or artifact paths."""
    title = new_title.strip()
    if not title:
        raise ValueError("task title must not be empty")
    with RunMutationLock(run_dir, mode="exclusive"):
        existing, _warning = safe_load_task_profile(run_dir)
        if existing is None:
            existing = fallback_task_profile(run_dir.name)
        profile = existing.model_copy(update={
            "task_title": title,
            "updated_at": updated_at,
            "source": "manual",
        })
        profile = TaskProfile.model_validate(profile.model_dump())
        _write_task_profile(run_dir, profile)
        return profile


def task_profile_needs_generated_title(run_dir: Path) -> bool:
    """Return whether the persisted UI placeholder is eligible for automatic naming."""
    profile, warning = safe_load_task_profile(run_dir)
    return bool(
        warning is None
        and profile is not None
        and profile.source == "ui"
        and profile.task_title == "未命名研究任务"
    )


def apply_generated_task_profile_if_placeholder(
    *,
    run_dir: Path,
    generated_profile: TaskProfile,
    updated_at: datetime,
) -> TaskProfile | None:
    """Persist an LLM-generated profile only while the original UI placeholder remains."""
    if (
        generated_profile.run_id != run_dir.name
        or generated_profile.source != "llm_first_user_instruction"
        or generated_profile.task_title == "未命名研究任务"
    ):
        return None

    with RunMutationLock(run_dir, mode="exclusive"):
        existing, warning = safe_load_task_profile(run_dir)
        if (
            warning is not None
            or existing is None
            or existing.source != "ui"
            or existing.task_title != "未命名研究任务"
        ):
            return None
        profile = existing.model_copy(update={
            "task_title": generated_profile.task_title,
            "task_summary": generated_profile.task_summary,
            "source": "llm_first_user_instruction",
            "updated_at": updated_at,
        })
        profile = TaskProfile.model_validate(profile.model_dump())
        _write_task_profile(run_dir, profile)
        return profile


def list_all_tasks(*, runs_root: Path, include_archived: bool = False) -> list[TaskListItem]:
    """Scan one-level run directories for UI task selection."""
    if not runs_root.is_dir():
        return []

    items: list[TaskListItem] = []
    for run_dir in sorted(runs_root.iterdir()):
        if not run_dir.is_dir() or run_dir.name.startswith("."):
            continue
        try:
            lease = run_operation_lease(runs_root, run_dir.name)
            lease.__enter__()
        except (FileNotFoundError, RuntimeError, ValueError):
            continue
        try:
            profile_path = _profile_path(run_dir)
            profile, warning = safe_load_task_profile(run_dir)
            archive_state, archive_warning = safe_load_task_archive_state(run_dir)
            if archive_warning and warning:
                warning = f"{warning};{archive_warning}"
            elif archive_warning:
                warning = archive_warning
            archived_at = archive_state.archived_at if archive_state else None
            if archived_at is not None and not include_archived:
                continue

            if profile is not None:
                item = TaskListItem(
                    run_id=run_dir.name,
                    task_title=profile.task_title,
                    created_at=profile.created_at,
                    updated_at=profile.updated_at,
                    profile_path=profile_path,
                    run_dir=run_dir,
                    source="profile",
                    profile_warning=warning,
                    archived_at=archived_at,
                )
            else:
                mtime = datetime.fromtimestamp(run_dir.stat().st_mtime, tz=timezone.utc)
                item = TaskListItem(
                    run_id=run_dir.name,
                    task_title="历史研究任务",
                    created_at=mtime,
                    updated_at=mtime,
                    profile_path=profile_path if profile_path.exists() else None,
                    run_dir=run_dir,
                    source="fallback",
                    profile_warning=warning,
                    archived_at=archived_at,
                )
            items.append(item)
        finally:
            lease.__exit__(None, None, None)

    def sort_key(item: TaskListItem) -> datetime:
        return item.updated_at or item.created_at or datetime.fromtimestamp(item.run_dir.stat().st_mtime, tz=timezone.utc)

    return sorted(items, key=sort_key, reverse=True)


def format_task_list_label(item: TaskListItem) -> str:
    stamp = item.updated_at or item.created_at
    suffix = " · 已归档" if item.archived_at is not None else ""
    if stamp is None:
        return f"{item.task_title}{suffix}"
    return f"{item.task_title} ({stamp.strftime('%Y-%m-%d %H:%M')}){suffix}"


# ---------------------------------------------------------------------------
# fallback
# ---------------------------------------------------------------------------


def fallback_task_profile(run_id: str) -> TaskProfile:
    """Return a placeholder profile when no task name has been generated."""
    return TaskProfile(
        run_id=run_id,
        task_title="未命名研究任务",
        task_summary="尚未生成任务摘要。请在研究助手中描述研究目标，系统将自动生成任务名。",
        source="fallback",
    )


# ---------------------------------------------------------------------------
# LLM-based generation
# ---------------------------------------------------------------------------


_GENERATE_SYSTEM_PROMPT = """你是一个任务命名助手。根据用户的研究描述，生成一个简短的任务名和一句话摘要。

要求：
- task_title: 中文 6-14 字，或英文 3-8 个词
- 必须具体表达本次研究目标，不能泛泛写成"研究任务""异常检测研究"
- 不能包含 run_id
- 不能包含路径
- 不能包含 API key
- task_summary: 一句话描述研究目标，不超过 100 字

仅输出 JSON，不要输出解释、markdown 或其他文字。格式：
{"task_title": "...", "task_summary": "..."}"""


def generate_task_profile_from_first_message(
    run_dir: Path,
    api_key: str,
    provider_base_url: str,
    first_user_message: str,
    model: str = "deepseek-chat",
    timeout_s: int = 15,
) -> TaskProfile:
    """Call LLM to generate a task profile from the first user message.

    On any failure (network, timeout, malformed JSON, validation error)
    returns a fallback profile instead of raising.
    """
    run_id = run_dir.name

    import httpx

    base = provider_base_url.rstrip("/")
    if base.endswith("/v1"):
        url = base + "/chat/completions"
    else:
        url = base + "/v1/chat/completions"

    try:
        resp = httpx.post(
            url,
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": _GENERATE_SYSTEM_PROMPT},
                    {"role": "user", "content": first_user_message},
                ],
                "temperature": 0.1,
                "max_tokens": 256,
            },
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout_s,
        )
    except Exception:
        return fallback_task_profile(run_id)

    if resp.status_code != 200:
        return fallback_task_profile(run_id)

    try:
        body = resp.json()
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError):
        return fallback_task_profile(run_id)

    # Extract JSON block
    json_match = re.search(r"\{[^{}]*\}", content, re.DOTALL)
    if not json_match:
        return fallback_task_profile(run_id)

    try:
        parsed = json.loads(json_match.group(0))
    except json.JSONDecodeError:
        return fallback_task_profile(run_id)

    title = str(parsed.get("task_title", "")).strip()
    summary = str(parsed.get("task_summary", "")).strip()

    if not title or not summary:
        return fallback_task_profile(run_id)

    try:
        return TaskProfile(
            run_id=run_id,
            task_title=title,
            task_summary=summary,
            source="llm_first_user_instruction",
        )
    except Exception:
        return fallback_task_profile(run_id)


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------


def get_task_title(run_dir: Path) -> str:
    """Return the human-readable task title for UI display."""
    profile, _warning = safe_load_task_profile(run_dir)
    if profile is not None:
        return profile.task_title
    return run_dir.name


def get_task_display_info(run_dir: Path) -> dict[str, Any]:
    """Return a dict with task_title, task_summary, run_id, and artifact_dir for UI rendering."""
    profile, warning = safe_load_task_profile(run_dir)
    archive_state, archive_warning = safe_load_task_archive_state(run_dir)
    if archive_warning and warning:
        warning = f"{warning};{archive_warning}"
    elif archive_warning:
        warning = archive_warning
    if profile is not None:
        task_title = profile.task_title
        task_summary = profile.task_summary
        task_source = profile.source
        run_id = profile.run_id
    else:
        fallback = fallback_task_profile(run_dir.name)
        task_title = "历史研究任务"
        task_summary = fallback.task_summary
        task_source = fallback.source
        run_id = run_dir.name

    return {
        "task_title": task_title,
        "task_summary": task_summary,
        "task_source": task_source,
        "task_profile_warning": warning,
        "archived_at": archive_state.archived_at if archive_state else None,
        "run_id": run_id,
        "artifact_dir": str(run_dir),
    }
