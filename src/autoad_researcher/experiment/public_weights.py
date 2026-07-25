"""Public model-weight resolution with an explicit user fallback."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Literal
from urllib.request import urlopen

from pydantic import BaseModel, ConfigDict, Field


CLIP_WEIGHT_NAME = "ViT-L-14-336px.pt"
CLIP_WEIGHT_URL = (
    "https://openaipublic.azureedge.net/clip/models/"
    "3035c92b350959924f9f00213499208652fc7ea050643e8b385c2dac08641f02/"
    "ViT-L-14-336px.pt"
)
CLIP_WEIGHT_SHA256 = "3035c92b350959924f9f00213499208652fc7ea050643e8b385c2dac08641f02"


class PublicWeightResolution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    asset_id: str = Field(min_length=1)
    status: Literal["available", "missing", "failed", "awaiting_user"]
    path: str | None = None
    source: Literal["cache", "user", "official_download", "unknown"] = "unknown"
    expected_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    actual_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    error: str | None = None


def resolve_clip_weight(
    *,
    asset_id: str = "clip_vit_l_14_336px",
    user_path: str | None = None,
    cache_dir: Path | None = None,
    auto_download: bool = False,
    timeout_seconds: int = 60,
) -> PublicWeightResolution:
    """Find and verify the pinned public weight.

    Downloads are opt-in so an experiment preparation request never starts a
    large network transfer without an explicit product action.
    """

    if user_path:
        result = _verify_path(asset_id, Path(user_path).expanduser(), "user")
        return result if result.status == "available" else result.model_copy(update={"status": "awaiting_user"})

    resolved_cache = cache_dir or Path(os.environ.get("CLIP_CACHE_DIR", Path.home() / ".cache" / "autoad" / "clip"))
    cached = resolved_cache / CLIP_WEIGHT_NAME
    if cached.is_file():
        return _verify_path(asset_id, cached, "cache")
    if not auto_download:
        return PublicWeightResolution(
            asset_id=asset_id,
            status="missing",
            expected_sha256=CLIP_WEIGHT_SHA256,
            error="official CLIP weight is not present in the configured cache",
        )
    try:
        resolved_cache.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=f"{CLIP_WEIGHT_NAME}.", dir=resolved_cache, delete=False) as handle:
            temporary = Path(handle.name)
            with urlopen(CLIP_WEIGHT_URL, timeout=timeout_seconds) as response:
                while chunk := response.read(1024 * 1024):
                    handle.write(chunk)
        result = _verify_path(asset_id, temporary, "official_download")
        if result.status != "available":
            temporary.unlink(missing_ok=True)
            return result.model_copy(update={"status": "awaiting_user"})
        temporary.replace(cached)
        return result.model_copy(update={"path": str(cached)})
    except Exception as exc:
        if "temporary" in locals():
            temporary.unlink(missing_ok=True)
        return PublicWeightResolution(
            asset_id=asset_id,
            status="awaiting_user",
            expected_sha256=CLIP_WEIGHT_SHA256,
            error=f"official CLIP weight download failed: {exc}",
        )


def _verify_path(asset_id: str, path: Path, source: Literal["cache", "user", "official_download"]) -> PublicWeightResolution:
    if not path.is_file():
        return PublicWeightResolution(asset_id=asset_id, status="missing", path=str(path), source=source, expected_sha256=CLIP_WEIGHT_SHA256, error="weight file does not exist")
    actual = _sha256_file(path)
    if actual != CLIP_WEIGHT_SHA256:
        return PublicWeightResolution(
            asset_id=asset_id,
            status="failed",
            path=str(path),
            source=source,
            expected_sha256=CLIP_WEIGHT_SHA256,
            actual_sha256=actual,
            error="weight SHA256 does not match the pinned official artifact",
        )
    return PublicWeightResolution(
        asset_id=asset_id,
        status="available",
        path=str(path),
        source=source,
        expected_sha256=CLIP_WEIGHT_SHA256,
        actual_sha256=actual,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
