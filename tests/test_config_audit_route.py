import json

import pytest

from autoad_researcher.server.routes import config_audit


@pytest.mark.asyncio
async def test_config_audit_persists_metadata_without_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(config_audit, "RUNS_ROOT", str(tmp_path / "runs"))

    await config_audit.record_config_audit(config_audit.ConfigAuditRequest(
        dialogue_model="dialogue",
        report_model="report",
        experiment_model="experiment",
        provider_origin="https://provider.example/v1",
        has_api_key=True,
    ))

    payload = json.loads((tmp_path / "runs" / "config_audit.jsonl").read_text(encoding="utf-8"))
    assert payload["provider_origin"] == "https://provider.example"
    assert payload["provider_host"] == "provider.example"
    assert payload["has_api_key"] is True
    assert "api_key" not in payload
    assert "fingerprint" not in payload
