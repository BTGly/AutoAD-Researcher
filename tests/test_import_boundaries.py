"""Public package imports must work in a fresh interpreter."""

from __future__ import annotations

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "statement",
    [
        "import autoad_researcher.runner",
        "from autoad_researcher.runner import execute_experiment_attempt",
        "import autoad_researcher.environments",
        "import autoad_researcher.experiment",
        "import autoad_researcher.runner; import autoad_researcher.environments; import autoad_researcher.experiment",
        "import autoad_researcher.experiment; import autoad_researcher.environments; import autoad_researcher.runner",
    ],
)
def test_public_packages_import_in_fresh_interpreter(statement: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", statement], text=True, capture_output=True, timeout=30, check=False
    )
    assert completed.returncode == 0, completed.stderr
