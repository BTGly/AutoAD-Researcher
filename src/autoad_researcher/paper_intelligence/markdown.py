"""Derived Markdown artifact for parsed paper text."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


PAPER_MARKDOWN_FILENAME = "paper.md"


def blocks_jsonl_to_paper_markdown(parse_dir: Path) -> Path | None:
    """Build paper.md from readable blocks.jsonl content.

    The source blocks stay unchanged. Garbled blocks are skipped so downstream
    assistants have a stable text artifact and do not reason from PDF binary
    fragments.
    """
    parse_dir = Path(parse_dir)
    blocks_path = parse_dir / "blocks.jsonl"
    output_path = parse_dir / PAPER_MARKDOWN_FILENAME
    markdown = build_paper_markdown_from_blocks(blocks_path)
    if not markdown:
        try:
            output_path.unlink()
        except FileNotFoundError:
            pass
        return None
    _write_atomic_text(output_path, markdown)
    return output_path


def build_paper_markdown_from_blocks(blocks_path: Path) -> str:
    if not blocks_path.is_file():
        return ""
    sections: list[str] = []
    current_page: int | None = None
    try:
        lines = blocks_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return ""
    for line in lines:
        block = _json_loads(line)
        if not isinstance(block, dict):
            continue
        text = _clean_block_text(block.get("text") or block.get("content"))
        if not text or not has_readable_paper_block_text(text):
            continue
        page_index = _page_index(block)
        if page_index != current_page:
            current_page = page_index
            sections.append(f"## Page {page_index + 1}")
        sections.append(text)
    return "\n\n".join(sections).strip() + ("\n" if sections else "")


def looks_garbled_text(text: str) -> bool:
    stripped = re.sub(r"\s+", " ", text.strip())
    if len(stripped) < 12:
        return False

    byte_data = stripped.encode("utf-8", errors="ignore")
    non_ascii_ratio = sum(1 for byte in byte_data if byte > 127) / max(len(byte_data), 1)
    cjk_count = sum(1 for ch in stripped if "\u4e00" <= ch <= "\u9fff")
    cjk_ratio = cjk_count / max(len(stripped), 1)
    control_ratio = sum(1 for ch in stripped if ord(ch) < 32 and ch not in "\n\r\t") / max(len(stripped), 1)
    replacement_ratio = stripped.count("\ufffd") / max(len(stripped), 1)
    meaningful_ratio = sum(1 for ch in stripped if ch.isalnum() or "\u4e00" <= ch <= "\u9fff") / max(len(stripped), 1)
    symbol_ratio = sum(
        1
        for ch in stripped
        if ch.isascii()
        and not ch.isalnum()
        and not ch.isspace()
        and ch not in ".,;:?!()[]{}'\"-/+%="
    ) / max(len(stripped), 1)
    word_like = re.findall(r"[A-Za-z]{3,}|[\u4e00-\u9fff]{2,}", stripped)
    tokens = re.findall(r"[A-Za-z0-9]+|[\u4e00-\u9fff]+", stripped)
    short_token_ratio = (
        sum(1 for token in tokens if len(token) <= 2 and not token.isdigit()) / len(tokens)
        if tokens
        else 1.0
    )

    return bool(
        replacement_ratio > 0.02
        or control_ratio > 0.02
        or (non_ascii_ratio > 0.50 and cjk_ratio < 0.20)
        or meaningful_ratio < 0.35
        or (symbol_ratio > 0.18 and meaningful_ratio < 0.55)
        or (len(tokens) >= 8 and short_token_ratio > 0.45)
        or (len(tokens) >= 5 and short_token_ratio > 0.60 and meaningful_ratio < 0.60)
        or (not word_like and len(stripped) >= 16)
    )


def has_readable_paper_block_text(text: str) -> bool:
    stripped = re.sub(r"\s+", " ", text.strip())
    if not stripped or looks_garbled_text(stripped):
        return False
    cjk_count = sum(1 for ch in stripped if "\u4e00" <= ch <= "\u9fff")
    word_like = re.findall(r"[A-Za-z]{3,}|[\u4e00-\u9fff]{2,}", stripped)
    if cjk_count >= 20:
        return True
    if len(stripped) >= 40 and len(word_like) >= 4:
        return True
    return False


def _json_loads(line: str) -> Any:
    if not line.strip():
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _clean_block_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"[ \t]+", " ", value).strip()


def _page_index(block: dict[str, Any]) -> int:
    value = block.get("physical_page_index", block.get("page", 0))
    if isinstance(value, int):
        return max(value, 0)
    if isinstance(value, str) and value.isdigit():
        return max(int(value), 0)
    return 0


def _write_atomic_text(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.chmod(0o644)
    os.replace(tmp, path)
