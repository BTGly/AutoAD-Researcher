from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from autoad_researcher.assistant.v2.orchestrator import OrchestratorResult, ResearchOrchestratorV2
from autoad_researcher.server.models import ChatRequest
from autoad_researcher.server.routes import (
    artifacts as artifacts_route,
    chat as chat_route,
    evidence as evidence_route,
    experiment_config as experiment_config_route,
    intent_summary as intent_summary_route,
    jobs as jobs_route,
    report_route,
    sources as sources_route,
)


@pytest.mark.asyncio
async def test_v2_routes_use_the_configured_runs_root(tmp_path: Path, monkeypatch):
    run_id = "run_configured_root"
    run_dir = tmp_path / run_id
    run_dir.mkdir()
    (run_dir / "artifact.txt").write_text("artifact", encoding="utf-8")
    (run_dir / "report.md").write_text("# Report", encoding="utf-8")
    for route in (
        artifacts_route,
        chat_route,
        evidence_route,
        experiment_config_route,
        intent_summary_route,
        jobs_route,
        report_route,
        sources_route,
    ):
        monkeypatch.setattr(route, "RUNS_ROOT", str(tmp_path))

    monkeypatch.setattr(
        ResearchOrchestratorV2,
        "handle",
        staticmethod(lambda *args, **kwargs: OrchestratorResult(reply="ok")),
    )
    request = Request(
        {"type": "http", "method": "POST", "path": "/api/chat/send", "headers": []}
    )

    response = await chat_route.chat_send(ChatRequest(user_input="hello"), request)

    assert response.reply == "ok"
    assert (run_dir / "chat" / "transcript.jsonl").is_file() is False
    chat_run_dir = next(path for path in tmp_path.iterdir() if path != run_dir)
    assert (chat_run_dir / "chat" / "transcript.jsonl").is_file()
    assert await jobs_route.get_jobs(run_id) == []
    assert await sources_route.get_sources(run_id) == []
    assert await evidence_route.get_evidence(run_id) == []
    assert (await intent_summary_route.get_intent_summary(run_id))["goal"] == ""
    assert (await artifacts_route.get_artifact(run_id, "artifact.txt"))["content"] == "artifact"
    assert (await report_route.get_report(run_id))["content"] == "# Report"
    assert await experiment_config_route.get_experiment_config(run_id) == {}
    assert await experiment_config_route.save_experiment_config(run_id, {"mode": "test"}) == {
        "status": "ok",
        "run_id": run_id,
    }
    assert (run_dir / "experiment_config.json").is_file()

    with pytest.raises(HTTPException, match="run_id") as excinfo:
        await jobs_route.get_jobs("../outside")
    assert excinfo.value.status_code == 400
