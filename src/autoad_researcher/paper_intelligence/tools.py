"""Paper tools for reading parsed paper artifacts.

These tools allow agents to navigate and read parsed paper content.
Every read/read_blocks/search call produces a PaperTextEvidenceRef with
content SHA256 and a deterministic evidence_id.
"""

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoad_researcher.paper_intelligence.evidence_models import EvidenceIndexRecord, PaperTextEvidenceRef


@dataclass
class SectionInfo:
    """One section in the paper's section tree."""

    section_id: str
    title: str
    level: int
    physical_page_start: int
    physical_page_end: int
    block_ids: list[str] = field(default_factory=list)


@dataclass
class PaperReadResult:
    """Result of reading a paper region, including evidence."""

    content: str
    evidence: PaperTextEvidenceRef


@dataclass
class PaperSearchMatch:
    """One search result in the paper, including evidence."""

    evidence: PaperTextEvidenceRef
    snippet: str
    section_path: list[str] = field(default_factory=list)
    physical_page_index: int | None = None
    block_id: str | None = None


class PaperToolError(Exception):
    """Raised when a paper tool operation fails."""


class EvidenceWriter:
    """Append-only evidence index writer.

    P1 limitation: evidence_id uses a counter scoped to the current
    CanonicalPaperStore instance. Re-running the same run_id without
    clearing the evidence directory will produce duplicate evidence_ids.
    This is deferred to formal resume/replay support.
    """

    def __init__(self, evidence_dir: Path):
        self.evidence_dir = Path(evidence_dir)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.evidence_dir / "evidence_index.jsonl"

    def append(self, evidence: PaperTextEvidenceRef) -> None:
        """Append a single evidence ref to the evidence index."""
        record = EvidenceIndexRecord(
            schema_version=1,
            parse_attempt_id=evidence.parse_attempt_id,
            evidence=evidence,
        ).model_dump()
        with open(self._index_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    @property
    def path(self) -> Path:
        return self._index_path


class CanonicalPaperStore:
    """Read-only accessor for parsed paper artifacts.

    When source identity fields are set, read_blocks and search produce
    PaperTextEvidenceRef with real content SHA256 and evidence_ids.
    """

    def __init__(self, parse_dir: Path):
        self.parse_dir = Path(parse_dir)
        self._sections: list[SectionInfo] = []
        self._loaded = False

        # Source identity (set after parse for evidence generation)
        self.source_id: str = ""
        self.source_pdf_sha256: str = ""
        self.parse_attempt_id: str = ""
        self.parser_profile_sha256: str = ""
        self.canonical_output_sha256: str = ""

        # Evidence writer
        self._evidence_writer: EvidenceWriter | None = None
        self._evidence_counter: int = 0

    def set_source_identity(
        self,
        source_id: str,
        source_pdf_sha256: str,
        parse_attempt_id: str,
        parser_profile_sha256: str,
        canonical_output_sha256: str,
    ) -> None:
        self.source_id = source_id
        self.source_pdf_sha256 = source_pdf_sha256
        self.parse_attempt_id = parse_attempt_id
        self.parser_profile_sha256 = parser_profile_sha256
        self.canonical_output_sha256 = canonical_output_sha256

    def set_evidence_writer(self, writer: EvidenceWriter) -> None:
        self._evidence_writer = writer

    def _next_evidence_id(self) -> str:
        self._evidence_counter += 1
        return f"ev_{self.source_id}_{self._evidence_counter:03d}"

    def _make_evidence(
        self,
        content: str,
        physical_page_index: int,
        block_id: str | None = None,
        tool_call_id: str = "",
    ) -> PaperTextEvidenceRef:
        content_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()
        ev = PaperTextEvidenceRef(
            source_kind="paper_text",
            evidence_id=self._next_evidence_id(),
            source_id=self.source_id,
            source_pdf_sha256=self.source_pdf_sha256,
            parse_attempt_id=self.parse_attempt_id,
            parser_profile_sha256=self.parser_profile_sha256,
            canonical_output_sha256=self.canonical_output_sha256,
            physical_page_index=physical_page_index,
            block_id=block_id or f"b_search_{self._evidence_counter}",
            content_sha256=content_sha,
            tool_call_id=tool_call_id or f"tc_{self._evidence_counter:03d}",
            trust_level="paper_body_fact",
        )
        if self._evidence_writer:
            self._evidence_writer.append(ev)
        return ev

    def load(self) -> None:
        """Load section index from parsed output."""
        sections_path = self.parse_dir / "sections.json"
        if not sections_path.exists():
            self._loaded = True
            return

        raw = json.loads(sections_path.read_text(encoding="utf-8"))
        for entry in raw:
            self._sections.append(SectionInfo(**entry))
        self._loaded = True

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def list_sections(self) -> list[SectionInfo]:
        """Return the section tree of the parsed paper."""
        self._ensure_loaded()
        return list(self._sections)

    def read_blocks(self, block_ids: list[str]) -> list[PaperReadResult]:
        """Read text content for specific block IDs and generate evidence."""
        self._ensure_loaded()
        blocks_path = self.parse_dir / "blocks.jsonl"
        if not blocks_path.exists():
            raise PaperToolError(f"blocks.jsonl not found in {self.parse_dir}")

        results: list[PaperReadResult] = []
        target_ids = set(block_ids)
        with open(blocks_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                block = json.loads(line)
                bid = block.get("block_id", "")
                if bid in target_ids:
                    content = block.get("text", "")
                    page_idx = block.get("physical_page_index", 0)
                    evidence = self._make_evidence(content, page_idx, block_id=bid)
                    results.append(PaperReadResult(
                        content=content,
                        evidence=evidence,
                    ))
                    target_ids.discard(bid)
                    if not target_ids:
                        break
        return results

    def search(self, query: str, max_results: int = 50) -> list[PaperSearchMatch]:
        """Search for a literal substring in parsed pages and generate evidence."""
        self._ensure_loaded()
        pages_path = self.parse_dir / "pages.jsonl"
        if not pages_path.exists():
            raise PaperToolError(f"pages.jsonl not found in {self.parse_dir}")

        results: list[PaperSearchMatch] = []
        with open(pages_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                page = json.loads(line)
                text = page.get("text", "")
                idx = text.lower().find(query.lower())
                if idx >= 0:
                    start = max(0, idx - 40)
                    end = min(len(text), idx + len(query) + 120)
                    snippet = text[start:end]
                    page_idx = page.get("physical_page_index", 0)
                    evidence = self._make_evidence(snippet, page_idx)
                    results.append(PaperSearchMatch(
                        evidence=evidence,
                        snippet=snippet,
                        physical_page_index=page_idx,
                        block_id=page.get("block_id"),
                        section_path=page.get("section_path", []),
                    ))
                if len(results) >= max_results:
                    break
        return results
