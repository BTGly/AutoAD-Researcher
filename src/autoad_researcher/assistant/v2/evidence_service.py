"""Evidence service for V2. Reads V1 source registry, parse attempts, and paper artifacts.

Produces EvidenceIndex — the single source of truth for what the assistant can answer from.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EVIDENCE_DIR = "evidence"
EVIDENCE_FILE = "evidence_index.jsonl"

_LEGACY_PAPER_ARTIFACT_PATHS = [
    "paper/artifacts/paper_reading_summary.json",
    "paper/artifacts/paper_reading_summary.md",
    "paper/artifacts/paper_method_cards.json",
    "paper/artifacts/paper_artifact_manifest.json",
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
    evidence.extend(_load_v2_artifact_evidence(run_dir))
    evidence.extend(_load_paper_text_evidence(run_dir))

    sources = _load_sources(run_dir)
    for src in sources:
        active_pa = src.get("active_parse_attempt_id")
        attempts = src.get("parse_attempts") or []

        if not active_pa and not attempts:
            continue

        active_attempt = _find_active_attempt(attempts, active_pa)

        if active_attempt and active_attempt.get("status") == "ok":
            quality_report_path = active_attempt.get("quality_report")
            if isinstance(quality_report_path, str) and not _quality_report_is_usable(run_dir, quality_report_path):
                continue
            parser_name = active_attempt.get("parser") or "unknown_legacy"
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
                                            "legacy": False,
                                            "summary": artifact.get("summary", ""),
                                            "raw": artifact.get("raw", {}),
                                        })
                    except (json.JSONDecodeError, OSError):
                        pass

    return _dedupe_evidence(evidence)


def append_artifact_evidence(
    run_dir: Path,
    *,
    source_id: str,
    artifact_path: str,
    evidence_type: str,
    summary: str,
    parser_name: str,
    support_level: str = "supported",
    raw: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one text-first evidence entry for V2 artifacts."""
    path = run_dir / EVIDENCE_DIR / EVIDENCE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "schema_version": 1,
        "evidence_id": _next_v2_evidence_id(run_dir),
        "source_id": source_id,
        "artifact_path": artifact_path,
        "evidence_type": evidence_type,
        "support_level": support_level,
        "parser_name": parser_name,
        "parser_known": parser_name not in ("", "unknown", "unavailable"),
        "legacy": False,
        "summary": _trim_text(summary, 1200),
        "raw": raw or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True))
        handle.write("\n")
    return entry


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


def load_unusable_parsed_sources(run_dir: Path) -> list[dict[str, Any]]:
    """Parsed/failed sources whose active attempt is not usable evidence."""
    result: list[dict[str, Any]] = []
    supported_source_ids = _source_ids_with_supported_text_evidence(run_dir)
    for source in _load_sources(run_dir):
        if str(source.get("source_id") or "") in supported_source_ids:
            continue
        attempts = source.get("parse_attempts")
        if not isinstance(attempts, list) or not attempts:
            continue
        active_attempt = _find_active_attempt(attempts, source.get("active_parse_attempt_id"))
        if not isinstance(active_attempt, dict):
            continue
        quality_report_path = active_attempt.get("quality_report")
        usable = isinstance(quality_report_path, str) and _quality_report_is_usable(run_dir, quality_report_path)
        if not usable:
            quality_report = _load_quality_report(run_dir, quality_report_path) if isinstance(quality_report_path, str) else {}
            result.append({
                "source_id": source.get("source_id", ""),
                "user_label": source.get("user_label", ""),
                "status": source.get("status", ""),
                "parse_attempt_id": active_attempt.get("parse_attempt_id", ""),
                "parser": active_attempt.get("parser", ""),
                "warnings": active_attempt.get("warnings", []),
                "quality_level": quality_report.get("quality_level", ""),
                "fatal_errors": quality_report.get("fatal_errors", []),
                "not_usable_for": quality_report.get("not_usable_for", []),
                "parser_errors": _load_parser_errors(run_dir, str(source.get("source_id", ""))),
            })
    return _dedupe_unusable_sources(result)


def _load_v2_artifact_evidence(run_dir: Path) -> list[dict[str, Any]]:
    path = run_dir / EVIDENCE_DIR / EVIDENCE_FILE
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(item, dict)
            and item.get("support_level") == "supported"
            and _artifact_evidence_is_currently_supported(run_dir, item)
        ):
            entries.append(item)
    return entries


def _artifact_evidence_is_currently_supported(run_dir: Path, item: dict[str, Any]) -> bool:
    if item.get("evidence_type") != "repo_summary":
        return True
    source_id = str(item.get("source_id") or "")
    if not source_id:
        return False
    return (run_dir / "repo_acquisition" / source_id / "repository_attestation.json").is_file()


def _source_ids_with_supported_text_evidence(run_dir: Path) -> set[str]:
    supported_types = {
        "paper_markdown_fallback",
        "paper_reading_summary",
        "paper_artifact_manifest",
        "paper_text",
        "uploaded_text",
        "web_markdown",
    }
    source_ids: set[str] = set()
    for item in _load_v2_artifact_evidence(run_dir):
        if item.get("evidence_type") in supported_types:
            source_id = str(item.get("source_id") or "")
            if source_id:
                source_ids.add(source_id)
    for item in _load_paper_text_evidence(run_dir):
        source_id = str(item.get("source_id") or "")
        if source_id:
            source_ids.add(source_id)
    return source_ids


def _dedupe_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in items:
        key = (
            str(item.get("source_id") or ""),
            str(item.get("parse_attempt_id") or ""),
            str(item.get("artifact_path") or ""),
            str(item.get("evidence_type") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _load_paper_text_evidence(run_dir: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    paper_index = run_dir / "paper" / "evidence_index.jsonl"
    if paper_index.is_file():
        for line in paper_index.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            evidence = record.get("evidence")
            if not isinstance(evidence, dict):
                continue
            parse_attempt_id = str(record.get("parse_attempt_id") or evidence.get("parse_attempt_id") or "")
            artifact_path = f"paper/parse/attempts/{parse_attempt_id}/paper.md" if parse_attempt_id else "paper/parse/paper.md"
            if not parse_attempt_id or not (run_dir / artifact_path).is_file():
                continue
            source_id = str(evidence.get("source_id") or "")
            page = evidence.get("physical_page_index")
            block_id = str(evidence.get("block_id") or "")
            entries.append({
                "source_id": source_id,
                "parse_attempt_id": parse_attempt_id,
                "artifact_path": artifact_path,
                "evidence_type": "paper_text",
                "support_level": "supported",
                "parser_name": "mineru_pipeline_v1",
                "parser_known": True,
                "legacy": False,
                "summary": _paper_evidence_summary(page, block_id),
                "raw": {"evidence_id": evidence.get("evidence_id"), "physical_page_index": page, "block_id": block_id},
            })
    return entries


def _quality_report_is_usable(run_dir: Path, rel_path: str) -> bool:
    report = _load_quality_report(run_dir, rel_path)
    return report.get("quality_level") == "usable"


def _load_quality_report(run_dir: Path, rel_path: str) -> dict[str, Any]:
    path = run_dir / rel_path
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_parser_errors(run_dir: Path, source_id: str) -> list[dict[str, str]]:
    source_dir = run_dir / "sources" / source_id
    if not source_dir.is_dir():
        return []
    errors: list[dict[str, str]] = []
    for path in sorted(source_dir.glob("*_error.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(payload, dict):
            errors.append({
                "parser_name": str(payload.get("parser_name") or path.stem.removesuffix("_error")),
                "error": _trim_text(str(payload.get("error") or ""), 500),
            })
    return errors


def _dedupe_unusable_sources(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (str(item.get("source_id") or ""), str(item.get("parse_attempt_id") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


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

    if full.suffix.lower() == ".md":
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        summary = _trim_text(text.strip(), 1000)
        return {"summary": summary, "raw": {"path": rel_path}} if summary else None

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
    if "paper_reading_summary" in path:
        return "paper_reading_summary"
    if "paper_method_cards" in path:
        return "paper_method_cards"
    if "paper_artifact_manifest" in path:
        return "paper_artifact_manifest"
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
    if "paper_reading_summary" in path:
        summary = _safe_text(data.get("summary", ""))
        if summary:
            return summary
        title = _safe_text(data.get("title", ""))
        return f"Paper reading summary: {title}" if title else ""
    if "paper_method_cards" in path:
        cards = data.get("cards") if isinstance(data, dict) else []
        if isinstance(cards, list):
            titles = [
                _safe_text(card.get("title", ""))
                for card in cards
                if isinstance(card, dict) and _safe_text(card.get("title", ""))
            ]
            return f"Method cards: {', '.join(titles[:8])}" if titles else f"{len(cards)} method cards"
    if "paper_artifact_manifest" in path:
        artifacts = data.get("artifacts") if isinstance(data, dict) else []
        default_context = _safe_text(data.get("default_context", ""))
        detail_context = _safe_text(data.get("detail_context", ""))
        count = len(artifacts) if isinstance(artifacts, list) else 0
        return f"Paper artifact manifest: {count} artifacts; default={default_context}; detail={detail_context}"
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
    if "paper_reading_summary" in path:
        raw["title"] = data.get("title", "")
        raw["source_of_truth"] = data.get("source_of_truth", "")
        raw["anchors"] = data.get("anchors", [])
    if "paper_artifact_manifest" in path:
        raw["default_context"] = data.get("default_context", "")
        raw["detail_context"] = data.get("detail_context", "")
        raw["artifacts"] = data.get("artifacts", [])
    if "paper_summary" in path:
        raw["title"] = data.get("title", "")
        raw["proposed_method"] = data.get("proposed_method", "")
    if isinstance(data, dict):
        raw = {**raw, **{k: v for k, v in data.items() if k not in raw and isinstance(v, (str, int, float, bool))}}
    return raw


def _next_v2_evidence_id(run_dir: Path) -> str:
    max_seen = 0
    path = run_dir / EVIDENCE_DIR / EVIDENCE_FILE
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            eid = item.get("evidence_id")
            if isinstance(eid, str) and eid.startswith("v2ev_"):
                suffix = eid[5:]
                if suffix.isdigit():
                    max_seen = max(max_seen, int(suffix))
    return f"v2ev_{max_seen + 1:06d}"


def _paper_evidence_summary(page: Any, block_id: str) -> str:
    parts = ["Parsed paper text"]
    if isinstance(page, int):
        parts.append(f"page {page + 1}")
    if block_id:
        parts.append(block_id)
    return " · ".join(parts)


def _trim_text(text: str, limit: int) -> str:
    cleaned = " ".join(str(text).split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"
