import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from autoad_researcher.server.routes import runs as runs_route
from autoad_researcher.server.routes.chat import TRANSCRIPT_RELATIVE_PATH


@pytest.mark.asyncio
async def test_create_list_rename_and_transcript(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(runs_route, "RUNS_ROOT", str(tmp_path))

    created = await runs_route.create_run(runs_route.CreateRunRequest(task_title="PatchCore Task"))
    assert created.run_id
    assert created.task_title == "PatchCore Task"
    assert created.sources_count == 0
    assert created.archived_at is None

    run_dir = tmp_path / created.run_id
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

    transcript = await runs_route.get_run_transcript(created.run_id)
    assert [(item.role, item.content) for item in transcript] == [("user", "hello"), ("assistant", "world")]


@pytest.mark.asyncio
async def test_archive_restore_and_delete_requires_archive(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(runs_route, "RUNS_ROOT", str(tmp_path))

    created = await runs_route.create_run(runs_route.CreateRunRequest(task_title="Delete Me"))

    with pytest.raises(HTTPException) as not_archived:
        await runs_route.delete_run(created.run_id)
    assert not_archived.value.status_code == 409

    archived = await runs_route.archive_run(created.run_id)
    assert archived.archived_at is not None
    assert await runs_route.list_runs(include_archived=False) == []
    assert [item.run_id for item in await runs_route.list_runs(include_archived=True)] == [created.run_id]

    restored = await runs_route.restore_run(created.run_id)
    assert restored.archived_at is None

    await runs_route.archive_run(created.run_id)
    deleted = await runs_route.delete_run(created.run_id)
    assert deleted == {"run_id": created.run_id, "deleted": True}
    assert not (tmp_path / created.run_id).exists()
