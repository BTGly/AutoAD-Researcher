"""Per-run advisory locking for authoritative control-plane mutations."""

from __future__ import annotations

import fcntl
import os
from contextvars import ContextVar, Token
from pathlib import Path
from types import TracebackType
from typing import Literal

from autoad_researcher.core.control_plane.errors import (
    ControlPlaneLockError,
    ControlPlaneLockReentryError,
)


LockMode = Literal["shared", "exclusive"]
_ACTIVE_LOCKS: ContextVar[frozenset[str]] = ContextVar("autoad_control_plane_locks", default=frozenset())


class AdvisoryFileLock:
    """Process-safe flock wrapper whose lock file may safely persist."""

    def __init__(self, path: Path, *, mode: LockMode) -> None:
        self.path = path
        self.mode = mode
        self._fd: int | None = None
        self._token: Token[frozenset[str]] | None = None

    def __enter__(self) -> "AdvisoryFileLock":
        key = str(self.path.resolve(strict=False))
        active = _ACTIVE_LOCKS.get()
        if key in active:
            raise ControlPlaneLockReentryError(f"control-plane lock reentry: {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            operation = fcntl.LOCK_SH if self.mode == "shared" else fcntl.LOCK_EX
            fcntl.flock(self._fd, operation)
        except OSError as exc:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
            raise ControlPlaneLockError(f"failed to acquire {self.mode} lock: {self.path}") from exc
        self._token = _ACTIVE_LOCKS.set(active | {key})
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._token is not None:
            _ACTIVE_LOCKS.reset(self._token)
            self._token = None
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None


class RunMutationLock(AdvisoryFileLock):
    """The single authoritative lock for one run's control-plane state."""

    def __init__(self, run_dir: Path, *, mode: LockMode) -> None:
        super().__init__(run_dir / ".control_plane.lock", mode=mode)


def run_lock_active(run_dir: Path) -> bool:
    key = str((run_dir / ".control_plane.lock").resolve(strict=False))
    return key in _ACTIVE_LOCKS.get()
