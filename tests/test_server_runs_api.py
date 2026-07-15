import json
from pathlib import Path

import pytest
from fastapi import BackgroundTasks

from autoad_researcher.server.routes import runs as runs_route
from autoad_researcher.server.routes.chat import TRANSCRIPT_RELATIVE_PATH
from autoad_researcher.core.run_lifecycle import lifecycle_root, load_run_lifecycle


@pytest.mark.asyncio
async def test_create_list_rename_and_transcript(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(runs_route, "RUNS_ROOT", str(tmp_path))

    created = await runs_route.create_run(runs_route.CreateRunRequest(task_title="PatchCore Task"))
    assert created.run_id
    assert created.task_title == "PatchCore Task"
    assert created.sources_count == 0
    assert created.archived_at is None
    assert load_run_lifecycle(tmp_path, created.run_id).status == "active"
    staging_root = tmp_path / ".control" / "staging"
    assert not staging_root.exists() or list(staging_root.iterdir()) == []

    run_dir = tmp_path / created.run_id
    artifact_marker = run_dir / "context" / "directory-must-not-change.txt"
    artifact_marker.write_text("keep", encoding="utf-8")
    original_directory = run_dir.resolve()
    transcript_path = run_dir / TRANSCRIPT_RELATIVE_PATH
    transcript_path.parent.mkdir(parents=True, exist_ok=True)
    transcript_path.write_text(
        json.dumps({"role": "user", "content": "hello", "created_at": "2026-07-08T00:00:00+00:00"}) + "\n"
        + json.dumps({"role": "assistant", "content": "world", "created_at": "2026-07-08T00:00:01+00:00"}) + "\n",
        encoding="utf-8",
    )

    listed = await runs_route.list_runs(include_archived=False)
    assert [item.run_id for item in listed] == [created.run_id]

    renamed = await runs_route.rename_run(created.run_id, runs_route.RenameRunRequest(task_title="Renamed Task"))
    assert renamed.task_title == "Renamed Task"
    assert renamed.run_id == created.run_id
    assert (tmp_path / renamed.run_id).resolve() == original_directory
    assert artifact_marker.read_text(encoding="utf-8") == "keep"

    refreshed = await runs_route.get_run(created.run_id)
    assert refreshed.task_title == "Renamed Task"
    assert refreshed.run_id == created.run_id

    transcript = await runs_route.get_run_transcript(created.run_id)
    assert [(item.role, item.content) for item in transcript] == [("user", "hello"), ("assistant", "world")]


@pytest.mark.asyncio
async def test_archive_restore_and_delete_session(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(runs_route, "RUNS_ROOT", str(tmp_path))

    created = await runs_route.create_run(runs_route.CreateRunRequest(task_title="Delete Me"))

    archived = await runs_route.archive_run(created.run_id)
    assert archived.archived_at is not None
    assert await runs_route.list_runs(include_archived=False) == []
    assert [item.run_id for item in await runs_route.list_runs(include_archived=True)] == [created.run_id]

    restored = await runs_route.restore_run(created.run_id)
    assert restored.archived_at is None

    background = BackgroundTasks()
    deleted = await runs_route.delete_run(created.run_id, background)
    assert deleted.status_code == 202
    await background()
    assert not (tmp_path / created.run_id).exists()


@pytest.mark.asyncio
async def test_failed_run_creation_leaves_no_publishable_directory(
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setattr(runs_route, "RUNS_ROOT", str(tmp_path))

    def fail_task_profile(**kwargs):
        raise OSError("simulated task profile write failure")

    monkeypatch.setattr(runs_route, "create_task_profile", fail_task_profile)

    with pytest.raises(OSError, match="simulated task profile write failure"):
        await runs_route.create_run(runs_route.CreateRunRequest(task_title="Will Fail"))

    records = sorted(lifecycle_root(tmp_path).glob("*.json"))
    assert len(records) == 1
    record = load_run_lifecycle(tmp_path, records[0].stem)
    assert record is not None and record.status == "deleted"
    assert not (tmp_path / record.run_id).exists()
    staging_root = tmp_path / ".control" / "staging"
    assert not staging_root.exists() or list(staging_root.iterdir()) == []
    assert await runs_route.list_runs(include_archived=True) == []
