from pathlib import Path

from autoad_researcher.experiment.public_weights import CLIP_WEIGHT_SHA256, resolve_clip_weight


def test_public_weight_missing_is_explicit_and_does_not_download_by_default(tmp_path: Path):
    result = resolve_clip_weight(cache_dir=tmp_path)

    assert result.status == "missing"
    assert result.expected_sha256 == CLIP_WEIGHT_SHA256
    assert result.path is None


def test_public_weight_user_path_is_verified_with_pinned_sha(tmp_path: Path):
    path = tmp_path / "clip.pt"
    path.write_bytes(b"wrong")

    result = resolve_clip_weight(user_path=str(path), cache_dir=tmp_path)

    assert result.status == "awaiting_user"
    assert result.actual_sha256 is not None
    assert "SHA256" in (result.error or "")
