"""Optional MarkItDown adapter for text-first source conversion."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from html import unescape
from pathlib import Path
from typing import Any

_BUILTIN_TEXT_SUFFIXES = {
    ".csv",
    ".htm",
    ".html",
    ".json",
    ".md",
    ".markdown",
    ".txt",
    ".xml",
}


@dataclass(frozen=True)
class MarkItDownAdapterResult:
    ok: bool
    parser_name: str = "markitdown"
    output_paths: list[str] = field(default_factory=list)
    error: str | None = None
    duration_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


def convert_local_to_markdown(
    input_path: Path,
    output_path: Path,
    *,
    run_dir: Path | None = None,
) -> MarkItDownAdapterResult:
    """Convert a local file to Markdown when MarkItDown is installed."""
    started = time.monotonic()
    input_path = Path(input_path)
    output_path = Path(output_path)
    try:
        from markitdown import MarkItDown  # type: ignore
    except Exception as exc:
        fallback = _builtin_text_conversion(input_path, output_path, run_dir=run_dir)
        if fallback.ok:
            return fallback
        return MarkItDownAdapterResult(
            ok=False,
            error=f"markitdown unavailable: {exc}; builtin fallback failed: {fallback.error}",
            duration_seconds=time.monotonic() - started,
        )

    if not input_path.is_file():
        return MarkItDownAdapterResult(
            ok=False,
            error=f"input file not found: {input_path}",
            duration_seconds=time.monotonic() - started,
        )

    try:
        result = MarkItDown().convert_local(str(input_path))
        text = str(getattr(result, "text_content", "") or "").strip()
        if not text:
            return MarkItDownAdapterResult(
                ok=False,
                error="markitdown produced no text_content",
                duration_seconds=time.monotonic() - started,
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(text + "\n", encoding="utf-8")
        rel = _relative_path(output_path, run_dir)
        return MarkItDownAdapterResult(
            ok=True,
            output_paths=[rel],
            duration_seconds=time.monotonic() - started,
            metadata={"input_path": _relative_path(input_path, run_dir), "text_chars": len(text)},
        )
    except Exception as exc:
        fallback = _builtin_text_conversion(input_path, output_path, run_dir=run_dir)
        if fallback.ok:
            return fallback
        return MarkItDownAdapterResult(
            ok=False,
            error=f"markitdown conversion failed: {exc}; builtin fallback failed: {fallback.error}",
            duration_seconds=time.monotonic() - started,
        )


def _builtin_text_conversion(
    input_path: Path,
    output_path: Path,
    *,
    run_dir: Path | None,
) -> MarkItDownAdapterResult:
    started = time.monotonic()
    if not input_path.is_file():
        return MarkItDownAdapterResult(ok=False, error=f"input file not found: {input_path}")
    suffix = input_path.suffix.lower()
    if suffix not in _BUILTIN_TEXT_SUFFIXES:
        return MarkItDownAdapterResult(ok=False, error=f"builtin fallback does not support {suffix or 'extensionless'} files")
    try:
        raw = input_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return MarkItDownAdapterResult(ok=False, error=f"builtin read failed: {exc}")
    if _looks_binary(raw):
        return MarkItDownAdapterResult(ok=False, error="builtin fallback refused binary-looking content")
    text = _html_to_markdown(raw) if suffix in {".html", ".htm"} else raw.strip()
    if not text.strip():
        return MarkItDownAdapterResult(ok=False, error="builtin fallback produced no text")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text.strip() + "\n", encoding="utf-8")
    rel = _relative_path(output_path, run_dir)
    return MarkItDownAdapterResult(
        ok=True,
        parser_name="builtin_text",
        output_paths=[rel],
        duration_seconds=time.monotonic() - started,
        metadata={"input_path": _relative_path(input_path, run_dir), "text_chars": len(text)},
    )


def _html_to_markdown(raw: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", raw)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|section|article|h[1-6]|li)>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = unescape(text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def _looks_binary(text: str) -> bool:
    if "\x00" in text:
        return True
    if not text:
        return False
    sample = text[:4096]
    replacement_ratio = sample.count("\ufffd") / max(len(sample), 1)
    control_count = sum(1 for char in sample if ord(char) < 32 and char not in "\n\r\t")
    return replacement_ratio > 0.02 or control_count > max(16, len(sample) // 100)


def _relative_path(path: Path, run_dir: Path | None) -> str:
    if run_dir is None:
        return str(path)
    try:
        return path.relative_to(run_dir).as_posix()
    except ValueError:
        return str(path)
