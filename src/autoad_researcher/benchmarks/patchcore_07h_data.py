"""Immutable, auditable MVTec bottle projections for the physical 07H flow."""

from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from autoad_researcher.benchmarks.hashing import canonical_sha256

_DATASET_FILES = (
    "source_inventory.json",
    "train_good_manifest.json",
    "b_dev_manifest.json",
    "b_test_manifest.json",
    "split_summary.json",
)
_PROJECTION_CONTRACT = "projection_contract.json"


@dataclass(frozen=True)
class Prepared07HData:
    artifact_dir: Path
    data_dir: Path
    source_inventory_sha256: str
    train_manifest_sha256: str
    b_dev_manifest_sha256: str
    b_test_manifest_sha256: str


def prepare_07h_data(
    *,
    source_root: Path,
    run_dir: Path,
    train_limit: int = 40,
    split_seed: int = 0,
) -> Prepared07HData:
    """Validate source data, freeze manifests, and build symlink projections.

    ``source_root`` is the directory containing ``bottle/``.  It is never
    modified and no absolute source path is persisted in the manifests.
    """
    if train_limit <= 0:
        raise ValueError("train_limit must be positive")
    source_root = _source_root(source_root)
    artifact_dir = run_dir / "artifacts" / "07h" / "dataset"
    data_dir = run_dir / "data"
    inventory, train, b_dev, b_test = _manifests(
        source_root, train_limit=train_limit, split_seed=split_seed
    )
    payloads = {
        "source_inventory.json": inventory,
        "train_good_manifest.json": train,
        "b_dev_manifest.json": b_dev,
        "b_test_manifest.json": b_test,
        "split_summary.json": _split_summary(inventory, train, b_dev, b_test),
    }
    contract = _projection_contract(payloads)
    result = _prepared_from_payloads(artifact_dir, data_dir, payloads)

    if artifact_dir.exists() or data_dir.exists():
        _validate_existing(artifact_dir, data_dir, payloads, contract)
        return result

    artifact_dir.parent.mkdir(parents=True, exist_ok=True)
    data_dir.parent.mkdir(parents=True, exist_ok=True)
    artifact_stage = artifact_dir.with_name(f".{artifact_dir.name}.tmp")
    data_stage = data_dir.with_name(f".{data_dir.name}.tmp")
    _remove_if_exists(artifact_stage)
    _remove_if_exists(data_stage)
    try:
        artifact_stage.mkdir(parents=True)
        for name, payload in payloads.items():
            _write_json(artifact_stage / name, payload)
        _build_projection(data_stage, source_root, train, b_dev, b_test)
        _write_json(data_stage / _PROJECTION_CONTRACT, contract)
        os.replace(artifact_stage, artifact_dir)
        os.replace(data_stage, data_dir)
    except Exception:
        _remove_if_exists(artifact_stage)
        _remove_if_exists(data_stage)
        raise
    return result


def verify_07h_data(*, source_root: Path, run_dir: Path, train_limit: int = 40, split_seed: int = 0) -> Prepared07HData:
    """Recompute source evidence and reject changed or incomplete projections."""
    return prepare_07h_data(
        source_root=source_root,
        run_dir=run_dir,
        train_limit=train_limit,
        split_seed=split_seed,
    )


def _source_root(value: Path) -> Path:
    if value.is_symlink():
        raise ValueError("source root must not be a symlink")
    root = value.resolve(strict=True)
    if not (root / "bottle").is_dir():
        raise ValueError("source root must contain bottle/")
    return root


def _manifests(source_root: Path, *, train_limit: int, split_seed: int) -> tuple[dict, dict, dict, dict]:
    bottle = source_root / "bottle"
    train_files = _files(bottle / "train" / "good", suffix=".png")
    if not train_files:
        raise ValueError("bottle/train/good is empty")
    selected_train = train_files[:train_limit]
    test_root = bottle / "test"
    ground_truth = bottle / "ground_truth"
    test_types = _directories(test_root)
    if "good" not in test_types:
        raise ValueError("bottle/test/good is missing")
    anomaly_types = [name for name in test_types if name != "good"]
    if not anomaly_types:
        raise ValueError("bottle/test contains no anomaly type")
    if set(_directories(ground_truth)) != set(anomaly_types):
        raise ValueError("ground_truth anomaly types do not match test anomaly types")

    source_records = [_record(source_root, path, split="source", label="good", mask=None) for path in train_files]
    train_records = [_record(source_root, path, split="train", label="good", mask=None) for path in selected_train]
    b_dev_records: list[dict] = []
    b_test_records: list[dict] = []
    for label in sorted(test_types):
        files = _files(test_root / label, suffix=".png")
        if not files:
            raise ValueError(f"bottle/test/{label} is empty")
        pairs: list[tuple[Path, Path | None]] = []
        for image in files:
            mask = None if label == "good" else ground_truth / label / f"{image.stem}_mask.png"
            if mask is not None:
                _validate_mask(source_root, image, mask)
            pairs.append((image, mask))
            source_records.append(_record(source_root, image, split="source", label=label, mask=mask))
            if mask is not None:
                source_records.append(_record(source_root, mask, split="source_mask", label=label, mask=None))
        if label != "good":
            _reject_orphan_masks(source_root, label, files, ground_truth / label)
        dev_pairs, test_pairs = _split_pairs(pairs, seed=split_seed, label=label)
        b_dev_records.extend(_record(source_root, image, split="b_dev", label=label, mask=mask) for image, mask in dev_pairs)
        b_test_records.extend(_record(source_root, image, split="b_test", label=label, mask=mask) for image, mask in test_pairs)

    inventory = _manifest("source_inventory", source_records)
    train = _manifest("train_good", train_records)
    b_dev = _manifest("b_dev", b_dev_records)
    b_test = _manifest("b_test", b_test_records)
    _assert_disjoint(b_dev, b_test)
    return inventory, train, b_dev, b_test


def _directories(path: Path) -> list[str]:
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"required directory missing or symlinked: {path.name}")
    names: list[str] = []
    for child in sorted(path.iterdir()):
        if child.is_symlink() or not child.is_dir():
            raise ValueError(f"unexpected entry in {path.name}: {child.name}")
        names.append(child.name)
    return names


def _files(path: Path, *, suffix: str) -> list[Path]:
    if path.is_symlink() or not path.is_dir():
        raise ValueError(f"required directory missing or symlinked: {path}")
    result: list[Path] = []
    for child in sorted(path.iterdir()):
        if child.is_symlink() or not child.is_file() or child.suffix != suffix:
            raise ValueError(f"unexpected file in {path.name}: {child.name}")
        _verify_image(child)
        result.append(child)
    return result


def _verify_image(path: Path) -> None:
    if path.stat().st_size == 0:
        raise ValueError(f"zero-byte image: {path.name}")
    try:
        payload = path.read_bytes()
        if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("PNG signature missing")
        offset = 8
        idat: list[bytes] = []
        saw_ihdr = False
        saw_iend = False
        while offset < len(payload):
            if offset + 12 > len(payload):
                raise ValueError("truncated PNG chunk")
            length = struct.unpack(">I", payload[offset:offset + 4])[0]
            chunk_type = payload[offset + 4:offset + 8]
            end = offset + 12 + length
            if end > len(payload):
                raise ValueError("truncated PNG payload")
            data = payload[offset + 8:offset + 8 + length]
            expected_crc = struct.unpack(">I", payload[offset + 8 + length:end])[0]
            if zlib.crc32(chunk_type + data) & 0xFFFFFFFF != expected_crc:
                raise ValueError("PNG CRC mismatch")
            if chunk_type == b"IHDR":
                if saw_ihdr or length != 13 or data[:8] == b"\x00" * 8:
                    raise ValueError("invalid PNG IHDR")
                saw_ihdr = True
            elif chunk_type == b"IDAT":
                idat.append(data)
            elif chunk_type == b"IEND":
                if length != 0 or end != len(payload):
                    raise ValueError("invalid PNG IEND")
                saw_iend = True
                break
            offset = end
        if not saw_ihdr or not saw_iend or not idat:
            raise ValueError("incomplete PNG")
        zlib.decompress(b"".join(idat))
    except Exception as exc:
        raise ValueError(f"unreadable image: {path.name}") from exc


def _validate_mask(source_root: Path, image: Path, mask: Path) -> None:
    if mask.is_symlink() or not mask.is_file():
        raise ValueError(f"missing mask for {image.name}")
    _inside(source_root, mask)
    _verify_image(mask)


def _reject_orphan_masks(source_root: Path, label: str, images: list[Path], mask_dir: Path) -> None:
    masks = _files(mask_dir, suffix=".png")
    expected = {f"{image.stem}_mask.png" for image in images}
    observed = {mask.name for mask in masks}
    if observed != expected:
        raise ValueError(f"orphan or missing masks for {label}")
    for mask in masks:
        _inside(source_root, mask)


def _split_pairs(pairs: list[tuple[Path, Path | None]], *, seed: int, label: str) -> tuple[list[tuple[Path, Path | None]], list[tuple[Path, Path | None]]]:
    if len(pairs) < 2:
        raise ValueError(f"{label} needs at least two samples for B_dev/B_test")
    ordered = sorted(pairs, key=lambda item: item[0].name)
    salt = int.from_bytes(hashlib.sha256(f"{seed}:{label}".encode()).digest()[:8], "big")
    random.Random(salt).shuffle(ordered)
    dev_count = min(len(ordered) - 1, max(1, len(ordered) // 2))
    return sorted(ordered[:dev_count]), sorted(ordered[dev_count:])


def _record(source_root: Path, path: Path, *, split: str, label: str, mask: Path | None) -> dict:
    _inside(source_root, path)
    return {
        "relative_path": path.relative_to(source_root).as_posix(),
        "split": split,
        "label": label,
        "is_anomaly": label != "good",
        "mask_relative_path": mask.relative_to(source_root).as_posix() if mask else None,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }


def _inside(root: Path, path: Path) -> None:
    try:
        path.resolve(strict=True).relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes licensed source root: {path}") from exc


def _manifest(name: str, records: list[dict]) -> dict:
    ordered = sorted(records, key=lambda item: item["relative_path"])
    payload = {"schema_version": 1, "manifest": name, "records": ordered}
    return {**payload, "manifest_sha256": canonical_sha256(payload)}


def _assert_disjoint(b_dev: dict, b_test: dict) -> None:
    dev = {record["relative_path"] for record in b_dev["records"]}
    test = {record["relative_path"] for record in b_test["records"]}
    dev_masks = {record["mask_relative_path"] for record in b_dev["records"] if record["mask_relative_path"]}
    test_masks = {record["mask_relative_path"] for record in b_test["records"] if record["mask_relative_path"]}
    if dev.intersection(test) or dev_masks.intersection(test_masks):
        raise ValueError("B_dev and B_test must not overlap")


def _split_summary(inventory: dict, train: dict, b_dev: dict, b_test: dict) -> dict:
    payload = {
        "schema_version": 1,
        "source_inventory_sha256": inventory["manifest_sha256"],
        "train_manifest_sha256": train["manifest_sha256"],
        "b_dev_manifest_sha256": b_dev["manifest_sha256"],
        "b_test_manifest_sha256": b_test["manifest_sha256"],
        "source_record_count": len(inventory["records"]),
        "train_good_count": len(train["records"]),
        "b_dev_count": len(b_dev["records"]),
        "b_test_count": len(b_test["records"]),
    }
    return {**payload, "summary_sha256": canonical_sha256(payload)}


def _projection_contract(payloads: dict[str, dict]) -> dict:
    payload = {
        "schema_version": 1,
        "train_manifest_sha256": payloads["train_good_manifest.json"]["manifest_sha256"],
        "b_dev_manifest_sha256": payloads["b_dev_manifest.json"]["manifest_sha256"],
        "b_test_manifest_sha256": payloads["b_test_manifest.json"]["manifest_sha256"],
        "link_strategy": "symlink",
    }
    return {**payload, "contract_sha256": canonical_sha256(payload)}


def _prepared_from_payloads(artifact_dir: Path, data_dir: Path, payloads: dict[str, dict]) -> Prepared07HData:
    return Prepared07HData(
        artifact_dir=artifact_dir,
        data_dir=data_dir,
        source_inventory_sha256=payloads["source_inventory.json"]["manifest_sha256"],
        train_manifest_sha256=payloads["train_good_manifest.json"]["manifest_sha256"],
        b_dev_manifest_sha256=payloads["b_dev_manifest.json"]["manifest_sha256"],
        b_test_manifest_sha256=payloads["b_test_manifest.json"]["manifest_sha256"],
    )


def _build_projection(data_dir: Path, source_root: Path, train: dict, b_dev: dict, b_test: dict) -> None:
    for record in train["records"]:
        _link(source_root / record["relative_path"], data_dir / "shared_train" / record["relative_path"])
    for name, manifest in (("b_dev", b_dev), ("b_test", b_test)):
        train_link = data_dir / name / "bottle" / "train" / "good"
        train_link.parent.mkdir(parents=True, exist_ok=True)
        train_target = data_dir / "shared_train" / "bottle" / "train" / "good"
        os.symlink(os.path.relpath(train_target, start=train_link.parent), train_link)
        for record in manifest["records"]:
            _link(source_root / record["relative_path"], data_dir / name / record["relative_path"])
            if record["mask_relative_path"]:
                _link(source_root / record["mask_relative_path"], data_dir / name / record["mask_relative_path"])


def _link(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(os.path.relpath(source, start=destination.parent), destination)


def _validate_existing(artifact_dir: Path, data_dir: Path, payloads: dict[str, dict], contract: dict) -> None:
    if not artifact_dir.is_dir() or not data_dir.is_dir():
        raise ValueError("existing 07H data artifacts and projection must both be directories")
    for name in _DATASET_FILES:
        path = artifact_dir / name
        if not path.is_file() or _read_json(path) != payloads[name]:
            raise ValueError(f"existing 07H manifest differs: {name}")
    marker = data_dir / _PROJECTION_CONTRACT
    if not marker.is_file() or _read_json(marker) != contract:
        raise ValueError("existing 07H projection contract differs")
    expected = _expected_projection_paths(payloads)
    observed = {path.relative_to(data_dir).as_posix() for path in data_dir.rglob("*") if path.is_symlink() or path.is_file()}
    if observed != expected:
        raise ValueError("projection contains missing or unregistered files")


def _expected_projection_paths(payloads: dict[str, dict]) -> set[str]:
    expected = {_PROJECTION_CONTRACT}
    for record in payloads["train_good_manifest.json"]["records"]:
        expected.add(f"shared_train/{record['relative_path']}")
    for name in ("b_dev", "b_test"):
        expected.add(f"{name}/bottle/train/good")
        for record in payloads[f"{name}_manifest.json"]["records"]:
            expected.add(f"{name}/{record['relative_path']}")
            if record["mask_relative_path"]:
                expected.add(f"{name}/{record['mask_relative_path']}")
    return expected


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path.name}")
    return value


def _remove_if_exists(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)
