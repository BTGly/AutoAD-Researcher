"""PR-004B local ResourceLease allocation tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from autoad_researcher.experiment.gpu import GpuAllocator, GpuDevice, GpuUnavailableError


def _devices() -> list[GpuDevice]:
    return [
        GpuDevice(device_id="0", total_vram_mb=40_000, used_vram_mb=2_000),
        GpuDevice(device_id="1", total_vram_mb=24_000, used_vram_mb=1_000),
    ]


def test_allocator_selects_available_devices_and_exposes_cuda_visible_devices(tmp_path: Path):
    allocator = GpuAllocator(probe=_devices, lease_ttl_seconds=60, resource_root=tmp_path)
    lease = allocator.allocate(
        tmp_path,
        attempt_id="attempt_000001",
        worker_id="worker_one",
        required_device_count=2,
        required_vram_mb=20_000,
    )

    assert lease.device_ids == ["0", "1"]
    assert lease.cuda_visible_devices == "0,1"


def test_active_lease_prevents_gpu_oversell_and_is_idempotent_per_attempt(tmp_path: Path):
    allocator = GpuAllocator(probe=lambda: _devices()[:1], resource_root=tmp_path)
    first = allocator.allocate(
        tmp_path,
        attempt_id="attempt_000001",
        worker_id="worker_one",
        required_device_count=1,
        required_vram_mb=10_000,
    )
    replay = allocator.allocate(
        tmp_path,
        attempt_id="attempt_000001",
        worker_id="worker_one",
        required_device_count=1,
        required_vram_mb=10_000,
    )
    assert replay.lease_id == first.lease_id

    with pytest.raises(GpuUnavailableError, match="TEMPORARY_GPU_UNAVAILABLE"):
        allocator.allocate(
            tmp_path,
            attempt_id="attempt_000002",
            worker_id="worker_two",
            required_device_count=1,
            required_vram_mb=10_000,
        )


def test_independent_runs_share_host_scope_and_do_not_reuse_same_device(tmp_path: Path):
    allocator = GpuAllocator(probe=_devices, resource_root=tmp_path)
    run_a = tmp_path / "run_a"
    run_b = tmp_path / "run_b"

    first = allocator.allocate(
        run_a,
        attempt_id="attempt_000001",
        worker_id="worker_a",
        required_device_count=1,
        required_vram_mb=20_000,
    )
    second = allocator.allocate(
        run_b,
        attempt_id="attempt_000001",
        worker_id="worker_b",
        required_device_count=1,
        required_vram_mb=20_000,
    )

    assert first.device_ids == ["0"]
    assert second.device_ids == ["1"]
    assert first.run_id == "run_a"
    assert second.run_id == "run_b"
    assert (tmp_path / "experiments/resource_leases.json").is_file()
    assert not (run_a / "experiments/resource_leases.json").exists()
    assert not (run_b / "experiments/resource_leases.json").exists()

    with pytest.raises(ValueError, match="different run"):
        allocator.heartbeat(run_b, lease_id=first.lease_id, worker_id="worker_a")


def test_independent_runs_fail_when_all_host_devices_are_leased(tmp_path: Path):
    allocator = GpuAllocator(probe=lambda: _devices()[:1], resource_root=tmp_path)
    allocator.allocate(
        tmp_path / "run_a",
        attempt_id="attempt_000001",
        worker_id="worker_a",
        required_device_count=1,
        required_vram_mb=20_000,
    )

    with pytest.raises(GpuUnavailableError, match="TEMPORARY_GPU_UNAVAILABLE"):
        allocator.allocate(
            tmp_path / "run_b",
            attempt_id="attempt_000001",
            worker_id="worker_b",
            required_device_count=1,
            required_vram_mb=20_000,
        )


def test_lease_heartbeat_release_and_expiry_recovery(tmp_path: Path):
    allocator = GpuAllocator(probe=lambda: _devices()[:1], lease_ttl_seconds=10, resource_root=tmp_path)
    start = datetime(2026, 7, 17, tzinfo=timezone.utc)
    lease = allocator.allocate(
        tmp_path,
        attempt_id="attempt_000001",
        worker_id="worker_one",
        required_device_count=1,
        required_vram_mb=10_000,
        now=start,
    )
    refreshed = allocator.heartbeat(
        tmp_path,
        lease_id=lease.lease_id,
        worker_id="worker_one",
        now=start + timedelta(seconds=5),
    )
    assert refreshed.expires_at == (start + timedelta(seconds=15)).isoformat()
    assert allocator.reclaim_expired(tmp_path, now=start + timedelta(seconds=14)) == []
    expired = allocator.reclaim_expired(tmp_path, now=start + timedelta(seconds=16))
    assert [item.lease_id for item in expired] == [lease.lease_id]

    replacement = allocator.allocate(
        tmp_path,
        attempt_id="attempt_000002",
        worker_id="worker_two",
        required_device_count=1,
        required_vram_mb=10_000,
        now=start + timedelta(seconds=16),
    )
    released = allocator.release(tmp_path, lease_id=replacement.lease_id, worker_id="worker_two")
    assert released.status == "released"


def test_finalizer_can_release_active_lease_after_worker_restart(tmp_path: Path):
    allocator = GpuAllocator(probe=lambda: _devices()[:1], resource_root=tmp_path)
    lease = allocator.allocate(
        tmp_path,
        attempt_id="attempt_000001",
        worker_id="worker_original",
        required_device_count=1,
        required_vram_mb=10_000,
    )

    released = allocator.release_after_attempt_terminal(
        tmp_path,
        lease_id=lease.lease_id,
        attempt_id="attempt_000001",
    )

    assert released.status == "released"
    with pytest.raises(ValueError, match="different Attempt"):
        allocator.release_after_attempt_terminal(
            tmp_path,
            lease_id=lease.lease_id,
            attempt_id="attempt_000002",
        )
