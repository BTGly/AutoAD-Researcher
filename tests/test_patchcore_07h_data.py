from __future__ import annotations

import struct
from pathlib import Path
import zlib

import pytest

from autoad_researcher.benchmarks.patchcore_07h_data import prepare_07h_data


def _png(path: Path, *, color: int = 1) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes([0, color])))
        + chunk(b"IEND", b"")
    )


def _source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    for index in range(4):
        _png(root / "bottle/train/good" / f"{index:03d}.png")
    for label in ("good", "scratch"):
        for index in range(2):
            _png(root / "bottle/test" / label / f"{index:03d}.png")
            if label != "good":
                _png(root / "bottle/ground_truth" / label / f"{index:03d}_mask.png")
    return root


def test_preparation_is_deterministic_idempotent_and_disjoint(tmp_path: Path):
    source = _source(tmp_path)
    run_dir = tmp_path / "run"
    first = prepare_07h_data(source_root=source, run_dir=run_dir, train_limit=3, split_seed=0)
    second = prepare_07h_data(source_root=source, run_dir=run_dir, train_limit=3, split_seed=0)

    assert first == second
    assert len(list((run_dir / "data/shared_train/bottle/train/good").iterdir())) == 3
    assert (run_dir / "data/b_dev/bottle/train/good").is_symlink()
    dev = (run_dir / "artifacts/07h/dataset/b_dev_manifest.json").read_text(encoding="utf-8")
    test = (run_dir / "artifacts/07h/dataset/b_test_manifest.json").read_text(encoding="utf-8")
    assert dev != test


def test_missing_or_orphan_mask_blocks_preparation(tmp_path: Path):
    source = _source(tmp_path)
    (source / "bottle/ground_truth/scratch/000_mask.png").unlink()
    with pytest.raises(ValueError, match="missing mask"):
        prepare_07h_data(source_root=source, run_dir=tmp_path / "run")

    source = _source(tmp_path / "other")
    _png(source / "bottle/ground_truth/scratch/orphan_mask.png")
    with pytest.raises(ValueError, match="orphan"):
        prepare_07h_data(source_root=source, run_dir=tmp_path / "other-run")


def test_source_change_or_projection_extra_file_is_rejected(tmp_path: Path):
    source = _source(tmp_path)
    run_dir = tmp_path / "run"
    prepare_07h_data(source_root=source, run_dir=run_dir)
    (run_dir / "data/b_dev/unregistered.txt").write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="unregistered"):
        prepare_07h_data(source_root=source, run_dir=run_dir)

    (run_dir / "data/b_dev/unregistered.txt").unlink()
    _png(source / "bottle/train/good/000.png", color=2)
    with pytest.raises(ValueError, match="manifest differs"):
        prepare_07h_data(source_root=source, run_dir=run_dir)
