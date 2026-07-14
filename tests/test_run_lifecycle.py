from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from autoad_researcher.core.run_lifecycle import (
    RunLifecycleGone,
    begin_run_deletion,
    create_run_lifecycle,
    finalize_run_deletion,
    load_run_lifecycle,
    recover_incomplete_run_deletions,
    run_operation_lease,
)
from autoad_researcher.task_workspace.task_profile import create_task_profile, list_all_tasks


def _create_run(runs_root: Path, run_id: str = "run_lifecycle") -> Path:
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True)
    create_task_profile(
        run_dir=run_dir,
        run_id=run_id,
        task_title="Lifecycle Test",
        created_at=datetime.now(timezone.utc),
    )
    create_run_lifecycle(runs_root, run_id)
    return run_dir


def test_delete_waits_for_active_operation_and_never_recreates_directory(tmp_path: Path):
    run_id = "run_blocked"
    run_dir = _create_run(tmp_path, run_id)
    entered = threading.Event()
    release = threading.Event()

    def active_writer() -> None:
        with run_operation_lease(tmp_path, run_id):
            entered.set()
            release.wait(timeout=5)
            (run_dir / "late.txt").write_text("late", encoding="utf-8")

    writer = threading.Thread(target=active_writer)
    writer.start()
    assert entered.wait(timeout=2)

    state = begin_run_deletion(tmp_path, run_id)
    assert state.status == "deleting"
    assert list_all_tasks(runs_root=tmp_path) == []

    finalizer = threading.Thread(target=finalize_run_deletion, args=(tmp_path, run_id))
    finalizer.start()
    finalizer.join(timeout=0.1)
    assert finalizer.is_alive()

    release.set()
    writer.join(timeout=2)
    finalizer.join(timeout=2)
    assert not run_dir.exists()
    assert load_run_lifecycle(tmp_path, run_id).status == "deleted"
    with pytest.raises(RunLifecycleGone):
        with run_operation_lease(tmp_path, run_id):
            pass
    assert not run_dir.exists()


def test_recover_deleting_run_after_restart(tmp_path: Path):
    run_id = "run_recover_delete"
    run_dir = _create_run(tmp_path, run_id)
    begin_run_deletion(tmp_path, run_id)

    assert recover_incomplete_run_deletions(tmp_path) == [run_id]
    assert not run_dir.exists()
    assert load_run_lifecycle(tmp_path, run_id).status == "deleted"


def test_deleted_run_rejects_reused_lifecycle(tmp_path: Path):
    run_id = "run_no_reuse"
    _create_run(tmp_path, run_id)
    begin_run_deletion(tmp_path, run_id)
    finalize_run_deletion(tmp_path, run_id)
    (tmp_path / run_id).mkdir()

    with pytest.raises(FileExistsError):
        create_run_lifecycle(tmp_path, run_id)
