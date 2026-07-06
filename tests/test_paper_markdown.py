"""Tests for derived paper.md generation from parsed blocks."""

from __future__ import annotations

import json

from autoad_researcher.paper_intelligence.markdown import (
    blocks_jsonl_to_paper_markdown,
    build_paper_markdown_from_blocks,
    looks_garbled_text,
)


def test_blocks_jsonl_to_paper_markdown_skips_garbled_blocks(tmp_path):
    parse_dir = tmp_path / "parse"
    parse_dir.mkdir()
    blocks_path = parse_dir / "blocks.jsonl"
    blocks_path.write_text(
        json.dumps({"physical_page_index": 0, "text": "x 350P A]#cS S G"}) + "\n"
        + json.dumps({"physical_page_index": 1, "text": "This paper proposes a simple anomaly detection method."}) + "\n",
        encoding="utf-8",
    )

    output = blocks_jsonl_to_paper_markdown(parse_dir)

    assert output == parse_dir / "paper.md"
    markdown = output.read_text(encoding="utf-8")
    assert "Page 2" in markdown
    assert "simple anomaly detection method" in markdown
    assert "350P" not in markdown


def test_build_paper_markdown_returns_empty_for_only_garbled_blocks(tmp_path):
    blocks_path = tmp_path / "blocks.jsonl"
    blocks_path.write_text(
        json.dumps({"physical_page_index": 0, "text": "x 350P A]#cS S G"}) + "\n",
        encoding="utf-8",
    )

    assert build_paper_markdown_from_blocks(blocks_path) == ""
    assert looks_garbled_text("x 350P A]#cS S G") is True
