"""Read, write, and hash asset contracts."""

import json
from pathlib import Path
from typing import Any

import yaml

from autoad_researcher.assets.models import AssetManifest, AssetPlan
from autoad_researcher.benchmarks.hashing import canonical_sha256


def load_asset_plan(path: Path | str) -> AssetPlan:
    plan_path = Path(path)
    data = _load_mapping(plan_path)
    return AssetPlan.model_validate(data)


def write_asset_plan(plan: AssetPlan, path: Path | str) -> None:
    _write_json_model(plan, path)


def write_asset_manifest(manifest: AssetManifest, path: Path | str) -> None:
    _write_json_model(manifest, path)


def asset_plan_sha256(plan: AssetPlan) -> str:
    return canonical_sha256(plan)


def asset_manifest_sha256(manifest: AssetManifest | dict[str, Any]) -> str:
    if isinstance(manifest, AssetManifest):
        payload = manifest.model_dump(mode="json", exclude={"manifest_sha256"})
    else:
        payload = dict(manifest)
        payload.pop("manifest_sha256", None)
    return canonical_sha256(payload)


def _load_mapping(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix in {".yaml", ".yml"}:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    elif path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        raise ValueError(f"unsupported asset plan format: {path.suffix}")
    if not isinstance(data, dict):
        raise ValueError("asset plan must be a mapping")
    return data


def _write_json_model(model, path: Path | str) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = model.model_dump(mode="json", exclude_none=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
