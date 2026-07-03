"""Sanitised chat transcript persistence — UI audit material, not pipeline evidence."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

TRANSCRIPT_DIR = "ui_chat"
TRANSCRIPT_FILE = "chat_transcript.jsonl"

_SK_PATTERN = re.compile(r"sk-[A-Za-z0-9_\-]{8,}")


def redact_secrets(text: str) -> str:
    """Replace API-key-like strings in free-form text."""
    return _SK_PATTERN.sub("sk-***REDACTED***", text)


def save_transcript(
    run_dir: Path,
    mode: str,
    role: str,
    content: str,
    context_refs: list[str] | None = None,
) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "role": role,
        "content": redact_secrets(content),
    }
    if context_refs:
        entry["context_refs"] = context_refs

    d = run_dir / TRANSCRIPT_DIR
    d.mkdir(parents=True, exist_ok=True)
    path = d / TRANSCRIPT_FILE
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def load_transcript(run_dir: Path) -> list[dict]:
    path = run_dir / TRANSCRIPT_DIR / TRANSCRIPT_FILE
    if not path.is_file():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").strip().splitlines():
        if line.strip():
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries
