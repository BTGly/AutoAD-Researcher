"""Durable local I/O primitives used by control-plane stores."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable


def fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        fsync_directory(path.parent)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, payload: Any) -> None:
    data = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    atomic_write_bytes(path, data)


def atomic_write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    lines = [json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for row in rows]
    data = (("\n".join(lines) + "\n") if lines else "").encode("utf-8")
    atomic_write_bytes(path, data)


def write_bytes_exclusive_durable(path: Path, data: bytes) -> bool:
    """Create an immutable file, or verify an identical recovered write.

    Returns True when a new file was created and False for an identical existing
    file. Different existing bytes are an integrity error.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        fsync_directory(path.parent)
        return True
    except FileExistsError:
        if path.read_bytes() == data:
            return False
        raise ValueError(f"immutable artifact content conflict: {path}") from None


def write_json_exclusive_durable(path: Path, payload: Any) -> bool:
    data = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return write_bytes_exclusive_durable(path, data)


def append_jsonl_line_durable(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        written = os.write(fd, data)
        if written != len(data):
            raise OSError(f"short JSONL append: wrote {written} of {len(data)} bytes")
        os.fsync(fd)
    finally:
        os.close(fd)
