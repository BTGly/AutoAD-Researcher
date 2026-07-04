"""Task profile — human-readable task title for UI display.

The task profile provides a user-facing task name and summary, generated
from the first research chat message. It does NOT replace run_id — run_id
remains the canonical artifact key for file paths, CLI, and approvals.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


_TASK_TITLE_MAX_CHARS = 30
_TASK_SUMMARY_MAX_CHARS = 200
_SK_SECRET_PATTERN = re.compile(r"sk-[a-zA-Z0-9]{8,}")
_RUN_ID_PREFIX_PATTERN = re.compile(r"^run_\d{8}_\d{4}", re.IGNORECASE)
_TASK_PROFILE_FILENAME = "task_profile.json"


class TaskProfile(BaseModel):
    """Human-readable task identity, persisted as ui_chat/task_profile.json."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    task_title: str = Field(min_length=1, max_length=_TASK_TITLE_MAX_CHARS)
    task_summary: str = Field(min_length=1, max_length=_TASK_SUMMARY_MAX_CHARS)
    source: Literal["llm_first_user_instruction", "manual", "fallback"]
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

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


# ---------------------------------------------------------------------------
# load / save
# ---------------------------------------------------------------------------


def _profile_path(run_dir: Path) -> Path:
    return run_dir / "ui_chat" / _TASK_PROFILE_FILENAME


def load_task_profile(run_dir: Path) -> TaskProfile | None:
    """Return the persisted task profile, or None if not found."""
    path = _profile_path(run_dir)
    if not path.is_file():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    return TaskProfile.model_validate(raw)


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
    profile = load_task_profile(run_dir)
    if profile is not None:
        return profile.task_title
    fallback = fallback_task_profile(run_dir.name)
    return fallback.task_title


def get_task_display_info(run_dir: Path) -> dict[str, Any]:
    """Return a dict with task_title, task_summary, run_id, and artifact_dir for UI rendering."""
    profile = load_task_profile(run_dir)
    if profile is None:
        profile = fallback_task_profile(run_dir.name)

    return {
        "task_title": profile.task_title,
        "task_summary": profile.task_summary,
        "task_source": profile.source,
        "run_id": profile.run_id,
        "artifact_dir": str(run_dir),
    }
