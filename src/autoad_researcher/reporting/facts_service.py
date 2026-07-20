"""Durable Facts-stage worker implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.reporting.digest import build_report_digest
from autoad_researcher.reporting.evidence import build_evidence_index
from autoad_researcher.reporting.facts import assemble_facts, facts_content_sha256
from autoad_researcher.reporting.facts_enrichment import enrich_facts
from autoad_researcher.reporting.persistence import write_immutable_report_json
from autoad_researcher.reporting.store import ReportStore

REPORT_FACTS_JOB_TYPE = "report_facts_assemble"


def run_facts_job(run_dir: Path, job: dict[str, Any]) -> list[str]:
    report_id = job.get("report_id")
    payload = job.get("payload")
    if not isinstance(report_id, str) or not isinstance(payload, dict):
        raise ValueError("report Facts Job lacks report identity")
    store = ReportStore()
    manifest = store.load_manifest(run_dir, report_id)
    if payload.get("snapshot_content_sha256") != manifest.source_snapshot_content_sha256:
        raise ValueError("report Facts Job identity conflicts with manifest")
    state = store.load_state(run_dir, report_id)
    if state.generation_status == "generating_narrative":
        return _existing_outputs(run_dir, report_id)
    if state.generation_status != "assembling_facts":
        raise ValueError("report cannot assemble Facts from its current state")
    snapshot = store.load_snapshot(run_dir, report_id)
    facts = assemble_facts(run_dir, snapshot=snapshot)
    facts = enrich_facts(run_dir, snapshot=snapshot, facts=facts)
    facts_hash = facts_content_sha256(facts)
    evidence = build_evidence_index(
        run_dir,
        report_id=report_id,
        snapshot_content_sha256=manifest.source_snapshot_content_sha256,
        snapshot=snapshot,
    )
    digest = build_report_digest(report_id=report_id, facts=facts)
    refs = [
        write_immutable_report_json(
            run_dir,
            report_id=report_id,
            filename="report_facts.json",
            artifact_type="report_facts",
            value=facts.model_dump(mode="json"),
            content_sha256=facts_hash,
        ),
        write_immutable_report_json(
            run_dir,
            report_id=report_id,
            filename="evidence_index.json",
            artifact_type="report_evidence_index",
            value=evidence.model_dump(mode="json"),
        ),
        write_immutable_report_json(
            run_dir,
            report_id=report_id,
            filename="report_digest.json",
            artifact_type="report_digest",
            value=digest.model_dump(mode="json"),
        ),
    ]
    store.transition_generation(run_dir, report_id=report_id, target="generating_narrative")
    append_event(run_dir, "report.facts_assembled", {"report_id": report_id, "facts_content_sha256": facts_hash})
    return [reference.locator for reference in refs]


def _existing_outputs(run_dir: Path, report_id: str) -> list[str]:
    directory = run_dir / "reports" / report_id
    names = ("report_facts.json", "evidence_index.json", "report_digest.json")
    if not all((directory / name).is_file() for name in names):
        raise ValueError("report state claims Facts completed but immutable artifacts are missing")
    return [str((directory / name).relative_to(run_dir)) for name in names]
