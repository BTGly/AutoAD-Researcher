from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from autoad_researcher.ui.material_requests import (
    append_material_request,
    claim_material_request,
    complete_material_request,
    fail_material_request,
    load_material_requests,
)


def test_material_request_claim_uses_lock(tmp_path: Path):
    run_dir = tmp_path / "run_queue"
    run_dir.mkdir()
    request = append_material_request(
        run_dir,
        user_message="搜索 MVTec AD 最新方法",
        payload={"query": "搜索 MVTec AD 最新方法"},
        evidence_role="candidate_source_only",
    )

    claimed = claim_material_request(run_dir, request_id=request["request_id"], worker_id="worker_001")

    assert claimed is True
    loaded = load_material_requests(run_dir)[0]
    assert loaded["status"] == "running"
    assert loaded["claimed_by"] == "worker_001"
    assert loaded["attempt_count"] == 1
    assert loaded["lease_until"]


def test_material_request_not_claimed_twice(tmp_path: Path):
    run_dir = tmp_path / "run_queue"
    run_dir.mkdir()
    request = append_material_request(run_dir, user_message="搜索方法")

    first = claim_material_request(run_dir, request_id=request["request_id"], worker_id="worker_001")
    second = claim_material_request(run_dir, request_id=request["request_id"], worker_id="worker_002")

    loaded = load_material_requests(run_dir)[0]
    assert first is True
    assert second is False
    assert loaded["claimed_by"] == "worker_001"
    assert loaded["attempt_count"] == 1


def test_material_request_lease_expiry_allows_retry(tmp_path: Path):
    run_dir = tmp_path / "run_queue"
    run_dir.mkdir()
    request = append_material_request(run_dir, user_message="搜索方法")
    assert claim_material_request(run_dir, request_id=request["request_id"], worker_id="worker_001")

    rows = load_material_requests(run_dir)
    rows[0]["lease_until"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    path = run_dir / "ui_chat" / "material_requests.jsonl"
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )

    retried = claim_material_request(run_dir, request_id=request["request_id"], worker_id="worker_002")

    loaded = load_material_requests(run_dir)[0]
    assert retried is True
    assert loaded["claimed_by"] == "worker_002"
    assert loaded["attempt_count"] == 2


def test_complete_and_fail_material_request_update_terminal_states(tmp_path: Path):
    run_dir = tmp_path / "run_queue"
    run_dir.mkdir()
    first = append_material_request(run_dir, user_message="搜索方法")
    second = append_material_request(run_dir, user_message="找资料")
    claim_material_request(run_dir, request_id=first["request_id"], worker_id="worker_001")
    claim_material_request(run_dir, request_id=second["request_id"], worker_id="worker_001")

    complete_material_request(run_dir, request_id=first["request_id"], notification_id="ntf_000001")
    fail_material_request(
        run_dir,
        request_id=second["request_id"],
        error_code="provider_unavailable",
        error_message="web_search provider is not configured",
        retryable=True,
    )

    rows = load_material_requests(run_dir)
    assert rows[0]["status"] == "completed"
    assert rows[0]["result_notification_id"] == "ntf_000001"
    assert rows[0]["lease_until"] is None
    assert rows[1]["status"] == "failed"
    assert rows[1]["last_error"]["error_code"] == "provider_unavailable"
    assert rows[1]["last_error"]["retryable"] is True
