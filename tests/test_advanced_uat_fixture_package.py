from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "docs/uat/AutoAD_高级前后端联合UAT包_2026-07-24"
GENERATED = PACKAGE / "generated/01_spike_ad_two_stage"


def test_advanced_uat_fixture_generator_and_oracle():
    try:
        subprocess.run(
            [sys.executable, str(PACKAGE / "materialize_fixture.py")],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        completed = subprocess.run(
            [sys.executable, str(GENERATED / "scripts/verify_fixture.py")],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout)

        for phase in ("b_dev", "b_test"):
            assert payload["baseline"][phase]["image AUROC"] == 0.0
            assert payload["baseline"][phase]["F1"] == 0.0
            assert payload["candidate"][phase]["image AUROC"] == 1.0
            assert payload["candidate"][phase]["F1"] == 1.0

        manifest = json.loads(
            (GENERATED / "autoad_executor_adapter.json").read_text(encoding="utf-8")
        )
        assert manifest["allowed_paths"] == ["model.py"]
        assert set(manifest["protected_paths"]) == {
            "metric.py",
            "evaluate.py",
            "train.py",
            "run_experiment.py",
        }
        assert set(manifest["evaluation_commands"]) == {"b_dev", "b_test"}
    finally:
        shutil.rmtree(PACKAGE / "generated", ignore_errors=True)
