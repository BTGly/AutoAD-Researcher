"""Dataset preflight — verify MVTec bottle structure and build structural manifest."""

import os
from pathlib import Path
from typing import Mapping

from autoad_researcher.benchmarks.errors import BenchmarkPreflightError
from autoad_researcher.benchmarks.evidence import BenchmarkDatasetFileEntry, BenchmarkDatasetManifest
from autoad_researcher.benchmarks.hashing import canonical_sha256


def resolve_dataset_root(*, case, environ: Mapping[str, str], workspace_root: Path) -> Path:
    env_name = case.dataset.root_env
    val = environ.get(env_name, "")
    if not val:
        raise BenchmarkPreflightError(check_name="dataset", code="DATASET_ROOT_ENV_MISSING",
            message=f"environment variable {env_name} not set")
    val = val.strip()
    if not val:
        raise BenchmarkPreflightError(check_name="dataset", code="DATASET_ROOT_ENV_EMPTY",
            message=f"environment variable {env_name} is empty")
    root = Path(val).resolve(strict=False)
    if not root.exists():
        raise BenchmarkPreflightError(check_name="dataset", code="DATASET_ROOT_NOT_FOUND",
            message="dataset root does not exist")
    allowed = (workspace_root / "datasets").resolve(strict=True)
    try:
        root.resolve(strict=True).relative_to(allowed)
    except (ValueError, FileNotFoundError):
        raise BenchmarkPreflightError(check_name="dataset", code="DATASET_PATH_OUTSIDE_WORKSPACE",
            message="dataset must be inside workspace/datasets")
    return root.resolve(strict=True)


def build_dataset_manifest(*, case, dataset_root: Path, workspace_root: Path) -> BenchmarkDatasetManifest:
    cat = case.dataset.category
    train_good_dir = dataset_root / cat / "train" / "good"
    test_dir = dataset_root / cat / "test"
    gt_dir = dataset_root / cat / "ground_truth"

    if not train_good_dir.is_dir():
        raise BenchmarkPreflightError(check_name="dataset", code="DATASET_REQUIRED_PATH_MISSING",
            message="train/good directory missing")
    if not test_dir.is_dir():
        raise BenchmarkPreflightError(check_name="dataset", code="DATASET_REQUIRED_PATH_MISSING",
            message="test directory missing")
    if not gt_dir.is_dir():
        raise BenchmarkPreflightError(check_name="dataset", code="DATASET_REQUIRED_PATH_MISSING",
            message="ground_truth directory missing")

    train_good_files = _scan_dir(train_good_dir, required_suffix=".png")
    test_files = sorted(test_dir.rglob("*"))
    test_good_files = []
    test_anomaly_files: dict[str, list[Path]] = {}
    for f in test_files:
        if f.is_symlink() or f.parent.is_symlink():
            raise BenchmarkPreflightError(check_name="dataset", code="DATASET_SYMLINK_FORBIDDEN",
                message="dataset must not contain symlinks")
        if not f.is_file():
            continue
        _validate_image_file(f)
        suffix = f.suffix.lower()
        if suffix != ".png":
            raise BenchmarkPreflightError(check_name="dataset", code="DATASET_UNEXPECTED_FILE",
                message=f"unexpected file type in test: {f.name}")
        rel = f.relative_to(test_dir)
        parts = rel.parts
        if parts[0] == "good":
            test_good_files.append(f)
        else:
            anomaly_type = parts[0]
            test_anomaly_files.setdefault(anomaly_type, []).append(f)

    if not train_good_files:
        raise BenchmarkPreflightError(check_name="dataset", code="DATASET_TRAIN_GOOD_EMPTY",
            message="train/good is empty")
    if not test_good_files:
        raise BenchmarkPreflightError(check_name="dataset", code="DATASET_TEST_GOOD_EMPTY",
            message="test/good is empty")
    if not test_anomaly_files:
        raise BenchmarkPreflightError(check_name="dataset", code="DATASET_NO_ANOMALY",
            message="no anomaly types in test")

    # Verify mask correspondence
    gt_types = set()
    for d in gt_dir.iterdir():
        if d.is_symlink():
            raise BenchmarkPreflightError(check_name="dataset", code="DATASET_SYMLINK_FORBIDDEN",
                message="ground_truth must not contain symlinks")
        if d.is_dir():
            gt_types.add(d.name)

    anomaly_types = set(test_anomaly_files.keys())
    if anomaly_types != gt_types:
        raise BenchmarkPreflightError(check_name="dataset", code="DATASET_GROUND_TRUTH_TYPE_MISMATCH",
            message=f"anomaly types {sorted(anomaly_types)} != ground_truth {sorted(gt_types)}")

    mask_count = 0
    for atype in anomaly_types:
        for img in test_anomaly_files[atype]:
            mask_path = gt_dir / atype / f"{img.stem}_mask.png"
            if not mask_path.is_file():
                raise BenchmarkPreflightError(check_name="dataset", code="DATASET_MASK_MISSING",
                    message=f"missing mask for {img.name}")
            _validate_image_file(mask_path)
            mask_count += 1

        # Check for orphan masks
        for mf in sorted((gt_dir / atype).iterdir()):
            if mf.is_file() and mf.suffix.lower() == ".png":
                stem = mf.stem.removesuffix("_mask")
                expected_img = test_dir / atype / f"{stem}.png"
                if not expected_img.exists():
                    raise BenchmarkPreflightError(check_name="dataset", code="DATASET_ORPHAN_MASK",
                        message=f"orphan mask {mf.name}")

    all_files: list[BenchmarkDatasetFileEntry] = []
    for f in sorted(train_good_files):
        all_files.append(BenchmarkDatasetFileEntry(
            relative_path=str(f.relative_to(dataset_root).as_posix()), size_bytes=f.stat().st_size))
    for f in sorted(test_good_files):
        all_files.append(BenchmarkDatasetFileEntry(
            relative_path=str(f.relative_to(dataset_root).as_posix()), size_bytes=f.stat().st_size))
    for atype in sorted(test_anomaly_files):
        for img in sorted(test_anomaly_files[atype]):
            all_files.append(BenchmarkDatasetFileEntry(
                relative_path=str(img.relative_to(dataset_root).as_posix()), size_bytes=img.stat().st_size))
            mask = gt_dir / atype / f"{img.stem}_mask.png"
            all_files.append(BenchmarkDatasetFileEntry(
                relative_path=str(mask.relative_to(dataset_root).as_posix()), size_bytes=mask.stat().st_size))

    manifest_data = {
        "schema_version": 1, "dataset_name": case.dataset.name, "category": cat,
        "root_env": case.dataset.root_env, "manifest_strategy": "relative_path_size_v1",
        "files": [{"relative_path": f.relative_path, "size_bytes": f.size_bytes} for f in all_files],
        "train_good_count": len(train_good_files), "test_good_count": len(test_good_files),
        "test_anomaly_count": sum(len(v) for v in test_anomaly_files.values()),
        "mask_count": mask_count,
    }
    return BenchmarkDatasetManifest(
        schema_version=1, dataset_name=case.dataset.name, category=cat,
        root_env=case.dataset.root_env, manifest_strategy="relative_path_size_v1",
        files=all_files, train_good_count=len(train_good_files),
        test_good_count=len(test_good_files),
        test_anomaly_count=sum(len(v) for v in test_anomaly_files.values()),
        mask_count=mask_count, manifest_sha256=canonical_sha256(manifest_data),
    )


def _scan_dir(directory: Path, required_suffix: str) -> list[Path]:
    files = []
    for f in sorted(directory.rglob("*")):
        if f.is_symlink() or f.parent.is_symlink():
            raise BenchmarkPreflightError(check_name="dataset", code="DATASET_SYMLINK_FORBIDDEN",
                message="dataset must not contain symlinks")
        if not f.is_file():
            continue
        _validate_image_file(f)
        if f.suffix.lower() != required_suffix:
            raise BenchmarkPreflightError(check_name="dataset", code="DATASET_UNEXPECTED_FILE",
                message=f"unexpected file type: {f.name}")
        files.append(f)
    if not files:
        raise BenchmarkPreflightError(check_name="dataset", code="DATASET_TRAIN_GOOD_EMPTY",
            message="directory is empty")
    return files


def _validate_image_file(f: Path) -> None:
    if f.stat().st_size == 0:
        raise BenchmarkPreflightError(check_name="dataset", code="DATASET_ZERO_BYTE_FILE",
            message=f"zero-byte file: {f.name}")
