from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

import httpx
import pytest

from autoad_researcher.assistant.v2.evidence_service import load_usable_evidence
from autoad_researcher.assistant.v2.job_service import load_pipeline_jobs
from autoad_researcher.server.main import app
from autoad_researcher.server.routes import chat as chat_route
from autoad_researcher.server.routes import sources as sources_route
from autoad_researcher.ui.sources import load_source_registry


@pytest.mark.asyncio
async def test_frontend_raw_file_upload_contract_preserves_encoded_filename(
    tmp_path: Path,
    monkeypatch,
):
    runs_root = tmp_path / "runs"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sources_route, "RUNS_ROOT", str(runs_root))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/runs/run_file_ingress/sources/upload",
            content="# Experiment notes\nPatchCore on MVTec AD".encode(),
            headers={"X-AutoAD-Filename": quote("实验 notes.md", safe="")},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["source"]["kind"] == "markdown"
    assert payload["jobs"] == []
    run_dir = runs_root / "run_file_ingress"
    registry = load_source_registry(run_dir)
    assert registry["sources"][0]["user_label"] == "实验 notes.md"
    assert (run_dir / payload["source"]["stored_path"]).read_text(encoding="utf-8").startswith(
        "# Experiment notes"
    )
    evidence = load_usable_evidence(run_dir)
    assert evidence[0]["evidence_type"] == "uploaded_text"
    assert "PatchCore on MVTec AD" in evidence[0]["summary"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "expected_kind", "expected_jobs"),
    [
        (
            "https://example.com/research/patchcore",
            "webpage",
            ["web_fetch", "web_markitdown"],
        ),
        (
            "https://github.com/amazon-science/patchcore-inspection.git",
            "github_repo",
            ["git_clone", "repo_summarize"],
        ),
    ],
)
async def test_chat_link_ingress_registers_source_and_jobs_without_live_llm(
    tmp_path: Path,
    monkeypatch,
    url: str,
    expected_kind: str,
    expected_jobs: list[str],
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(chat_route, "CONFIG_PATH", tmp_path / "missing-config.json")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    transport = httpx.ASGITransport(app=app)
    run_id = f"run_link_{expected_kind}"

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/chat/send",
            json={"run_id": run_id, "user_input": url},
        )

    assert response.status_code == 200
    assert response.json()["reply_kind"] == "source_intake"
    run_dir = tmp_path / "runs" / run_id
    registry = load_source_registry(run_dir)
    assert len(registry["sources"]) == 1
    assert registry["sources"][0]["kind"] == expected_kind
    assert [job["job_type"] for job in load_pipeline_jobs(run_dir)] == expected_jobs
