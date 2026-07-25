#!/usr/bin/env python3
"""Record and validate a local full-pytest verification for one worktree state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


STATE_VERSION = 1
PYTEST_COMMAND = "uv run --extra dev pytest -q --durations=20"
STATE_FILENAME = "autoad-verify-pytest.json"


def _git_output(root: Path, *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return result.stdout


def repository_root() -> Path:
    root = _git_output(Path.cwd(), "rev-parse", "--show-toplevel")
    return Path(os.fsdecode(root.rstrip(b"\n")))


def default_state_path(root: Path) -> Path:
    raw_path = os.fsdecode(
        _git_output(root, "rev-parse", "--git-path", STATE_FILENAME).rstrip(b"\n")
    )
    path = Path(raw_path)
    return path if path.is_absolute() else root / path


def worktree_fingerprint(root: Path) -> str:
    """Hash every tracked and non-ignored worktree file, including its mode."""
    raw_paths = _git_output(
        root,
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "-z",
    )
    digest = hashlib.sha256()
    digest.update(b"autoad-pytest-worktree-v1\0")

    for raw_path in sorted(path for path in raw_paths.split(b"\0") if path):
        relative = Path(os.fsdecode(raw_path))
        path = root / relative
        stat = path.lstat()

        digest.update(raw_path)
        digest.update(b"\0")
        digest.update(oct(stat.st_mode & 0o7777).encode("ascii"))
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(os.fsencode(os.readlink(path)))
        elif path.is_file():
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
        else:
            raise ValueError(f"unsupported worktree entry: {relative}")
        digest.update(b"\0")

    return digest.hexdigest()


def expected_state(root: Path) -> dict[str, Any]:
    return {
        "state_version": STATE_VERSION,
        "worktree_fingerprint": worktree_fingerprint(root),
        "pytest_command": PYTEST_COMMAND,
        "python_version": sys.version,
    }


def record_state(root: Path, state_path: Path) -> None:
    state = expected_state(root)
    state["verified_at"] = datetime.now(UTC).isoformat()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=state_path.parent,
        delete=False,
    ) as handle:
        json.dump(state, handle, sort_keys=True)
        handle.write("\n")
        temporary_path = Path(handle.name)
    temporary_path.replace(state_path)


def state_matches(root: Path, state_path: Path) -> tuple[bool, str]:
    if not state_path.is_file():
        return False, "no prior full-pytest verification"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"invalid prior verification state: {exc}"

    expected = expected_state(root)
    for key, value in expected.items():
        if state.get(key) != value:
            return False, f"{key} changed since the prior full pytest"
    return True, "worktree matches the prior full-pytest verification"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("fingerprint", "record", "matches"))
    parser.add_argument("--state-path", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = repository_root()
    state_path = args.state_path or default_state_path(root)

    if args.action == "fingerprint":
        print(worktree_fingerprint(root))
        return 0
    if args.action == "record":
        record_state(root, state_path)
        print(f"[verify] recorded full-pytest verification at {state_path}")
        return 0

    matches, reason = state_matches(root, state_path)
    print(f"[verify] pytest verification cache: {reason}")
    return 0 if matches else 1


if __name__ == "__main__":
    raise SystemExit(main())
