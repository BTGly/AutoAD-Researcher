"""Locked MinerU parser provider.

Provides version-locked, profile-locked PDF parsing. Supports both real
MinerU backend and fixture-based parse simulation for offline testing.
"""

import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from autoad_researcher.paper_intelligence.errors import PaperParseError
from autoad_researcher.paper_intelligence.parser_models import (
    DocumentParseRequest,
    DocumentParseResult,
    ParserManifest,
    ParseQualityReport,
)


class MinerUProvider(Protocol):
    """Interface for a locked MinerU parser provider.

    All implementations must be version-locked and profile-locked
    to ensure reproducible evidence.
    """

    def parse(self, request: DocumentParseRequest, output_dir: Path) -> DocumentParseResult:
        """Parse a PDF document with a locked MinerU profile.

        Writes canonical output artifacts to output_dir.
        """
        ...

    def get_manifest(self, result: DocumentParseResult) -> ParserManifest:
        """Generate the locked parser manifest for a parse attempt."""
        ...

    def get_quality_report(self, result: DocumentParseResult) -> ParseQualityReport:
        """Generate a parse quality report."""
        ...

    @property
    def parser_version(self) -> str:
        """Locked MinerU package version."""
        ...

    @property
    def parser_profile_id(self) -> str:
        """Active parse profile identifier."""
        ...


@dataclass
class MinerUProfileConfig:
    """Locked MinerU profile configuration.

    Every field must be pinned to ensure reproducible evidence.
    """

    profile_id: str
    parser_version: str
    parser_backend: str  # pipeline, hybrid, vlm
    model_revision: str | None
    model_weight_sha256: str | None
    ocr_engine: str | None
    ocr_language_hints: list[str]

    def compute_profile_sha256(self) -> str:
        """Compute a stable SHA256 of the profile configuration."""
        parts = [
            self.profile_id,
            self.parser_version,
            self.parser_backend,
            self.model_revision or "",
            self.model_weight_sha256 or "",
            self.ocr_engine or "",
            ",".join(sorted(self.ocr_language_hints)),
        ]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _compute_file_sha256(path: Path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            sha.update(chunk)
    return sha.hexdigest()


def _deterministic_attempt_id(source_id: str, pdf_sha256: str, profile_sha256: str) -> str:
    """Generate a deterministic parse_attempt_id from source+PDF+profile."""
    h = hashlib.sha256(f"{source_id}|{pdf_sha256}|{profile_sha256}".encode())
    return f"pa_{h.hexdigest()[:12]}"


class FixtureMinerUProvider:
    """Deterministic test-fixture MinerU provider.

    Writes canonical output files (pages.jsonl, blocks.jsonl, sections.json, etc.)
    to the output directory with content derived from the actual PDF.
    Computes real hashes from the written artifacts.
    """

    def __init__(
        self,
        profile: MinerUProfileConfig,
        runtime_python_version: str,
        runtime_platform: str,
        device_profile: str,
    ):
        self._profile = profile
        self._runtime_python_version = runtime_python_version
        self._runtime_platform = runtime_platform
        self._device_profile = device_profile
        self._last_source_sha256 = ""
        self._last_canonical_sha256 = ""
        self._last_page_count = 0

    @property
    def parser_version(self) -> str:
        return self._profile.parser_version

    @property
    def parser_profile_id(self) -> str:
        return self._profile.profile_id

    def parse(self, request: DocumentParseRequest, output_dir: Path) -> DocumentParseResult:
        """Parse a PDF and write canonical output to output_dir.

        The fixture provider:
        1. Reads the real PDF bytes for hashing
        2. Produces pages.jsonl with actual text extraction from PDF
        3. Writes all canonical artifacts
        4. Computes deterministic parse_attempt_id
        5. Uses deterministic canonical_output_sha256
        """
        source_path = Path(request.source_pdf_path)
        if not source_path.exists():
            return DocumentParseResult(
                schema_version=1,
                parse_attempt_id="pa_000000000000",
                source_id=request.source_id,
                parser_manifest_path="",
                canonical_output_path="",
                parse_quality_report_path="",
                status="failed",
                warnings=["source PDF not found"],
            )

        if not source_path.suffix.lower() == ".pdf":
            return DocumentParseResult(
                schema_version=1,
                parse_attempt_id="pa_000000000000",
                source_id=request.source_id,
                parser_manifest_path="",
                canonical_output_path="",
                parse_quality_report_path="",
                status="failed",
                warnings=["source file is not a PDF"],
            )

        pdf_bytes = source_path.read_bytes()
        pdf_sha256 = hashlib.sha256(pdf_bytes).hexdigest()
        self._last_source_sha256 = pdf_sha256

        profile_sha256 = self._profile.compute_profile_sha256()
        attempt_id = _deterministic_attempt_id(request.source_id, pdf_sha256, profile_sha256)

        output_dir.mkdir(parents=True, exist_ok=True)

        # Extract text from PDF bytes (simple approach: decode text between stream/endstream)
        pages = _extract_pages_from_pdf_bytes(pdf_bytes)
        self._last_page_count = len(pages)

        # Write canonical output artifacts
        _write_atomic_jsonl(output_dir / "pages.jsonl", pages)

        # Build sections from page content
        sections = _build_sections(pages)
        _write_atomic_json(output_dir / "sections.json", sections)

        # Build blocks from page content
        blocks = _build_blocks(pages)
        _write_atomic_jsonl(output_dir / "blocks.jsonl", blocks)

        # Empty placeholder artifacts (real MinerU would fill these)
        _write_atomic_json(output_dir / "figures.json", [])
        _write_atomic_json(output_dir / "tables.json", [])
        _write_atomic_json(output_dir / "references.json", [])

        # Compute canonical output SHA from all written files
        canonical_sha256 = _compute_canonical_sha256(output_dir)
        self._last_canonical_sha256 = canonical_sha256

        # Write parse_quality_report
        quality = ParseQualityReport(
            schema_version=1,
            status="success" if pages else "partial_success",
            page_count=len(pages),
        )
        _write_atomic_json(output_dir / "parse_quality_report.json", quality.model_dump())

        # Write parser manifest
        manifest_data = {
            "schema_version": 1,
            "parser_name": "MinerU",
            "parser_version": self._profile.parser_version,
            "parser_backend": self._profile.parser_backend,
            "parser_profile_id": self._profile.profile_id,
            "parser_profile_sha256": profile_sha256,
            "source_pdf_sha256": pdf_sha256,
            "canonical_output_sha256": canonical_sha256,
        }
        _write_atomic_json(output_dir / "parser_manifest.json", manifest_data)
        _write_atomic(output_dir / "canonical_output.sha256", canonical_sha256 + "\n")

        return DocumentParseResult(
            schema_version=1,
            parse_attempt_id=attempt_id,
            source_id=request.source_id,
            parser_manifest_path=str(output_dir / "parser_manifest.json"),
            canonical_output_path=str(output_dir),
            parse_quality_report_path=str(output_dir / "parse_quality_report.json"),
            status="success",
            warnings=[],
        )

    def get_manifest(self, result: DocumentParseResult) -> ParserManifest:
        """Generate a locked parser manifest from actual parse data."""
        return ParserManifest(
            schema_version=1,
            parser_name="MinerU",
            parser_version=self._profile.parser_version,
            parser_backend=self._profile.parser_backend,
            parser_profile_id=self._profile.profile_id,
            parser_profile_sha256=self._profile.compute_profile_sha256(),
            model_revision=self._profile.model_revision,
            model_weight_sha256=self._profile.model_weight_sha256,
            ocr_engine=self._profile.ocr_engine,
            ocr_language_hints=list(self._profile.ocr_language_hints),
            runtime_python_version=self._runtime_python_version,
            runtime_platform=self._runtime_platform,
            device_profile=self._device_profile,
            source_pdf_sha256=self._last_source_sha256,
            canonical_output_sha256=self._last_canonical_sha256,
        )

    def get_quality_report(self, result: DocumentParseResult) -> ParseQualityReport:
        """Generate a parse quality report from actual parse data."""
        if result.status == "failed":
            return ParseQualityReport(
                schema_version=1,
                status="failed",
                page_count=0,
                fatal_errors=result.warnings,
            )
        return ParseQualityReport(
            schema_version=1,
            status="success",
            page_count=self._last_page_count,
        )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _extract_pages_from_pdf_bytes(pdf_bytes: bytes) -> list[dict]:
    """Extract page text from PDF bytes.

    Looks for text between BT/ET markers inside stream objects.
    Falls back to extracting readable ASCII if no text operators found.
    """
    text = ""
    try:
        text = pdf_bytes.decode("utf-8", errors="replace")
    except Exception:
        text = pdf_bytes.decode("latin-1", errors="replace")

    pages: list[dict] = []
    page_num = 0

    # Find stream...endstream blocks
    import re
    stream_pattern = re.compile(rb'stream\s*\n(.*?)endstream', re.DOTALL)
    page_pattern = re.compile(rb'/Type\s*/Page[^s]')

    # First find page boundaries
    stream_matches = list(stream_pattern.finditer(pdf_bytes))

    for sm in stream_matches:
        content = sm.group(1)
        try:
            content_text = content.decode("utf-8", errors="replace")
        except Exception:
            content_text = content.decode("latin-1", errors="replace")

        # Extract text inside BT...ET blocks (PDF text operators)
        bt_blocks = re.findall(r'BT\s*(.*?)\s*ET', content_text, re.DOTALL)
        page_text_parts = []
        for bt in bt_blocks:
            # Extract Tj, TJ, ' operators
            text_ops = re.findall(r'\(([^)]*)\)\s*Tj', bt)
            for t in text_ops:
                page_text_parts.append(t)

        if page_text_parts:
            page_text = " ".join(page_text_parts)
        else:
            # Fallback: extract readable ASCII
            page_text = "".join(c if 32 <= ord(c) < 127 or c in "\n\r\t" else " " for c in content_text)[:2000]

        if page_text.strip():
            pages.append({
                "physical_page_index": page_num,
                "text": page_text.strip(),
                "block_ids": [f"b_{page_num}_0"],
            })
            page_num += 1

    # If no pages found, create one page from full text
    if not pages:
        readable = "".join(c if 32 <= ord(c) < 127 or c in "\n\r\t" else " " for c in text)[:5000]
        if readable.strip():
            pages.append({
                "physical_page_index": 0,
                "text": readable.strip(),
                "block_ids": ["b_0_0"],
            })

    return pages


def _build_sections(pages: list[dict]) -> list[dict]:
    sections = []
    for page in pages:
        text = page.get("text", "")
        title = "Section"
        for line in text.split("\n"):
            line = line.strip()
            if len(line) > 5 and len(line) < 100:
                title = line[:80]
                break
        sections.append({
            "section_id": f"s_{page['physical_page_index']}",
            "title": title,
            "level": 1,
            "physical_page_start": page["physical_page_index"],
            "physical_page_end": page["physical_page_index"],
            "block_ids": page.get("block_ids", []),
        })
    return sections


def _build_blocks(pages: list[dict]) -> list[dict]:
    blocks = []
    for page in pages:
        for bid in page.get("block_ids", []):
            blocks.append({
                "block_id": bid,
                "text": page.get("text", ""),
                "physical_page_index": page["physical_page_index"],
            })
    return blocks


def _compute_canonical_sha256(output_dir: Path) -> str:
    """Compute SHA256 of all canonical output files (sorted by name, then content)."""
    sha = hashlib.sha256()
    for fp in sorted(output_dir.iterdir()):
        if fp.is_file() and fp.name != "parser_manifest.json":
            sha.update(fp.name.encode())
            sha.update(fp.read_bytes())
    return sha.hexdigest()


def _write_atomic(path: Path, content: str) -> None:
    """Write a file atomically via a temp file + os.replace."""
    import os
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.chmod(0o644)
    os.replace(tmp, path)


def _write_atomic_json(path: Path, data: object) -> None:
    _write_atomic(path, json.dumps(data, indent=2, ensure_ascii=False))


def _write_atomic_jsonl(path: Path, items: list[dict]) -> None:
    import os
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    tmp.chmod(0o644)
    os.replace(tmp, path)


MINERU_PIPELINE_V1_PROFILE = MinerUProfileConfig(
    profile_id="mineru_pipeline_v1",
    parser_version="0.10.0",
    parser_backend="pipeline",
    model_revision=None,
    model_weight_sha256=None,
    ocr_engine=None,
    ocr_language_hints=["en"],
)
