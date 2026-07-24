from __future__ import annotations

import csv
from pathlib import Path


def test_runtime_only_patchcore_evidence_separates_runtime_and_readiness_status():
    root = Path(__file__).resolve().parents[1]
    evidence_path = root / "notes/uat/AutoAD_ML_DL_跨任务UAT扩展包_2026-07-23/GPU运行时观察.csv"
    with evidence_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    row = next(item for item in rows if item["step_id"] == "runtime_only_worker_gpu_smoke")

    assert row["result"] == "PASS"
    assert row["runtime_status"] == "COMPLETED"
    assert row["formal_readiness_status"] == "blocked"
    assert "runtime_only is an evidence-scope label" in row["notes"]
    assert "not a GPU runtime failure" in row["notes"]
