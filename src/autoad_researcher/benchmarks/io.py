"""Atomic JSON I/O for benchmark evidence."""

import json
import os
from pathlib import Path

from pydantic import BaseModel


def write_json_atomic(path: Path, model: BaseModel) -> None:
    """Write model as JSON atomically. Refuses to overwrite existing files."""
    if path.exists():
        raise FileExistsError(f"refusing to overwrite: {path}")

    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        payload = model.model_dump(mode="json", exclude_none=True)
        data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")

        with tmp.open("wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())

        os.replace(tmp, path)
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise
