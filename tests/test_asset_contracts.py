"""Tests for generic asset contracts."""

from pathlib import Path

import pytest

from autoad_researcher.assets import (
    AssetManifest,
    AssetManifestEntry,
    AssetPlan,
    AssetSource,
    asset_manifest_sha256,
    asset_plan_sha256,
    load_asset_plan,
    write_asset_manifest,
    write_asset_plan,
)


def valid_asset_plan(**overrides):
    data = {
        "schema_version": 1,
        "plan_id": "asset_plan_v0",
        "run_id": "run_asset_fixture",
        "assets": [
            {
                "asset_id": "backbone_weight",
                "kind": "model_weight",
                "source": {
                    "source_type": "url",
                    "uri": "https://example.com/weights/model.pt",
                    "description": "fixture source",
                },
                "destination": "assets/prepared/backbone/model.pt",
                "expected_sha256": "a" * 64,
                "required": True,
                "validation": [
                    {
                        "validation_id": "backbone_sha",
                        "kind": "sha256",
                        "parameters": {},
                        "required": True,
                        "network": False,
                    }
                ],
            }
        ],
        "network_during_prepare": True,
        "network_during_execution": False,
    }
    data.update(overrides)
    return data


def test_valid_asset_plan_contract():
    plan = AssetPlan.model_validate(valid_asset_plan())

    assert plan.assets[0].asset_id == "backbone_weight"
    assert asset_plan_sha256(plan) == asset_plan_sha256(plan)


def test_asset_plan_rejects_execution_network():
    data = valid_asset_plan(network_during_execution=True)

    with pytest.raises(Exception):
        AssetPlan.model_validate(data)


def test_asset_destination_escape_rejected():
    data = valid_asset_plan()
    data["assets"][0]["destination"] = "../model.pt"

    with pytest.raises(Exception):
        AssetPlan.model_validate(data)


def test_asset_destination_must_be_under_assets_prepared():
    data = valid_asset_plan()
    data["assets"][0]["destination"] = "runs/run_demo/model.pt"

    with pytest.raises(ValueError, match="assets/prepared"):
        AssetPlan.model_validate(data)


def test_asset_url_must_be_https():
    data = valid_asset_plan()
    data["assets"][0]["source"]["uri"] = "http://example.com/model.pt"

    with pytest.raises(ValueError, match="https"):
        AssetPlan.model_validate(data)


def test_duplicate_asset_id_rejected():
    data = valid_asset_plan()
    data["assets"].append(dict(data["assets"][0]))

    with pytest.raises(ValueError, match="duplicate asset_id"):
        AssetPlan.model_validate(data)


def test_asset_plan_yaml_roundtrip(tmp_path: Path):
    path = tmp_path / "asset_plan.yaml"
    path.write_text(
        """
schema_version: 1
plan_id: asset_plan_v0
run_id: run_asset_fixture
assets:
  - asset_id: local_index
    kind: index
    source:
      source_type: local_path
      uri: assets/source/index.faiss
    destination: assets/prepared/index/index.faiss
    expected_sha256: null
    required: true
    validation:
      - validation_id: index_exists
        kind: file_exists
        parameters: {}
        required: true
        network: false
network_during_prepare: false
network_during_execution: false
""",
        encoding="utf-8",
    )

    plan = load_asset_plan(path)

    assert plan.assets[0].kind == "index"


def test_write_asset_plan_json(tmp_path: Path):
    plan = AssetPlan.model_validate(valid_asset_plan())
    path = tmp_path / "plan.json"

    write_asset_plan(plan, path)

    assert load_asset_plan(path) == plan


def test_manifest_hash_ignores_manifest_sha_field():
    source = AssetSource(source_type="url", uri="https://example.com/model.pt")
    entry = AssetManifestEntry(
        asset_id="backbone_weight",
        kind="model_weight",
        source=source,
        path="assets/prepared/backbone/model.pt",
        sha256="a" * 64,
        required=True,
        status="prepared",
    )
    payload = {
        "schema_version": 1,
        "plan_id": "asset_plan_v0",
        "run_id": "run_asset_fixture",
        "assets": [entry.model_dump(mode="json")],
    }
    payload["manifest_sha256"] = asset_manifest_sha256(payload)
    manifest = AssetManifest.model_validate(payload)
    modified = manifest.model_copy(update={"manifest_sha256": "b" * 64})

    assert asset_manifest_sha256(manifest) == asset_manifest_sha256(modified)


def test_failed_manifest_entry_requires_failure_code():
    source = AssetSource(source_type="url", uri="https://example.com/model.pt")
    payload = {
        "schema_version": 1,
        "plan_id": "asset_plan_v0",
        "run_id": "run_asset_fixture",
        "assets": [
            {
                "asset_id": "backbone_weight",
                "kind": "model_weight",
                "source": source.model_dump(mode="json"),
                "path": "assets/prepared/backbone/model.pt",
                "sha256": None,
                "required": True,
                "status": "failed",
            }
        ],
        "manifest_sha256": "a" * 64,
    }

    with pytest.raises(ValueError, match="failure_code"):
        AssetManifest.model_validate(payload)


def test_write_asset_manifest_json(tmp_path: Path):
    source = AssetSource(source_type="manual", uri="operator-provided")
    payload = {
        "schema_version": 1,
        "plan_id": "asset_plan_v0",
        "run_id": "run_asset_fixture",
        "assets": [
            {
                "asset_id": "manual_tokenizer",
                "kind": "tokenizer",
                "source": source.model_dump(mode="json"),
                "path": "assets/prepared/tokenizer/tokenizer.json",
                "sha256": "c" * 64,
                "required": False,
                "status": "prepared",
            }
        ],
    }
    payload["manifest_sha256"] = asset_manifest_sha256(payload)
    manifest = AssetManifest.model_validate(payload)
    path = tmp_path / "manifest.json"

    write_asset_manifest(manifest, path)

    assert path.is_file()
