"""One-lock unit of work for future cross-store control-plane operations."""

from __future__ import annotations

from pathlib import Path
from types import TracebackType

from autoad_researcher.core.control_plane.job_store import PipelineJobStore
from autoad_researcher.core.control_plane.lock import RunMutationLock


class ControlPlaneUnitOfWork:
    """Hold the run mutation lock once and expose bound store implementations."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = Path(run_dir)
        self._lock = RunMutationLock(self.run_dir, mode="exclusive")
        self.jobs = PipelineJobStore(self.run_dir)

    def __enter__(self) -> "ControlPlaneUnitOfWork":
        self._lock.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._lock.__exit__(exc_type, exc, traceback)
