"""Runtime import boundary for the active V2 Research Chat dependency chain."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).parents[1]
FORBIDDEN_V1_MODULES = [
    "autoad_researcher.ui.research_chat",
    "autoad_researcher.assistant.response_guard",
]
V2_PRODUCTION_MODULES = [
    "autoad_researcher.server.routes.chat",
    "autoad_researcher.assistant.v2.orchestrator",
    "autoad_researcher.assistant.v2.dialogue_gate",
    "autoad_researcher.assistant.v2.research_dialogue_agent",
]


def _assert_no_import(target: str, forbidden: list[str]) -> None:
    probe = """
import importlib
import json
import sys

target = sys.argv[1]
forbidden = json.loads(sys.argv[2])
importlib.import_module(target)
print(json.dumps(sorted(name for name in forbidden if name in sys.modules)))
"""
    completed = subprocess.run(
        [sys.executable, "-c", probe, target, json.dumps(forbidden)],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    loaded = json.loads(completed.stdout)
    assert loaded == [], f"{target} loaded forbidden V1 modules: {loaded}"


def test_v2_chat_route_does_not_import_legacy_modules():
    """V2 production modules must not load keyword-based legacy modules."""

    for module_name in V2_PRODUCTION_MODULES:
        _assert_no_import(module_name, FORBIDDEN_V1_MODULES)
