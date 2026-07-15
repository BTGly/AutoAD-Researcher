"""Deterministic bridge from UI clarification data to pipeline intake."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.core.run_id import validate_run_id
from autoad_researcher.schemas.intake import InputTask
from autoad_researcher.ui.intent_draft import CLARIFICATION_INPUT_JSON, INTENT_DRAFT_DIR


INPUT_TASK_YAML = "input_task.yaml"
INPUT_TASK_SOURCE_REPORT_JSON = "input_task_source_report.json"
_SECRET_LIKE_RE = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")


class InputTaskSourceReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    source: Literal["ui_chat/clarification_input.json"] = "ui_chat/clarification_input.json"
    created_output: Literal["input_task.yaml"] = "input_task.yaml"
    source_sha256: str = Field(min_length=64, max_length=64)
    created_at: str


def build_input_task_from_clarification(run_dir: Path) -> InputTask:
    run_id = _validate_run_dir(run_dir)
    clarification_path = run_dir / INTENT_DRAFT_DIR / CLARIFICATION_INPUT_JSON
    clarification_data = _read_json_object(
        clarification_path,
        missing_message="missing clarification_input.json",
    )
    task_data = clarification_data.get("input_task")
    if not isinstance(task_data, dict):
        raise ValueError("clarification_input.json must contain input_task object")
    task = InputTask.model_validate(task_data)
    if task.run_id != run_id:
        raise ValueError("run_id mismatch: clarification_input.json")
    return task


def save_input_task_yaml_from_clarification(
    run_dir: Path,
    *,
    overwrite: bool = False,
) -> Path:
    run_id = _validate_run_dir(run_dir)
    output_path = run_dir / INPUT_TASK_YAML
    if output_path.exists() and not overwrite:
        raise FileExistsError("input_task.yaml already exists; set overwrite=True")
    task = build_input_task_from_clarification(run_dir)
    output_text = yaml.safe_dump(
        task.model_dump(mode="json", exclude_none=True),
        allow_unicode=True,
        sort_keys=False,
    )
    _reject_secret_like_text(output_text)
    run_dir.mkdir(parents=True, exist_ok=True)
    output_path.write_text(output_text, encoding="utf-8")
    _write_source_report(run_dir, run_id)
    return output_path


def get_intake_bridge_status(run_dir: Path) -> dict[str, Any]:
    try:
        run_id = _validate_run_dir(run_dir)
    except ValueError as exc:
        return {
            "valid_run_dir": False,
            "run_id": run_dir.name,
            "input_task_exists": False,
            "clarification_exists": False,
            "can_generate": False,
            "reason": str(exc),
        }
    input_task_exists = (run_dir / INPUT_TASK_YAML).is_file()
    clarification_exists = (
        run_dir / INTENT_DRAFT_DIR / CLARIFICATION_INPUT_JSON
    ).is_file()
    return {
        "valid_run_dir": True,
        "run_id": run_id,
        "input_task_exists": input_task_exists,
        "clarification_exists": clarification_exists,
        "can_generate": clarification_exists,
        "reason": None if clarification_exists else "missing clarification_input.json",
    }


def _validate_run_dir(run_dir: Path) -> str:
    validate_run_id(run_dir.parent, run_dir.name)
    return run_dir.name


def _read_json_object(path: Path, *, missing_message: str) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(missing_message)
    text = path.read_text(encoding="utf-8")
    _reject_secret_like_text(text)
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must be a JSON object")
    return data


def _reject_secret_like_text(text: str) -> None:
    if _SECRET_LIKE_RE.search(text):
        raise ValueError("secret-like content forbidden")


def _write_source_report(run_dir: Path, run_id: str) -> Path:
    clarification_path = run_dir / INTENT_DRAFT_DIR / CLARIFICATION_INPUT_JSON
    report = InputTaskSourceReport(
        run_id=run_id,
        source_sha256=_sha256_file(clarification_path),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    report_path = run_dir / INTENT_DRAFT_DIR / INPUT_TASK_SOURCE_REPORT_JSON
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n",
        encoding="utf-8",
    )
    return report_path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
