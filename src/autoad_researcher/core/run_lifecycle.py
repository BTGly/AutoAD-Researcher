"""Durable lifecycle and cross-process operation leases for Run directories."""

from __future__ import annotations

import shutil
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Literal

from pydantic import BaseModel, ConfigDict

from autoad_researcher.core.control_plane.lock import AdvisoryFileLock
from autoad_researcher.core.run_id import run_dir_path, validate_run_id


class RunLifecycleError(RuntimeError):
    """Base lifecycle failure."""


class RunLifecycleGone(RunLifecycleError):
    """The requested Run is deleting or has already been deleted."""

    def __init__(self, run_id: str, status: str) -> None:
        self.run_id = run_id
        self.status = status
        super().__init__(f"run {run_id} is {status}")


class RunLifecycleRecord(BaseModel):
    """Persistent lifecycle state stored outside the deletable Run directory."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str
    generation: str
    status: Literal["creating", "active", "deleting", "deleted"]
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


def lifecycle_root(runs_root: str | Path) -> Path:
    return Path(runs_root) / ".control" / "lifecycle"


def lifecycle_path(runs_root: str | Path, run_id: str) -> Path:
    validate_run_id(runs_root, run_id)
    return lifecycle_root(runs_root) / f"{run_id}.json"


def lifecycle_exists(runs_root: str | Path, run_id: str) -> bool:
    return lifecycle_path(runs_root, run_id).is_file()


def load_run_lifecycle(runs_root: str | Path, run_id: str) -> RunLifecycleRecord | None:
    path = lifecycle_path(runs_root, run_id)
    if not path.is_file():
        return None
    return RunLifecycleRecord.model_validate_json(path.read_text(encoding="utf-8"))


def create_run_lifecycle(
    runs_root: str | Path,
    run_id: str,
    *,
    created_at: datetime | None = None,
) -> RunLifecycleRecord:
    """Create a non-reusable active lifecycle record for a newly created Run."""

    run_dir = run_dir_path(runs_root, run_id)
    if not run_dir.is_dir():
        raise FileNotFoundError(f"run directory does not exist: {run_id}")
    with _state_lock(runs_root, run_id):
        if lifecycle_path(runs_root, run_id).exists():
            raise FileExistsError(f"run lifecycle already exists: {run_id}")
        now = created_at or _utcnow()
        record = RunLifecycleRecord(
            run_id=run_id,
            generation=uuid.uuid4().hex,
            status="active",
            created_at=now,
            updated_at=now,
        )
        _write_record(runs_root, record)
        return record


def begin_run_creation(
    runs_root: str | Path,
    run_id: str,
    *,
    created_at: datetime | None = None,
) -> RunLifecycleRecord:
    """Reserve a Run identity before any user-visible directory is created."""

    validate_run_id(runs_root, run_id)
    with _state_lock(runs_root, run_id):
        if lifecycle_path(runs_root, run_id).exists():
            raise FileExistsError(f"run lifecycle already exists: {run_id}")
        if run_dir_path(runs_root, run_id).exists():
            raise FileExistsError(f"run directory already exists: {run_id}")
        now = created_at or _utcnow()
        record = RunLifecycleRecord(
            run_id=run_id,
            generation=uuid.uuid4().hex,
            status="creating",
            created_at=now,
            updated_at=now,
        )
        _write_record(runs_root, record)
        return record


def staging_run_dir(
    runs_root: str | Path,
    run_id: str,
    generation: str,
) -> Path:
    """Return the hidden same-filesystem staging directory for one creation."""

    validate_run_id(runs_root, run_id)
    try:
        normalized_generation = uuid.UUID(hex=generation).hex
    except (ValueError, AttributeError) as exc:
        raise ValueError("generation must be 32 lowercase hexadecimal characters") from exc
    if normalized_generation != generation:
        raise ValueError("generation must be 32 lowercase hexadecimal characters")
    return Path(runs_root) / ".control" / "staging" / f"{run_id}.{generation}"


def publish_run_creation(
    runs_root: str | Path,
    run_id: str,
    *,
    generation: str,
) -> RunLifecycleRecord:
    """Atomically publish a prepared staging directory and activate its lifecycle."""

    validate_run_id(runs_root, run_id)
    with AdvisoryFileLock(_operation_lock_path(runs_root, run_id), mode="exclusive"):
        with _state_lock(runs_root, run_id):
            record = _load_record_unlocked(runs_root, run_id)
            if (
                record is None
                or record.generation != generation
                or record.status != "creating"
            ):
                raise RunLifecycleError(f"run {run_id} is not in the expected creating state")
            staging = staging_run_dir(runs_root, run_id, generation)
            run_dir = run_dir_path(runs_root, run_id)
            if not staging.is_dir():
                raise FileNotFoundError(f"run staging directory does not exist: {run_id}")
            if not (staging / "ui_chat" / "task_profile.json").is_file():
                raise RunLifecycleError(f"run staging directory is incomplete: {run_id}")
            if run_dir.exists():
                raise FileExistsError(f"run directory already exists: {run_id}")
            staging.rename(run_dir)
            record = record.model_copy(update={"status": "active", "updated_at": _utcnow()})
            _write_record(runs_root, record)
            return record


def abort_run_creation(
    runs_root: str | Path,
    run_id: str,
    *,
    generation: str,
) -> RunLifecycleRecord:
    """Remove unpublished creation artifacts and persist a non-reusable tombstone."""

    validate_run_id(runs_root, run_id)
    with AdvisoryFileLock(_operation_lock_path(runs_root, run_id), mode="exclusive"):
        with _state_lock(runs_root, run_id):
            record = _load_record_unlocked(runs_root, run_id)
            if record is None or record.generation != generation:
                raise RunLifecycleError(f"run {run_id} creation identity does not match")
            if record.status != "creating":
                return record
            staging = staging_run_dir(runs_root, run_id, generation)
            if staging.exists():
                shutil.rmtree(staging)
            run_dir = run_dir_path(runs_root, run_id)
            if run_dir.exists():
                shutil.rmtree(run_dir)
            now = _utcnow()
            record = record.model_copy(update={
                "status": "deleted",
                "updated_at": now,
                "deleted_at": now,
            })
            _write_record(runs_root, record)
            return record


def ensure_run_lifecycle(runs_root: str | Path, run_id: str) -> RunLifecycleRecord:
    """Lazily import a pre-lifecycle Run without reviving a tombstone."""

    validate_run_id(runs_root, run_id)
    with _state_lock(runs_root, run_id):
        existing = _load_record_unlocked(runs_root, run_id)
        if existing is not None:
            return existing
        run_dir = run_dir_path(runs_root, run_id)
        if not run_dir.is_dir():
            raise FileNotFoundError(f"run not found: {run_id}")
        timestamp = datetime.fromtimestamp(run_dir.stat().st_ctime, tz=timezone.utc)
        record = RunLifecycleRecord(
            run_id=run_id,
            generation=uuid.uuid4().hex,
            status="active",
            created_at=timestamp,
            updated_at=_utcnow(),
        )
        _write_record(runs_root, record)
        return record


@contextmanager
def run_operation_lease(
    runs_root: str | Path,
    run_id: str,
) -> Iterator[RunLifecycleRecord]:
    """Hold a shared lease for the complete lifetime of one Run operation."""

    validate_run_id(runs_root, run_id)
    with AdvisoryFileLock(_operation_lock_path(runs_root, run_id), mode="shared"):
        record = ensure_run_lifecycle(runs_root, run_id)
        if record.status != "active":
            raise RunLifecycleGone(run_id, record.status)
        if not run_dir_path(runs_root, run_id).is_dir():
            raise FileNotFoundError(f"run not found: {run_id}")
        yield record


def begin_run_deletion(runs_root: str | Path, run_id: str) -> RunLifecycleRecord:
    """Logically delete a Run so new operations are rejected immediately."""

    validate_run_id(runs_root, run_id)
    with _state_lock(runs_root, run_id):
        record = _load_record_unlocked(runs_root, run_id)
        if record is None:
            run_dir = run_dir_path(runs_root, run_id)
            if not run_dir.is_dir():
                raise FileNotFoundError(f"run not found: {run_id}")
            timestamp = datetime.fromtimestamp(run_dir.stat().st_ctime, tz=timezone.utc)
            record = RunLifecycleRecord(
                run_id=run_id,
                generation=uuid.uuid4().hex,
                status="active",
                created_at=timestamp,
                updated_at=_utcnow(),
            )
        if record.status == "active":
            record = record.model_copy(update={"status": "deleting", "updated_at": _utcnow()})
            _write_record(runs_root, record)
        return record


def finalize_run_deletion(runs_root: str | Path, run_id: str) -> RunLifecycleRecord:
    """Wait for shared leases, remove the directory, and persist the tombstone."""

    validate_run_id(runs_root, run_id)
    with AdvisoryFileLock(_operation_lock_path(runs_root, run_id), mode="exclusive"):
        with _state_lock(runs_root, run_id):
            record = _load_record_unlocked(runs_root, run_id)
            if record is None:
                raise FileNotFoundError(f"run lifecycle not found: {run_id}")
            if record.status == "deleted":
                return record
            if record.status != "deleting":
                raise RunLifecycleError(f"run {run_id} must be deleting before finalization")
            run_dir = run_dir_path(runs_root, run_id)
            if run_dir.exists():
                shutil.rmtree(run_dir)
            now = _utcnow()
            record = record.model_copy(update={
                "status": "deleted",
                "updated_at": now,
                "deleted_at": now,
            })
            _write_record(runs_root, record)
            return record


def recover_incomplete_run_deletions(runs_root: str | Path) -> list[str]:
    """Finish durable deleting records after a server restart."""

    root = lifecycle_root(runs_root)
    if not root.is_dir():
        return []
    recovered: list[str] = []
    for path in sorted(root.glob("*.json")):
        try:
            record = RunLifecycleRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if record.status != "deleting":
            continue
        finalize_run_deletion(runs_root, record.run_id)
        recovered.append(record.run_id)
    return recovered


def recover_incomplete_run_creations(runs_root: str | Path) -> list[str]:
    """Finish or abort durable creating records after a server restart."""

    root = lifecycle_root(runs_root)
    if not root.is_dir():
        return []
    recovered: list[str] = []
    for path in sorted(root.glob("*.json")):
        try:
            candidate = RunLifecycleRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if candidate.status != "creating":
            continue
        with AdvisoryFileLock(_operation_lock_path(runs_root, candidate.run_id), mode="exclusive"):
            with _state_lock(runs_root, candidate.run_id):
                record = _load_record_unlocked(runs_root, candidate.run_id)
                if record is None or record.status != "creating":
                    continue
                staging = staging_run_dir(runs_root, record.run_id, record.generation)
                run_dir = run_dir_path(runs_root, record.run_id)
                profile_path = run_dir / "ui_chat" / "task_profile.json"
                if run_dir.is_dir() and profile_path.is_file():
                    if staging.exists():
                        shutil.rmtree(staging)
                    record = record.model_copy(update={
                        "status": "active",
                        "updated_at": _utcnow(),
                    })
                else:
                    if staging.exists():
                        shutil.rmtree(staging)
                    if run_dir.exists():
                        shutil.rmtree(run_dir)
                    now = _utcnow()
                    record = record.model_copy(update={
                        "status": "deleted",
                        "updated_at": now,
                        "deleted_at": now,
                    })
                _write_record(runs_root, record)
                recovered.append(record.run_id)
    return recovered


def is_run_visible(runs_root: str | Path, run_id: str) -> bool:
    """Return whether an existing Run is active and may appear in the task list."""

    try:
        record = ensure_run_lifecycle(runs_root, run_id)
    except (FileNotFoundError, ValueError):
        return False
    return record.status == "active"


def _state_lock(runs_root: str | Path, run_id: str) -> AdvisoryFileLock:
    return AdvisoryFileLock(lifecycle_root(runs_root) / f"{run_id}.lock", mode="exclusive")


def _operation_lock_path(runs_root: str | Path, run_id: str) -> Path:
    return Path(runs_root) / ".control" / "operations" / f"{run_id}.lock"


def _load_record_unlocked(runs_root: str | Path, run_id: str) -> RunLifecycleRecord | None:
    path = lifecycle_path(runs_root, run_id)
    if not path.is_file():
        return None
    return RunLifecycleRecord.model_validate_json(path.read_text(encoding="utf-8"))


def _write_record(runs_root: str | Path, record: RunLifecycleRecord) -> None:
    path = lifecycle_path(runs_root, record.run_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f".{uuid.uuid4().hex}.tmp")
    temp.write_text(record.model_dump_json(indent=2), encoding="utf-8")
    temp.replace(path)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)
