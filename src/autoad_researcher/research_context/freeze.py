"""Immutable ResearchContext freeze packages."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from autoad_researcher.ui.sources import load_source_registry


def freeze_context(run_dir: Path, *, freeze_version: str | None = None) -> dict[str, Any]:
    """Create an immutable context freeze under `context/freezes/{fv_id}`."""
    run_dir = Path(run_dir)
    context_dir = run_dir / "context"
    draft_path = context_dir / "research_context_draft.json"
    if not draft_path.is_file():
        raise FileNotFoundError("context/research_context_draft.json not found")

    freezes_dir = context_dir / "freezes"
    freezes_dir.mkdir(parents=True, exist_ok=True)
    fv_id = freeze_version or _next_freeze_version(freezes_dir)
    final_dir = freezes_dir / fv_id
    tmp_dir = freezes_dir / f".tmp_{fv_id}"
    if final_dir.exists():
        raise FileExistsError(f"freeze already exists: {fv_id}")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True)

    draft = _read_json(draft_path)
    source_registry = load_source_registry(run_dir)
    source_snapshot = _source_snapshot(source_registry)
    parse_attempt_snapshot = _parse_attempt_snapshot(source_registry)
    evidence_boundary = draft.get("evidence_boundary", {}) if isinstance(draft, dict) else {}
    research_brief = _render_research_brief(draft, fv_id)

    _write_json(tmp_dir / "research_context_draft.json", draft)
    (tmp_dir / "research_brief.md").write_text(research_brief, encoding="utf-8")
    _write_json(tmp_dir / "source_snapshot.json", source_snapshot)
    _write_json(tmp_dir / "parse_attempt_snapshot.json", parse_attempt_snapshot)
    _write_json(tmp_dir / "evidence_boundary.json", evidence_boundary)

    manifest = {
        "freeze_version": fv_id,
        "run_id": run_dir.name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_snapshot_hash": _sha256_file(tmp_dir / "source_snapshot.json"),
        "research_context_hash": _sha256_file(tmp_dir / "research_context_draft.json"),
        "research_brief_hash": _sha256_file(tmp_dir / "research_brief.md"),
    }
    _write_json(tmp_dir / "manifest.json", manifest)
    _validate_manifest_hashes(tmp_dir, manifest)

    tmp_dir.rename(final_dir)
    entry = _manifest_entry(fv_id, manifest["created_at"])
    _update_freeze_manifest(context_dir, entry)
    return {"freeze_version": fv_id, "freeze_dir": str(final_dir), "manifest": manifest}


def load_active_freeze_manifest(run_dir: Path) -> dict[str, Any] | None:
    path = Path(run_dir) / "context" / "freeze_manifest.json"
    if not path.is_file():
        return None
    manifest = _read_json(path)
    return manifest if isinstance(manifest, dict) else None


def active_freeze_context_path(run_dir: Path) -> Path | None:
    manifest = load_active_freeze_manifest(run_dir)
    if not manifest:
        return None
    active = manifest.get("active_freeze_version")
    if not isinstance(active, str):
        return None
    path = Path(run_dir) / "context" / "freezes" / active / "research_context_draft.json"
    return path if path.is_file() else None


def _next_freeze_version(freezes_dir: Path) -> str:
    max_seen = 0
    for path in freezes_dir.iterdir() if freezes_dir.exists() else []:
        name = path.name
        if not path.is_dir() or not name.startswith("fv_"):
            continue
        suffix = name[3:]
        if suffix.isdigit():
            max_seen = max(max_seen, int(suffix))
    return f"fv_{max_seen + 1:03d}"


def _source_snapshot(source_registry: dict[str, Any]) -> dict[str, Any]:
    sources = []
    for source in source_registry.get("sources", []):
        if not isinstance(source, dict):
            continue
        sources.append({
            "source_id": source.get("source_id"),
            "kind": source.get("kind"),
            "user_label": source.get("user_label"),
            "status": source.get("status"),
            "stored_path": source.get("stored_path"),
            "active_parse_attempt_id": source.get("active_parse_attempt_id"),
            "intake_status": source.get("intake_status"),
        })
    return {"schema_version": 1, "sources": sources}


def _parse_attempt_snapshot(source_registry: dict[str, Any]) -> dict[str, Any]:
    attempts = []
    for source in source_registry.get("sources", []):
        if not isinstance(source, dict):
            continue
        source_id = source.get("source_id")
        active = source.get("active_parse_attempt_id")
        for attempt in source.get("parse_attempts", []):
            if isinstance(attempt, dict):
                item = dict(attempt)
                item["source_id"] = item.get("source_id") or source_id
                item["active_for_source"] = bool(active and item.get("parse_attempt_id") == active)
                attempts.append(item)
    return {"schema_version": 1, "parse_attempts": attempts}


def _render_research_brief(draft: Any, freeze_version: str) -> str:
    if not isinstance(draft, dict):
        return f"# Research Context Freeze {freeze_version}\n\nNo structured draft was available.\n"
    task = draft.get("task") if isinstance(draft.get("task"), dict) else {}
    facts = draft.get("facts") if isinstance(draft.get("facts"), list) else []
    boundary = draft.get("evidence_boundary") if isinstance(draft.get("evidence_boundary"), dict) else {}
    lines = [
        f"# Research Context Freeze {freeze_version}",
        "",
        f"- Run ID: {draft.get('run_id')}",
        f"- Task: {task.get('goal')}",
        f"- Context ID: {draft.get('context_id')}",
        f"- Context Version: {draft.get('context_version')}",
        "",
        "## Key Facts",
    ]
    for fact in facts[:20]:
        if not isinstance(fact, dict):
            continue
        lines.append(f"- {fact.get('subject')} {fact.get('predicate')} {fact.get('value')}")
    lines.extend([
        "",
        "## Evidence Boundary",
        f"- Unparsed sources: {', '.join(boundary.get('unparsed_sources', [])) if isinstance(boundary.get('unparsed_sources'), list) else ''}",
        f"- Failed parse attempts: {', '.join(boundary.get('failed_parse_attempts', [])) if isinstance(boundary.get('failed_parse_attempts'), list) else ''}",
    ])
    return "\n".join(lines) + "\n"


def _manifest_entry(freeze_version: str, created_at: str) -> dict[str, str]:
    prefix = f"context/freezes/{freeze_version}"
    return {
        "freeze_version": freeze_version,
        "created_at": created_at,
        "created_from": "context/research_context_draft.json",
        "source_snapshot": f"{prefix}/source_snapshot.json",
        "parse_attempt_snapshot": f"{prefix}/parse_attempt_snapshot.json",
        "evidence_boundary": f"{prefix}/evidence_boundary.json",
        "research_brief": f"{prefix}/research_brief.md",
        "manifest": f"{prefix}/manifest.json",
    }


def _update_freeze_manifest(context_dir: Path, entry: dict[str, str]) -> None:
    path = context_dir / "freeze_manifest.json"
    if path.is_file():
        manifest = _read_json(path)
        if not isinstance(manifest, dict):
            manifest = {}
    else:
        manifest = {}
    freezes = manifest.get("freezes")
    if not isinstance(freezes, list):
        freezes = []
    if any(isinstance(item, dict) and item.get("freeze_version") == entry["freeze_version"] for item in freezes):
        raise FileExistsError(f"freeze manifest entry already exists: {entry['freeze_version']}")
    payload = {
        "active_freeze_version": entry["freeze_version"],
        "freezes": [*freezes, entry],
    }
    _write_json_atomic(path, payload)


def _validate_manifest_hashes(freeze_dir: Path, manifest: dict[str, str]) -> None:
    checks = {
        "source_snapshot_hash": freeze_dir / "source_snapshot.json",
        "research_context_hash": freeze_dir / "research_context_draft.json",
        "research_brief_hash": freeze_dir / "research_brief.md",
    }
    for key, path in checks.items():
        if manifest.get(key) != _sha256_file(path):
            raise ValueError(f"freeze manifest hash mismatch: {key}")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    _write_json(tmp, payload)
    tmp.replace(path)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
