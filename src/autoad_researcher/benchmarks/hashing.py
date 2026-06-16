"""Deterministic hashing for benchmark evidence."""

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from pydantic import BaseModel


def sha256_file(path: Path) -> str:
    """Streaming SHA-256 of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


def canonical_json_bytes(value: BaseModel | Mapping[str, Any]) -> bytes:
    """Serialize to canonical JSON bytes (sorted keys, no nan, compact)."""
    if isinstance(value, BaseModel):
        payload = value.model_dump(mode="json", exclude_none=True)
    else:
        payload = dict(value)
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: BaseModel | Mapping[str, Any]) -> str:
    """Canonical SHA-256 of a model or dict."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
