"""FastAPI adapters for durable Run lifecycle leases."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from fastapi import HTTPException

from autoad_researcher.core.run_lifecycle import (
    RunLifecycleGone,
    RunLifecycleRecord,
    run_operation_lease,
)
from autoad_researcher.server.config import RUNS_ROOT


@contextmanager
def active_run_lease(
    run_id: str,
    *,
    runs_root: str | None = None,
) -> Iterator[RunLifecycleRecord]:
    """Translate lifecycle failures into stable HTTP status codes."""

    try:
        with run_operation_lease(runs_root or RUNS_ROOT, run_id) as record:
            yield record
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="run not found") from exc
    except RunLifecycleGone as exc:
        raise HTTPException(
            status_code=410,
            detail={"code": "run_gone", "status": exc.status, "message": str(exc)},
        ) from exc
