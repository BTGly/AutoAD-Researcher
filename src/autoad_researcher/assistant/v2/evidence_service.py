"""Evidence service for V2. Reads V1 source registry, parse attempts, and paper artifacts.

Produces EvidenceIndex — the single source of truth for what the assistant can answer from.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

_LEGACY_PAPER_ARTIFACT_PATHS = [
    "paper/artifacts/paper_summary.json",
    "paper/artifacts/paper_candidates.json",
    "paper/artifacts/method_components.json",
    "paper/artifacts/paper_idea_sources.json",
    "paper/parse/paper.md",
    "paper/parse/sections.json",
]


def load_usable_evidence(run_dir: Path) -> list[dict[str, Any]]:
    """Load evidence from V1 source registry and legacy artifacts.

    Returns a list of evidence items, each with source_id, parse_attempt_id,
    artifact_path, evidence_type, support_level, and summary.
    """

    evidence: list[dict[str, Any]] = []

    sources = _load_sources(run_dir)
    for src in sources:
        active_pa = src.get("active_parse_attempt_id")
        attempts = src.get("parse_attempts") or []

        if not active_pa and not attempts:
            continue

        active_attempt = _find_active_attempt(attempts, active_pa)

        if active_attempt and active_attempt.get("status") == "ok":
            for artifact_path in _LEGACY_PAPER_ARTIFACT_PATHS:
                artifact = _read_paper_artifact(run_dir, artifact_path, attempt=active_attempt)
                if artifact:
                    evidence.append({
                        "source_id": src.get("source_id", ""),
                        "parse_attempt_id": active_attempt.get("parse_attempt_id", ""),
                        "artifact_path": artifact_path,
                        "evidence_type": _evidence_type_for_path(artifact_path),
                        "support_level": "supported",
                        "parser_known": parser_name not in (None, "unknown_legacy"),
                        "legacy": parser_name == "unknown_legacy",
                        "summary": artifact.get("summary", ""),
                        "raw": artifact.get("raw", {}),
                    })

        paper_parse_dir = run_dir / "paper" / "parse" / "attempts"
        if paper_parse_dir.exists():
            for attempt_dir in paper_parse_dir.iterdir():
                if not attempt_dir.is_dir():
                    continue
                qr_path = attempt_dir / "parse_quality_report.json"
                if qr_path.is_file():
                    try:
                        qr = json.loads(qr_path.read_text(encoding="utf-8"))
                        quality = qr.get("quality_level", "") or qr.get("quality", "")
                        if quality == "usable":
                            pa_id = attempt_dir.name
                            src_id = qr.get("source_id", "")
                            already = any(e.get("parse_attempt_id") == pa_id for e in evidence)
                            if not already:
                                for artifact_path in _LEGACY_PAPER_ARTIFACT_PATHS:
                                    artifact = _read_paper_artifact(run_dir, artifact_path, attempt=qr)
                                    if artifact:
                                        evidence.append({
                                            "source_id": src_id,
                                            "parse_attempt_id": pa_id,
                                            "artifact_path": artifact_path,
                                            "evidence_type": _evidence_type_for_path(artifact_path),
                                            "support_level": "supported",
                                            "parser_known": qr.get("parser") not in (None, "unknown_legacy"),
                                            "summary": artifact.get("summary", ""),
                                            "raw": artifact.get("raw", {}),
                                        })
                    except (json.JSONDecodeError, OSError):
                        pass

    return evidence


def load_candidate_sources(run_dir: Path) -> list[dict[str, Any]]:
    """Load web_search / candidate-only sources that are not yet evidence."""
    candidates: list[dict[str, Any]] = []
    sync_path = run_dir / "ui_chat" / "sync_web_search_results.jsonl"
    if sync_path.is_file():
        for line in sync_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    candidates.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return candidates


def load_unparsed_sources(run_dir: Path) -> list[dict[str, Any]]:
    """Sources that are registered but not yet parsed."""
    sources = _load_sources(run_dir)
    return [
        s for s in sources
        if s.get("status") in ("registered", "uploaded_not_parsed", "user_provided_not_ingested")
        and not s.get("parse_attempts")
    ]


def _load_sources(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / "sources" / "source_references.json"
    if not path.is_file():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("sources", [])
    except (json.JSONDecodeError, OSError):
        return []


def _find_active_attempt(attempts: list[dict], active_pa: str | None) -> dict | None:
    if not attempts:
        return None
    if active_pa:
        for a in attempts:
            if isinstance(a, dict) and a.get("parse_attempt_id") == active_pa:
                return a
    for a in attempts:
        if isinstance(a, dict) and a.get("status") == "ok":
            return a
    return attempts[0] if isinstance(attempts[0], dict) else None


def _read_paper_artifact(run_dir: Path, rel_path: str, attempt: dict | None = None) -> dict | None:
    full = run_dir / rel_path
    if not full.is_file():
        if attempt:
            pa_id = attempt.get("parse_attempt_id", "")
            if pa_id:
                alt = run_dir / "paper" / "parse" / "attempts" / pa_id / rel_path.split("/")[-1]
                if alt.is_file():
                    full = alt
                else:
                    return None
            else:
                return None
        else:
            return None

    try:
        data = json.loads(full.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    summary = _extract_summary(rel_path, data)
    if not summary:
        return None
    raw = _extract_raw_fields(rel_path, data)
    return {"summary": summary, "raw": raw}


def _evidence_type_for_path(path: str) -> str:
    if "paper_summary" in path:
        return "paper_summary"
    if "paper_candidates" in path:
        return "paper_candidates"
    if "method_components" in path:
        return "method_components"
    if "paper_idea_sources" in path:
        return "paper_idea_sources"
    if "sections" in path:
        return "sections"
    return "unknown"


def _extract_summary(path: str, data: dict) -> str:
    if "paper_summary" in path:
        parts = []
        title = data.get("title", "")
        title_text = _safe_text(title)
        if title_text:
            parts.append(f"Title: {title_text}")
        method = data.get("proposed_method", "")
        method_text = _safe_text(method)
        if method_text:
            parts.append(f"Method: {method_text}")
        problem = data.get("research_problem", "")
        problem_text = _safe_text(problem)
        if problem_text:
            parts.append(f"Problem: {problem_text}")
        return "\n".join(parts) if parts else ""
    if "paper_candidates" in path:
        return f"{len(data) if isinstance(data, list) else 0} candidate components"
    if "sections" in path:
        titles = []
        items = data if isinstance(data, list) else []
        for s in items:
            if isinstance(s, dict):
                t = s.get("title", "")
                t_safe = _safe_text(t)
                if t_safe:
                    titles.append(t_safe)
        return "Sections: " + ", ".join(titles[:10]) if titles else ""
    return ""

def _safe_text(value: Any) -> str:
    """Extract clean text from a value that could be str, dict (evidence claim), or list."""
    if isinstance(value, str):
        txt = value.strip()
        if len(txt) < 5 or _is_garbled(txt):
            return ""
        return txt[:500]
    if isinstance(value, dict):
        v = value.get("value", value.get("text", ""))
        if isinstance(v, str):
            txt = v.strip()
            if len(txt) < 5 or _is_garbled(txt):
                return ""
            return txt[:500]
    if isinstance(value, list) and value:
        return _safe_text(value[0]) if value else ""
    return ""

def _is_garbled(text: str) -> bool:
    if not text or len(text) < 5:
        return True
    words = text.split()
    if not words:
        return True
    avg_len = sum(len(w) for w in words) / len(words)
    if avg_len < 2.5:
        return True
    alpha = sum(1 for c in text if c.isalpha())
    if len(text) > 30 and alpha < len(text) * 0.15:
        return True
    return False


def _extract_raw_fields(path: str, data: dict) -> dict:
    raw: dict[str, Any] = {}
    if "paper_summary" in path:
        raw["title"] = data.get("title", "")
        raw["proposed_method"] = data.get("proposed_method", "")
    if isinstance(data, dict):
        raw = {**raw, **{k: v for k, v in data.items() if k not in raw and isinstance(v, (str, int, float, bool))}}
    return raw
