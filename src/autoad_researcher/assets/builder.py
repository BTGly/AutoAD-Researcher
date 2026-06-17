"""Controlled asset preparation and offline validation."""

from collections.abc import Callable
from pathlib import Path
from urllib.request import urlretrieve

from autoad_researcher.assets.io import asset_manifest_sha256, write_asset_manifest
from autoad_researcher.assets.models import (
    AssetManifest,
    AssetManifestEntry,
    AssetPlan,
    AssetRequirement,
    AssetValidation,
)
from autoad_researcher.benchmarks.hashing import sha256_file

AssetFetcher = Callable[[str, Path], None]
AssetProbe = Callable[[Path, AssetValidation], bool]


def prepare_assets(
    plan: AssetPlan,
    *,
    workspace_root: Path | str,
    run_dir: Path | str,
    fetcher: AssetFetcher | None = None,
    probes: dict[str, AssetProbe] | None = None,
    manifest_path: Path | str | None = None,
) -> AssetManifest:
    """Prepare assets described by a plan and return an AssetManifest.

    The function writes only below ``run_dir`` and never overwrites an existing
    destination whose SHA differs from the expected asset SHA.
    """
    workspace = Path(workspace_root)
    run_root = Path(run_dir)
    entries = [
        _prepare_one(
            asset,
            plan=plan,
            workspace_root=workspace,
            run_dir=run_root,
            fetcher=fetcher or _urlretrieve_fetcher,
            probes=probes or {},
        )
        for asset in plan.assets
    ]
    payload = {
        "schema_version": 1,
        "plan_id": plan.plan_id,
        "run_id": plan.run_id,
        "assets": [entry.model_dump(mode="json", exclude_none=True) for entry in entries],
    }
    payload["manifest_sha256"] = asset_manifest_sha256(payload)
    manifest = AssetManifest.model_validate(payload)
    if manifest_path is not None:
        write_asset_manifest(manifest, manifest_path)
    return manifest


def _prepare_one(
    asset: AssetRequirement,
    *,
    plan: AssetPlan,
    workspace_root: Path,
    run_dir: Path,
    fetcher: AssetFetcher,
    probes: dict[str, AssetProbe],
) -> AssetManifestEntry:
    destination = _resolve_inside(run_dir, asset.destination)
    try:
        if destination.exists():
            return _entry_from_existing(asset, destination, run_dir, probes)

        destination.parent.mkdir(parents=True, exist_ok=True)
        if asset.source.source_type == "local_path":
            source = _resolve_inside(workspace_root, asset.source.uri)
            if not source.is_file():
                return _unavailable(asset, "ASSET_DOWNLOAD_FAILED", f"source not found: {asset.source.uri}")
            destination.write_bytes(source.read_bytes())
        elif asset.source.source_type == "url":
            if not plan.network_during_prepare:
                return _unavailable(asset, "ASSET_DOWNLOAD_FAILED", "network disabled during asset prepare")
            fetcher(asset.source.uri, destination)
        elif asset.source.source_type in {"manual", "registry"}:
            return _unavailable(asset, "ASSET_DOWNLOAD_FAILED", "asset must already exist at destination")
        else:
            return _unavailable(asset, "ASSET_DOWNLOAD_FAILED", "unsupported asset source")

        return _entry_from_existing(asset, destination, run_dir, probes)
    except Exception as exc:
        if destination.exists():
            destination.unlink()
        return _unavailable(asset, "ASSET_DOWNLOAD_FAILED", str(exc))


def _entry_from_existing(
    asset: AssetRequirement,
    destination: Path,
    run_dir: Path,
    probes: dict[str, AssetProbe],
) -> AssetManifestEntry:
    actual_sha = sha256_file(destination)
    if asset.expected_sha256 and actual_sha != asset.expected_sha256:
        return _unavailable(
            asset,
            "ASSET_SHA_MISMATCH",
            f"expected {asset.expected_sha256}, got {actual_sha}",
            path=_relative_to(destination, run_dir),
            sha256=actual_sha,
        )

    validation_error = _validate_asset(asset, destination, actual_sha, probes)
    if validation_error is not None:
        return _unavailable(
            asset,
            validation_error[0],
            validation_error[1],
            path=_relative_to(destination, run_dir),
            sha256=actual_sha,
        )

    return AssetManifestEntry(
        asset_id=asset.asset_id,
        kind=asset.kind,
        source=asset.source,
        path=_relative_to(destination, run_dir),
        sha256=actual_sha,
        required=asset.required,
        status="prepared",
    )


def _validate_asset(
    asset: AssetRequirement,
    path: Path,
    actual_sha: str,
    probes: dict[str, AssetProbe],
) -> tuple[str, str] | None:
    for validation in asset.validation:
        if validation.kind == "sha256":
            expected = validation.parameters.get("sha256") or asset.expected_sha256
            if expected and actual_sha != expected:
                return "ASSET_SHA_MISMATCH", f"validation {validation.validation_id} SHA mismatch"
        elif validation.kind == "file_exists":
            if not path.is_file():
                return "ASSET_OFFLINE_VALIDATION_FAILED", f"validation {validation.validation_id} file missing"
        elif validation.kind in {"framework_load", "cli_inspect", "custom_probe"}:
            probe = probes.get(validation.validation_id) or probes.get(validation.kind)
            if probe is None:
                if validation.required:
                    return "ASSET_OFFLINE_VALIDATION_FAILED", f"validation {validation.validation_id} has no probe"
                continue
            if not probe(path, validation):
                return "ASSET_OFFLINE_VALIDATION_FAILED", f"validation {validation.validation_id} failed"
        else:
            return "ASSET_OFFLINE_VALIDATION_FAILED", f"unsupported validation kind: {validation.kind}"
    return None


def _unavailable(
    asset: AssetRequirement,
    code: str,
    message: str,
    *,
    path: str | None = None,
    sha256: str | None = None,
) -> AssetManifestEntry:
    status = "failed" if asset.required else "skipped"
    return AssetManifestEntry(
        asset_id=asset.asset_id,
        kind=asset.kind,
        source=asset.source,
        path=path or asset.destination,
        sha256=sha256,
        required=asset.required,
        status=status,
        failure_code=code if status == "failed" else None,
        failure_message=message if status == "failed" else None,
    )


def _resolve_inside(root: Path, relative: str) -> Path:
    candidate = root / relative
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve(strict=False)
    try:
        resolved_candidate.relative_to(resolved_root)
    except ValueError:
        raise ValueError(f"path escapes root: {relative}") from None
    return candidate


def _relative_to(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _urlretrieve_fetcher(url: str, destination: Path) -> None:
    urlretrieve(url, destination)
