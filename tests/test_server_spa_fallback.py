from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.responses import FileResponse

from autoad_researcher.server.main import _spa_fallback_response, app


@pytest.mark.asyncio
async def test_removed_draft_api_is_a_real_404():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/runs/run_missing/draft")

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


@pytest.mark.asyncio
async def test_intent_summary_api_replaces_draft_api():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/runs/run_missing/intent-summary")

    assert response.status_code == 200
    assert response.json() == {
        "goal": "",
        "confirmed_facts": [],
        "confirmed_task_parameters": {
            "baseline": None,
            "dataset": None,
            "compute_budget": None,
            "primary_metrics": [],
            "evaluation_constraints": [],
        },
        "inferred_facts": [],
        "unresolved_conflicts": [],
        "blocking_question": None,
    }


def test_non_api_route_keeps_spa_fallback(tmp_path):
    index = tmp_path / "index.html"
    index.write_text("<html>AutoAD</html>", encoding="utf-8")

    response = _spa_fallback_response("research/workspace", tmp_path)

    assert isinstance(response, FileResponse)
    assert response.status_code == 200
    assert Path(response.path) == index
