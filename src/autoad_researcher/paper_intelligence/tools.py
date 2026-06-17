"""Paper tools for reading parsed paper artifacts.

These tools allow agents to navigate and read parsed paper content:
- paper_list_sections: Return the section tree
- paper_read: Read content by section/page/block/table/figure/reference
- paper_search: Search within canonical parsed text
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


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
    """Result of reading a paper region."""

    content: str
    evidence_id: str | None = None
    source_id: str | None = None
    physical_page_index: int | None = None
    block_id: str | None = None


@dataclass
class PaperSearchMatch:
    """One search result in the paper."""

    evidence_id: str
    snippet: str
    section_path: list[str] = field(default_factory=list)
    physical_page_index: int | None = None
    block_id: str | None = None


class PaperToolError(Exception):
    """Raised when a paper tool operation fails."""


class CanonicalPaperStore:
    """Read-only accessor for parsed paper artifacts."""

    def __init__(self, parse_dir: Path):
        self.parse_dir = Path(parse_dir)
        self._sections: list[SectionInfo] = []
        self._loaded = False

    def load(self) -> None:
        """Load section index from parsed output."""
        sections_path = self.parse_dir / "sections.json"
        if not sections_path.exists():
            self._loaded = True
            return

        import json

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

    def read_blocks(self, block_ids: list[str], source_id: str = "") -> list[PaperReadResult]:
        """Read text content for specific block IDs."""
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
                import json

                block = json.loads(line)
                bid = block.get("block_id", "")
                if bid in target_ids:
                    results.append(
                        PaperReadResult(
                            content=block.get("text", ""),
                            block_id=bid,
                            source_id=source_id,
                            physical_page_index=block.get("physical_page_index"),
                        )
                    )
                    target_ids.discard(bid)
                    if not target_ids:
                        break
        return results

    def search(self, query: str, max_results: int = 50, source_id: str = "") -> list[PaperSearchMatch]:
        """Search for a literal substring in parsed pages."""
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
                import json

                page = json.loads(line)
                text = page.get("text", "")
                idx = text.lower().find(query.lower())
                if idx >= 0:
                    start = max(0, idx - 40)
                    end = min(len(text), idx + len(query) + 120)
                    snippet = text[start:end]
                    results.append(
                        PaperSearchMatch(
                            evidence_id="",
                            snippet=snippet,
                            physical_page_index=page.get("physical_page_index", 0),
                            block_id=page.get("block_id"),
                            section_path=page.get("section_path", []),
                        )
                    )
                if len(results) >= max_results:
                    break
        return results
