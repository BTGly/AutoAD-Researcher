"""Tests for controlled asset preparation."""

from pathlib import Path

from autoad_researcher.assets import AssetPlan, AssetValidation, prepare_assets
from tests.test_asset_contracts import valid_asset_plan


def plan_with_local_source(source_path: str, expected_sha256: str | None = None) -> AssetPlan:
    data = valid_asset_plan()
    data["network_during_prepare"] = False
    data["assets"][0]["source"] = {"source_type": "local_path", "uri": source_path}
    data["assets"][0]["expected_sha256"] = expected_sha256
    data["assets"][0]["validation"] = [
        {
            "validation_id": "asset_exists",
            "kind": "file_exists",
            "parameters": {},
            "required": True,
            "network": False,
        }
    ]
    return AssetPlan.model_validate(data)


def test_prepare_local_asset_success(tmp_path: Path):
    workspace = tmp_path / "workspace"
    source = workspace / "assets/source/model.pt"
    source.parent.mkdir(parents=True)
    source.write_text("weights", encoding="utf-8")
    plan = plan_with_local_source("assets/source/model.pt")

    manifest = prepare_assets(plan, workspace_root=workspace, run_dir=tmp_path / "run")

    assert manifest.assets[0].status == "prepared"
    assert manifest.assets[0].sha256
    assert (tmp_path / "run" / manifest.assets[0].path).read_text(encoding="utf-8") == "weights"


def test_prepare_writes_manifest(tmp_path: Path):
    workspace = tmp_path / "workspace"
    source = workspace / "assets/source/model.pt"
    source.parent.mkdir(parents=True)
    source.write_text("weights", encoding="utf-8")
    plan = plan_with_local_source("assets/source/model.pt")

    manifest = prepare_assets(
        plan,
        workspace_root=workspace,
        run_dir=tmp_path / "run",
        manifest_path=tmp_path / "run/assets/manifest.json",
    )

    assert (tmp_path / "run/assets/manifest.json").is_file()
    assert manifest.manifest_sha256


def test_sha_mismatch_required_asset_fails(tmp_path: Path):
    workspace = tmp_path / "workspace"
    source = workspace / "assets/source/model.pt"
    source.parent.mkdir(parents=True)
    source.write_text("weights", encoding="utf-8")
    plan = plan_with_local_source("assets/source/model.pt", expected_sha256="0" * 64)

    manifest = prepare_assets(plan, workspace_root=workspace, run_dir=tmp_path / "run")

    assert manifest.assets[0].status == "failed"
    assert manifest.assets[0].failure_code == "ASSET_SHA_MISMATCH"


def test_existing_destination_with_different_hash_fails_without_overwrite(tmp_path: Path):
    workspace = tmp_path / "workspace"
    source = workspace / "assets/source/model.pt"
    source.parent.mkdir(parents=True)
    source.write_text("new", encoding="utf-8")
    run_dir = tmp_path / "run"
    existing = run_dir / "assets/prepared/backbone/model.pt"
    existing.parent.mkdir(parents=True)
    existing.write_text("old", encoding="utf-8")
    plan = plan_with_local_source("assets/source/model.pt", expected_sha256="0" * 64)

    manifest = prepare_assets(plan, workspace_root=workspace, run_dir=run_dir)

    assert manifest.assets[0].status == "failed"
    assert existing.read_text(encoding="utf-8") == "old"


def test_missing_optional_asset_is_skipped(tmp_path: Path):
    data = valid_asset_plan()
    data["network_during_prepare"] = False
    data["assets"][0]["required"] = False
    data["assets"][0]["source"] = {"source_type": "local_path", "uri": "assets/source/missing.pt"}
    data["assets"][0]["expected_sha256"] = None
    plan = AssetPlan.model_validate(data)

    manifest = prepare_assets(plan, workspace_root=tmp_path / "workspace", run_dir=tmp_path / "run")

    assert manifest.assets[0].status == "skipped"
    assert manifest.assets[0].failure_code is None


def test_url_asset_requires_network_permission(tmp_path: Path):
    data = valid_asset_plan(network_during_prepare=False)
    plan = AssetPlan.model_validate(data)

    manifest = prepare_assets(plan, workspace_root=tmp_path, run_dir=tmp_path / "run")

    assert manifest.assets[0].status == "failed"
    assert manifest.assets[0].failure_code == "ASSET_DOWNLOAD_FAILED"


def test_url_asset_uses_injected_fetcher(tmp_path: Path):
    data = valid_asset_plan()
    data["assets"][0]["expected_sha256"] = None
    data["assets"][0]["validation"] = [
        {
            "validation_id": "asset_exists",
            "kind": "file_exists",
            "parameters": {},
            "required": True,
            "network": False,
        }
    ]
    plan = AssetPlan.model_validate(data)

    def fetcher(url: str, destination: Path) -> None:
        destination.write_text(f"downloaded from {url}", encoding="utf-8")

    manifest = prepare_assets(plan, workspace_root=tmp_path, run_dir=tmp_path / "run", fetcher=fetcher)

    assert manifest.assets[0].status == "prepared"


def test_required_framework_load_without_probe_fails(tmp_path: Path):
    workspace = tmp_path / "workspace"
    source = workspace / "assets/source/model.pt"
    source.parent.mkdir(parents=True)
    source.write_text("weights", encoding="utf-8")
    data = valid_asset_plan()
    data["network_during_prepare"] = False
    data["assets"][0]["source"] = {"source_type": "local_path", "uri": "assets/source/model.pt"}
    data["assets"][0]["expected_sha256"] = None
    data["assets"][0]["validation"] = [
        {
            "validation_id": "load_weight",
            "kind": "framework_load",
            "parameters": {},
            "required": True,
            "network": False,
        }
    ]
    plan = AssetPlan.model_validate(data)

    manifest = prepare_assets(plan, workspace_root=workspace, run_dir=tmp_path / "run")

    assert manifest.assets[0].status == "failed"
    assert manifest.assets[0].failure_code == "ASSET_OFFLINE_VALIDATION_FAILED"


def test_custom_probe_success(tmp_path: Path):
    workspace = tmp_path / "workspace"
    source = workspace / "assets/source/model.pt"
    source.parent.mkdir(parents=True)
    source.write_text("weights", encoding="utf-8")
    data = valid_asset_plan()
    data["network_during_prepare"] = False
    data["assets"][0]["source"] = {"source_type": "local_path", "uri": "assets/source/model.pt"}
    data["assets"][0]["expected_sha256"] = None
    data["assets"][0]["validation"] = [
        {
            "validation_id": "load_weight",
            "kind": "custom_probe",
            "parameters": {},
            "required": True,
            "network": False,
        }
    ]
    plan = AssetPlan.model_validate(data)

    def probe(path: Path, validation: AssetValidation) -> bool:
        return path.read_text(encoding="utf-8") == "weights"

    manifest = prepare_assets(
        plan,
        workspace_root=workspace,
        run_dir=tmp_path / "run",
        probes={"load_weight": probe},
    )

    assert manifest.assets[0].status == "prepared"
