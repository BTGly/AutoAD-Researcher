"""Local PDF text extraction fallback using system tools."""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoad_researcher.paper_intelligence.markdown import looks_garbled_text


@dataclass(frozen=True)
class PdfTextAdapterResult:
    ok: bool
    parser_name: str = "pdftotext"
    output_paths: list[str] = field(default_factory=list)
    error: str | None = None
    duration_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


def convert_pdf_to_markdown(
    input_path: Path,
    output_path: Path,
    *,
    run_dir: Path | None = None,
    timeout_s: int = 120,
) -> PdfTextAdapterResult:
    """Extract readable text with `pdftotext` and write a markdown artifact."""
    started = time.monotonic()
    input_path = Path(input_path)
    output_path = Path(output_path)
    binary = shutil.which("pdftotext")
    if not binary:
        return PdfTextAdapterResult(
            ok=False,
            error="pdftotext unavailable on PATH",
            duration_seconds=time.monotonic() - started,
        )
    if not input_path.is_file():
        return PdfTextAdapterResult(
            ok=False,
            error=f"input file not found: {input_path}",
            duration_seconds=time.monotonic() - started,
        )

    txt_path = output_path.with_suffix(".txt")
    try:
        txt_path.parent.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            [binary, "-layout", "-enc", "UTF-8", str(input_path), str(txt_path)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except Exception as exc:
        return PdfTextAdapterResult(
            ok=False,
            error=f"pdftotext execution failed: {exc}",
            duration_seconds=time.monotonic() - started,
        )
    if result.returncode != 0:
        return PdfTextAdapterResult(
            ok=False,
            error=(result.stderr or result.stdout or f"pdftotext exited {result.returncode}")[:1000],
            duration_seconds=time.monotonic() - started,
        )

    try:
        raw = txt_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return PdfTextAdapterResult(
            ok=False,
            error=f"pdftotext output unreadable: {exc}",
            duration_seconds=time.monotonic() - started,
        )

    markdown = _text_to_markdown(raw)
    if not markdown:
        return PdfTextAdapterResult(
            ok=False,
            error="pdftotext produced no readable text",
            duration_seconds=time.monotonic() - started,
        )

    output_path.write_text(markdown, encoding="utf-8")
    rel = _relative_path(output_path, run_dir)
    return PdfTextAdapterResult(
        ok=True,
        output_paths=[rel],
        duration_seconds=time.monotonic() - started,
        metadata={
            "input_path": _relative_path(input_path, run_dir),
            "text_chars": len(markdown),
            "tool": binary,
        },
    )


def _text_to_markdown(raw: str) -> str:
    pages = re.split(r"\f+", raw)
    parts: list[str] = []
    for idx, page in enumerate(pages, start=1):
        lines = [_clean_line(line) for line in page.splitlines()]
        text = "\n".join(line for line in lines if line).strip()
        if not text or looks_garbled_text(text[:2000]):
            continue
        parts.append(f"## Page {idx}\n\n{text}")
    return "\n\n".join(parts).strip() + ("\n" if parts else "")


def _clean_line(line: str) -> str:
    return re.sub(r"[ \t]+", " ", line).strip()


def _relative_path(path: Path, run_dir: Path | None) -> str:
    if run_dir is None:
        return str(path)
    try:
        return path.relative_to(run_dir).as_posix()
    except ValueError:
        return str(path)
