"""Generate, validate and publish deterministic report content."""

from pathlib import Path
from typing import Any

from autoad_researcher.assistant.v2.event_service import append_event
from autoad_researcher.reporting.evidence import EvidenceIndex
from autoad_researcher.reporting.facts import ExperimentReportFactsV1
from autoad_researcher.reporting.narrative_agent import generate_narrative
from autoad_researcher.reporting.persistence import write_immutable_report_json
from autoad_researcher.reporting.store import ReportStore

REPORT_NARRATIVE_JOB_TYPE = "report_narrative_generate"


def run_narrative_job(run_dir: Path, job: dict[str, Any]) -> list[str]:
    report_id = job.get("report_id")
    if not isinstance(report_id, str):
        raise ValueError("report Narrative Job lacks report identity")
    store = ReportStore()
    state = store.load_state(run_dir, report_id)
    if state.generation_status == "content_ready":
        return _outputs(run_dir, report_id)
    if state.generation_status != "generating_narrative":
        raise ValueError("report cannot generate Narrative from its current state")
    directory = run_dir / "reports" / report_id
    facts = ExperimentReportFactsV1.model_validate_json((directory / "report_facts.json").read_text(encoding="utf-8"))
    evidence = EvidenceIndex.model_validate_json((directory / "evidence_index.json").read_text(encoding="utf-8"))
    generated = generate_narrative(facts=facts, evidence=evidence)
    narrative = _bind_default_evidence(generated.narrative, evidence)
    write_immutable_report_json(run_dir, report_id=report_id, filename="narrative_sections.json", artifact_type="report_narrative", value=narrative.model_dump(mode="json"))
    write_immutable_report_json(
        run_dir,
        report_id=report_id,
        filename="narrative_generation.json",
        artifact_type="report_narrative_generation",
        value={"schema_version": 1, "profile": "structured-chat-v1", "mode": generated.mode, "model": generated.model, "fallback_reason": generated.fallback_reason},
    )
    store.transition_generation(run_dir, report_id=report_id, target="validating")
    append_event(run_dir, "report.narrative_generated", {"report_id": report_id})
    return [str((directory / name).relative_to(run_dir)) for name in ("narrative_sections.json", "narrative_generation.json")]


def _outputs(run_dir: Path, report_id: str) -> list[str]:
    directory = run_dir / "reports" / report_id
    return [str((directory / name).relative_to(run_dir)) for name in ("narrative_sections.json", "narrative_generation.json", "report_validation.json", "report.md")]


def _bind_default_evidence(narrative, evidence: EvidenceIndex):
    root_ids = [item.evidence_id for item in evidence.entries if item.field_path == "$"]
    if not root_ids:
        raise ValueError("report Narrative requires snapshot Evidence")
    return narrative.model_copy(update={
        "claims": [
            claim if not claim.fact_refs or claim.evidence_ids else claim.model_copy(update={"evidence_ids": [root_ids[0]]})
            for claim in narrative.claims
        ]
    })
