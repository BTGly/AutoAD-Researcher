"""Text-first reading artifacts for parsed papers.

The summary is an entry point for the main assistant and UI. It is not the
source of truth: each extracted section keeps an anchor back to paper.md.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SUMMARY_MD = "paper_reading_summary.md"
SUMMARY_JSON = "paper_reading_summary.json"
METHOD_CARDS_JSON = "paper_method_cards.json"
MANIFEST_JSON = "paper_artifact_manifest.json"


@dataclass(frozen=True)
class PaperReadingArtifacts:
    source_id: str
    parse_attempt_id: str
    summary_md_path: str
    summary_json_path: str
    method_cards_path: str
    manifest_path: str
    summary: str
    anchors: list[dict[str, Any]]


def build_paper_reading_artifacts(
    run_dir: Path,
    *,
    source_id: str,
    parse_attempt_id: str,
    paper_markdown_relpath: str,
    parser_name: str,
) -> PaperReadingArtifacts | None:
    """Create anchored summary/manifest artifacts from a readable paper.md."""
    run_dir = Path(run_dir)
    paper_path = run_dir / paper_markdown_relpath
    if not paper_path.is_file():
        return None
    text = paper_path.read_text(encoding="utf-8", errors="replace")
    sections = _split_markdown_sections(text)
    readable_sections = [s for s in sections if _has_readable_text(s["text"])]
    if not readable_sections:
        return None

    artifacts_dir = run_dir / "paper" / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    title = _infer_title(readable_sections, source_id)
    anchors = _select_anchors(readable_sections)
    method_cards = _build_method_cards(readable_sections, paper_markdown_relpath)
    summary_text = _build_summary_text(title, readable_sections, anchors)
    generated_at = datetime.now(timezone.utc).isoformat()

    summary_json = {
        "schema_version": 1,
        "source_id": source_id,
        "parse_attempt_id": parse_attempt_id,
        "parser_name": parser_name,
        "paper_markdown_path": paper_markdown_relpath,
        "title": title,
        "generated_at": generated_at,
        "summary": summary_text,
        "anchors": anchors,
        "section_count": len(readable_sections),
        "text_first": True,
        "source_of_truth": paper_markdown_relpath,
        "limitations": [
            "This summary is a routing artifact; verify detailed claims against paper.md anchors.",
            "Image understanding is limited to text extracted by the parser unless a vision-capable model reads image artifacts.",
        ],
    }
    manifest = {
        "schema_version": 1,
        "source_id": source_id,
        "parse_attempt_id": parse_attempt_id,
        "parser_name": parser_name,
        "quality": "usable",
        "generated_at": generated_at,
        "artifacts": [
            {"role": "summary_markdown", "path": f"paper/artifacts/{SUMMARY_MD}", "kind": "markdown"},
            {"role": "summary_json", "path": f"paper/artifacts/{SUMMARY_JSON}", "kind": "json"},
            {"role": "method_cards", "path": f"paper/artifacts/{METHOD_CARDS_JSON}", "kind": "json"},
            {"role": "source_markdown", "path": paper_markdown_relpath, "kind": "markdown"},
        ],
        "default_context": f"paper/artifacts/{SUMMARY_MD}",
        "detail_context": paper_markdown_relpath,
    }

    summary_md = _summary_markdown(summary_json, method_cards)
    _write_json(artifacts_dir / SUMMARY_JSON, summary_json)
    _write_json(artifacts_dir / METHOD_CARDS_JSON, method_cards)
    _write_json(artifacts_dir / MANIFEST_JSON, manifest)
    _write_text(artifacts_dir / SUMMARY_MD, summary_md)

    return PaperReadingArtifacts(
        source_id=source_id,
        parse_attempt_id=parse_attempt_id,
        summary_md_path=f"paper/artifacts/{SUMMARY_MD}",
        summary_json_path=f"paper/artifacts/{SUMMARY_JSON}",
        method_cards_path=f"paper/artifacts/{METHOD_CARDS_JSON}",
        manifest_path=f"paper/artifacts/{MANIFEST_JSON}",
        summary=summary_text,
        anchors=anchors,
    )


def _split_markdown_sections(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    sections: list[dict[str, Any]] = []
    current_title = "Document"
    current_start = 1
    current_lines: list[str] = []

    for idx, line in enumerate(lines, start=1):
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match and current_lines:
            sections.append({
                "title": current_title,
                "start_line": current_start,
                "end_line": idx - 1,
                "text": "\n".join(current_lines).strip(),
            })
            current_title = match.group(2).strip()
            current_start = idx
            current_lines = [line]
        elif match:
            current_title = match.group(2).strip()
            current_start = idx
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_lines:
        sections.append({
            "title": current_title,
            "start_line": current_start,
            "end_line": len(lines),
            "text": "\n".join(current_lines).strip(),
        })
    return sections


def _has_readable_text(text: str) -> bool:
    words = re.findall(r"[A-Za-z]{3,}|[\u4e00-\u9fff]{2,}", text)
    return len(words) >= 8


def _infer_title(sections: list[dict[str, Any]], fallback: str) -> str:
    for section in sections[:3]:
        title = str(section.get("title") or "").strip()
        if title and not title.lower().startswith("page "):
            return title[:180]
    first = re.sub(r"\s+", " ", sections[0]["text"]).strip()
    return first[:120] if first else fallback


def _select_anchors(sections: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    anchors: list[dict[str, Any]] = []
    scored = sorted(sections, key=lambda s: _section_score(s), reverse=True)
    for section in scored[:limit]:
        snippet = _trim(re.sub(r"\s+", " ", section["text"]), 520)
        anchors.append({
            "title": section["title"],
            "start_line": section["start_line"],
            "end_line": section["end_line"],
            "snippet": snippet,
        })
    return anchors


def _section_score(section: dict[str, Any]) -> int:
    text = f"{section.get('title', '')}\n{section.get('text', '')}".lower()
    keywords = [
        "method", "approach", "propose", "architecture", "algorithm",
        "experiment", "result", "ablation", "mvtec", "patchcore", "anomaly",
    ]
    return sum(text.count(k) for k in keywords) * 10 + min(len(text), 2000) // 200


def _build_method_cards(sections: list[dict[str, Any]], paper_path: str) -> dict[str, Any]:
    cards: list[dict[str, Any]] = []
    for section in sorted(sections, key=lambda s: _section_score(s), reverse=True)[:12]:
        text = section["text"]
        title = section["title"]
        if _section_score(section) <= 0:
            continue
        cards.append({
            "card_id": f"method_card_{len(cards) + 1:03d}",
            "title": title,
            "anchor": {
                "artifact_path": paper_path,
                "start_line": section["start_line"],
                "end_line": section["end_line"],
            },
            "summary": _trim(re.sub(r"\s+", " ", text), 700),
            "signals": _matched_signals(text),
        })
    return {"schema_version": 1, "cards": cards}


def _matched_signals(text: str) -> list[str]:
    lower = text.lower()
    signals = []
    for key in ("method", "approach", "experiment", "result", "ablation", "dataset", "mvtec", "patchcore"):
        if key in lower:
            signals.append(key)
    return signals


def _build_summary_text(title: str, sections: list[dict[str, Any]], anchors: list[dict[str, Any]]) -> str:
    section_titles = ", ".join(str(s["title"]) for s in sections[:8])
    first_anchor = anchors[0]["snippet"] if anchors else ""
    parts = [
        f"Title: {title}",
        f"Readable sections: {section_titles}",
    ]
    if first_anchor:
        parts.append(f"Most relevant excerpt: {first_anchor}")
    parts.append("Use this summary as a routing artifact; inspect paper.md anchors for detailed paper claims.")
    return "\n".join(parts)


def _summary_markdown(summary: dict[str, Any], method_cards: dict[str, Any]) -> str:
    lines = [
        f"# {summary['title']}",
        "",
        "## Reading Summary",
        summary["summary"],
        "",
        "## Anchors",
    ]
    for anchor in summary["anchors"]:
        lines.append(
            f"- {anchor['title']} "
            f"(paper.md lines {anchor['start_line']}-{anchor['end_line']}): {anchor['snippet']}"
        )
    lines.extend(["", "## Method Cards"])
    for card in method_cards.get("cards", []):
        anchor = card["anchor"]
        lines.append(
            f"- {card['title']} "
            f"({anchor['artifact_path']} lines {anchor['start_line']}-{anchor['end_line']}): "
            f"{card['summary']}"
        )
    lines.extend([
        "",
        "## Use",
        "- This file is the compact reading entry point.",
        "- For formulae, exact methods, tables, and challenged claims, read the linked paper.md lines.",
    ])
    return "\n".join(lines).strip() + "\n"


def _trim(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
