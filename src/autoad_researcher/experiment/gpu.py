"""Local, deterministic GPU allocation for durable experiment Attempts."""

from __future__ import annotations

import json
import os
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field

LEASES_FILE = "experiments/resource_leases.json"


class GpuDevice(BaseModel):
    """Observed allocatable capacity of one physical GPU."""

    model_config = ConfigDict(extra="forbid")

    device_id: str = Field(pattern=r"^[0-9]+$")
    total_vram_mb: int = Field(ge=0)
    used_vram_mb: int = Field(ge=0)

    @property
    def free_vram_mb(self) -> int:
        return max(0, self.total_vram_mb - self.used_vram_mb)


class ResourceLease(BaseModel):
    """One exclusive AutoAD claim on a set of local GPU devices."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    lease_id: str = Field(pattern=r"^lease_[0-9]{6}$")
    attempt_id: str = Field(pattern=r"^attempt_[0-9]{6}$")
    worker_id: str = Field(min_length=1)
    device_ids: list[str] = Field(min_length=1)
    required_device_count: int = Field(gt=0)
    required_vram_mb: int = Field(ge=0)
    allocated_at: str
    expires_at: str
    heartbeat_at: str
    status: Literal["active", "released", "expired"] = "active"

    @property
    def cuda_visible_devices(self) -> str:
        return ",".join(self.device_ids)


class GpuUnavailableError(RuntimeError):
    """A transient capacity failure, safe to expose as a Job failure code."""


GpuProbe = Callable[[], list[GpuDevice]]


class GpuAllocator:
    """Allocate GPUs under one lock; never infer ownership from process lists."""

    def __init__(self, *, probe: GpuProbe | None = None, lease_ttl_seconds: int = 60):
        if lease_ttl_seconds <= 0:
            raise ValueError("lease_ttl_seconds must be positive")
        self._probe = probe or probe_local_gpus
        self._lease_ttl_seconds = lease_ttl_seconds

    def allocate(
        self,
        run_dir: Path,
        *,
        attempt_id: str,
        worker_id: str,
        required_device_count: int,
        required_vram_mb: int,
        now: datetime | None = None,
    ) -> ResourceLease:
        if required_device_count <= 0:
            raise ValueError("required_device_count must be positive")
        if required_vram_mb < 0:
            raise ValueError("required_vram_mb must not be negative")
        current = _as_utc(now or datetime.now(timezone.utc))
        with _leases_lock(run_dir):
            leases = _load_leases_unlocked(run_dir)
            leases, changed = _expire_leases(leases, current)
            existing = next(
                (lease for lease in leases if lease.attempt_id == attempt_id and lease.status == "active"),
                None,
            )
            if existing is not None:
                if (
                    existing.worker_id != worker_id
                    or existing.required_device_count != required_device_count
                    or existing.required_vram_mb != required_vram_mb
                ):
                    raise ValueError("Attempt already has a conflicting active ResourceLease")
                if changed:
                    _write_leases_unlocked(run_dir, leases)
                return existing
            observed = self._probe()
            occupied = {device_id for lease in leases if lease.status == "active" for device_id in lease.device_ids}
            eligible = [
                device for device in observed
                if device.device_id not in occupied and device.free_vram_mb >= required_vram_mb
            ]
            if len(eligible) < required_device_count:
                if changed:
                    _write_leases_unlocked(run_dir, leases)
                raise GpuUnavailableError("TEMPORARY_GPU_UNAVAILABLE: no unleased GPU satisfies the request")
            device_ids = [device.device_id for device in eligible[:required_device_count]]
            lease = ResourceLease(
                lease_id=_next_lease_id(leases),
                attempt_id=attempt_id,
                worker_id=worker_id,
                device_ids=device_ids,
                required_device_count=required_device_count,
                required_vram_mb=required_vram_mb,
                allocated_at=current.isoformat(),
                heartbeat_at=current.isoformat(),
                expires_at=(current + timedelta(seconds=self._lease_ttl_seconds)).isoformat(),
            )
            leases.append(lease)
            _write_leases_unlocked(run_dir, leases)
            return lease

    def heartbeat(self, run_dir: Path, *, lease_id: str, worker_id: str, now: datetime | None = None) -> ResourceLease:
        current = _as_utc(now or datetime.now(timezone.utc))
        with _leases_lock(run_dir):
            leases = _load_leases_unlocked(run_dir)
            updated: ResourceLease | None = None
            for index, lease in enumerate(leases):
                if lease.lease_id != lease_id:
                    continue
                if lease.status != "active":
                    raise ValueError("only active ResourceLease may heartbeat")
                if lease.worker_id != worker_id:
                    raise ValueError("ResourceLease belongs to a different worker")
                updated = lease.model_copy(
                    update={
                        "heartbeat_at": current.isoformat(),
                        "expires_at": (current + timedelta(seconds=self._lease_ttl_seconds)).isoformat(),
                    }
                )
                leases[index] = updated
                break
            if updated is None:
                raise FileNotFoundError("ResourceLease not found")
            _write_leases_unlocked(run_dir, leases)
            return updated

    def release(self, run_dir: Path, *, lease_id: str, worker_id: str) -> ResourceLease:
        with _leases_lock(run_dir):
            leases = _load_leases_unlocked(run_dir)
            updated: ResourceLease | None = None
            for index, lease in enumerate(leases):
                if lease.lease_id != lease_id:
                    continue
                if lease.worker_id != worker_id:
                    raise ValueError("ResourceLease belongs to a different worker")
                if lease.status == "released":
                    return lease
                if lease.status != "active":
                    raise ValueError("expired ResourceLease cannot be released")
                updated = lease.model_copy(update={"status": "released"})
                leases[index] = updated
                break
            if updated is None:
                raise FileNotFoundError("ResourceLease not found")
            _write_leases_unlocked(run_dir, leases)
            return updated

    def release_after_attempt_terminal(
        self,
        run_dir: Path,
        *,
        lease_id: str,
        attempt_id: str,
    ) -> ResourceLease:
        """Release an active lease during durable terminal finalization.

        A restarted Worker cannot prove the original worker identity, but it
        can prove the persisted terminal Attempt and its bound lease.  This
        narrow recovery path intentionally checks the Attempt binding instead
        of granting an arbitrary worker a lease-release capability.
        """
        with _leases_lock(run_dir):
            leases = _load_leases_unlocked(run_dir)
            for index, lease in enumerate(leases):
                if lease.lease_id != lease_id:
                    continue
                if lease.attempt_id != attempt_id:
                    raise ValueError("ResourceLease belongs to a different Attempt")
                if lease.status in {"released", "expired"}:
                    return lease
                updated = lease.model_copy(update={"status": "released"})
                leases[index] = updated
                _write_leases_unlocked(run_dir, leases)
                return updated
            raise FileNotFoundError("ResourceLease not found")

    def reclaim_expired(self, run_dir: Path, *, now: datetime | None = None) -> list[ResourceLease]:
        current = _as_utc(now or datetime.now(timezone.utc))
        with _leases_lock(run_dir):
            leases = _load_leases_unlocked(run_dir)
            updated, changed = _expire_leases(leases, current)
            if changed:
                _write_leases_unlocked(run_dir, updated)
            before = {lease.lease_id: lease.status for lease in leases}
            return [
                lease for lease in updated
                if lease.status == "expired" and before.get(lease.lease_id) != "expired"
            ]


def probe_local_gpus() -> list[GpuDevice]:
    """Read actual GPU memory without a shell or an LLM."""
    try:
        completed = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,memory.total,memory.used",
                "--format=csv,noheader,nounits",
            ],
            shell=False,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []
    devices: list[GpuDevice] = []
    for line in completed.stdout.splitlines():
        values = [value.strip() for value in line.split(",")]
        if len(values) != 3:
            continue
        try:
            devices.append(
                GpuDevice(
                    device_id=values[0], total_vram_mb=int(values[1]), used_vram_mb=int(values[2])
                )
            )
        except ValueError:
            continue
    return devices


def _load_leases_unlocked(run_dir: Path) -> list[ResourceLease]:
    path = run_dir / LEASES_FILE
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("ResourceLease store must be a JSON array")
    return [ResourceLease.model_validate(item) for item in raw]


def _write_leases_unlocked(run_dir: Path, leases: list[ResourceLease]) -> None:
    path = run_dir / LEASES_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump([lease.model_dump(mode="json") for lease in leases], handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


@contextmanager
def _leases_lock(run_dir: Path, timeout: float = 5.0):
    lock_path = run_dir / "experiments" / ".resource_leases.lock"
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
        raise TimeoutError(f"Could not acquire ResourceLease lock for {run_dir} within {timeout}s")
    try:
        yield
    finally:
        os.close(fd)
        try:
            os.unlink(lock_path)
        except OSError:
            pass


def _next_lease_id(leases: list[ResourceLease]) -> str:
    max_number = 0
    for lease in leases:
        max_number = max(max_number, int(lease.lease_id.removeprefix("lease_")))
    return f"lease_{max_number + 1:06d}"


def _expire_leases(leases: list[ResourceLease], current: datetime) -> tuple[list[ResourceLease], bool]:
    changed = False
    updated: list[ResourceLease] = []
    for lease in leases:
        expires_at = _as_utc(datetime.fromisoformat(lease.expires_at))
        if lease.status == "active" and expires_at <= current:
            updated.append(lease.model_copy(update={"status": "expired"}))
            changed = True
        else:
            updated.append(lease)
    return updated, changed


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("ResourceLease times must include a timezone")
    return value.astimezone(timezone.utc)
