"""Generic external asset contracts."""

from autoad_researcher.assets.io import (
    asset_manifest_sha256,
    asset_plan_sha256,
    load_asset_plan,
    write_asset_manifest,
    write_asset_plan,
)
from autoad_researcher.assets.builder import (
    AssetFetcher,
    AssetProbe,
    prepare_assets,
)
from autoad_researcher.assets.models import (
    AssetManifest,
    AssetManifestEntry,
    AssetPlan,
    AssetRequirement,
    AssetSource,
    AssetValidation,
)

__all__ = [
    "AssetManifest",
    "AssetManifestEntry",
    "AssetPlan",
    "AssetRequirement",
    "AssetSource",
    "AssetValidation",
    "AssetFetcher",
    "AssetProbe",
    "asset_manifest_sha256",
    "asset_plan_sha256",
    "load_asset_plan",
    "prepare_assets",
    "write_asset_manifest",
    "write_asset_plan",
]
