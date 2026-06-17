"""Read, write, and hash environment plans."""

import json
from pathlib import Path

import yaml

from autoad_researcher.benchmarks.hashing import canonical_sha256
from autoad_researcher.environments.models import EnvironmentPlan


def load_environment_plan(path: Path | str) -> EnvironmentPlan:
    """Load an EnvironmentPlan from JSON or YAML."""
    plan_path = Path(path)
    data = _load_mapping(plan_path)
    return EnvironmentPlan.model_validate(data)


def write_environment_plan(plan: EnvironmentPlan, path: Path | str) -> None:
    """Write an EnvironmentPlan as deterministic JSON."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = plan.model_dump(mode="json", exclude_none=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def environment_plan_sha256(plan: EnvironmentPlan) -> str:
    """Return stable SHA-256 for an EnvironmentPlan."""
    return canonical_sha256(plan)


def _load_mapping(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix in {".yaml", ".yml"}:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    elif path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        raise ValueError(f"unsupported environment plan format: {path.suffix}")
    if not isinstance(data, dict):
        raise ValueError("environment plan file must contain a mapping")
    return data
