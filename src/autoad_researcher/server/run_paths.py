"""Shared, validated mapping from an API run_id to its configured run directory."""

from pathlib import Path

from fastapi import HTTPException

from autoad_researcher.core.run_id import run_dir_path


def run_dir_or_400(runs_root: str | Path, run_id: str) -> Path:
    try:
        return run_dir_path(runs_root, run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
