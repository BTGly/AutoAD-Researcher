"""Spike 01: DeepAgentsHarness file whitelist + schema artifact validation.

Verifies that a constrained Deep Agent can:
1. Read from runs/run_demo/**
2. Write experiment_plan.json and patch_plan.json
3. Stay within the path whitelist (no shell, no external writes)
"""

from pathlib import Path
import json

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from deepagents.middleware import FilesystemPermission

from schema import ExperimentPlan, PatchPlan


ROOT = Path(__file__).resolve().parent
RUN_DIR = ROOT / "runs" / "run_demo"


def read_task() -> str:
    return (ROOT / "task.md").read_text(encoding="utf-8")


def validate_outputs() -> None:
    experiment_plan_path = RUN_DIR / "experiment_plan.json"
    patch_plan_path = RUN_DIR / "patch_plan.json"

    if not experiment_plan_path.exists():
        raise FileNotFoundError(f"Missing: {experiment_plan_path}")

    if not patch_plan_path.exists():
        raise FileNotFoundError(f"Missing: {patch_plan_path}")

    experiment_plan_data = json.loads(experiment_plan_path.read_text(encoding="utf-8"))
    patch_plan_data = json.loads(patch_plan_path.read_text(encoding="utf-8"))

    ExperimentPlan.model_validate(experiment_plan_data)
    PatchPlan.model_validate(patch_plan_data)

    print("PASS: experiment_plan.json and patch_plan.json are valid.")


def main() -> None:
    RUN_DIR.mkdir(parents=True, exist_ok=True)

    agent = create_deep_agent(
        model="anthropic:claude-sonnet-4-6",
        system_prompt=(
            "You are AutoAD-Researcher's DeepAgentsHarness backend. "
            "You must follow filesystem permissions strictly. "
            "You must write only schema-valid JSON artifacts."
        ),
        backend=FilesystemBackend(
            root_dir=str(ROOT),
            virtual_mode=True,
        ),
        permissions=[
            FilesystemPermission(
                operations=["read", "write"],
                paths=["/runs/run_demo/**"],
                mode="allow",
            ),
            FilesystemPermission(
                operations=["read", "write"],
                paths=["/**"],
                mode="deny",
            ),
        ],
    )

    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": read_task(),
                }
            ]
        }
    )

    print("Agent result:")
    print(result)

    validate_outputs()


if __name__ == "__main__":
    main()
