"""Evidence-based context builder for Research Chat intent alignment."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.assistant.probe import WhatWeKnow, silent_probe
from autoad_researcher.ui.sources import load_source_registry


SourceContextStatus = Literal[
    "user_provided_not_ingested",
    "uploaded_not_parsed",
    "parsing",
    "parsed",
    "failed",
]


class CandidateReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    kind: str
    user_label: str
    status: SourceContextStatus
    reference_value: str | None = None


class UploadedUnparsedSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    kind: str
    user_label: str
    status: SourceContextStatus
    stored_path: str | None = None


class ParsedPaperEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str | None = None
    user_label: str | None = None
    artifact_refs: list[str] = Field(default_factory=list)
    paper_methods: list[str] = Field(default_factory=list)


class ResearchChatEvidenceContext(BaseModel):
    """Structured Research Chat context.

    Candidate references are intentionally separate from known facts. A title,
    arXiv ID, URL, DOI, or repo name only proves that the user provided a
    reference; it does not prove that AutoAD has parsed paper content or
    analyzed repository code.
    """

    model_config = ConfigDict(extra="forbid")

    known_facts: dict[str, str] = Field(default_factory=dict)
    candidate_references: list[CandidateReference] = Field(default_factory=list)
    uploaded_unparsed_sources: list[UploadedUnparsedSource] = Field(default_factory=list)
    parsed_paper_evidence: list[ParsedPaperEvidence] = Field(default_factory=list)
    missing_blocking_gaps: list[str] = Field(default_factory=list)
    forbidden_assumptions: list[str] = Field(default_factory=list)
    has_repo_evidence: bool = False
    has_parsed_paper_evidence: bool = False


def build_research_chat_evidence_context(run_dir: Path) -> ResearchChatEvidenceContext:
    """Build a deterministic, evidence-partitioned context for Research Chat."""
    what = _probe_or_empty(run_dir)
    registry = _load_registry_or_empty(run_dir)

    candidate_references: list[CandidateReference] = []
    uploaded_unparsed_sources: list[UploadedUnparsedSource] = []
    parsed_source_ids: list[str] = []
    parsed_source_labels: list[str] = []

    for source in registry.get("sources", []):
        source_id = _clean_str(source.get("source_id"))
        kind = _clean_str(source.get("kind"))
        user_label = _clean_str(source.get("user_label"))
        status = _clean_str(source.get("status"))
        if not source_id or not kind or not user_label or status not in _STATUS_VALUES:
            continue

        stored_path = _clean_str(source.get("stored_path"))
        if status == "user_provided_not_ingested" or (kind in _REFERENCE_KINDS and status != "parsed"):
            candidate_references.append(
                CandidateReference(
                    source_id=source_id,
                    kind=kind,
                    user_label=user_label,
                    status=status,  # type: ignore[arg-type]
                    reference_value=_clean_str(source.get("reference_value")) or stored_path,
                )
            )
            continue

        if status in {"uploaded_not_parsed", "parsing", "failed"}:
            uploaded_unparsed_sources.append(
                UploadedUnparsedSource(
                    source_id=source_id,
                    kind=kind,
                    user_label=user_label,
                    status=status,  # type: ignore[arg-type]
                    stored_path=stored_path,
                )
            )
            continue

        if kind == "paper_pdf" and status == "parsed":
            parsed_source_ids.append(source_id)
            parsed_source_labels.append(user_label)

    parsed_paper_evidence: list[ParsedPaperEvidence] = []
    if what.has_paper_artifacts:
        parsed_paper_evidence.append(
            ParsedPaperEvidence(
                source_id=parsed_source_ids[0] if parsed_source_ids else None,
                user_label=parsed_source_labels[0] if parsed_source_labels else None,
                artifact_refs=[
                    ref for ref in what.evidence_artifacts
                    if ref.startswith("paper/")
                ],
                paper_methods=what.paper_methods,
            )
        )

    return ResearchChatEvidenceContext(
        known_facts=_known_facts_from_probe(what),
        candidate_references=candidate_references,
        uploaded_unparsed_sources=uploaded_unparsed_sources,
        parsed_paper_evidence=parsed_paper_evidence,
        missing_blocking_gaps=_select_blocking_gaps(what.missing_fields),
        forbidden_assumptions=_forbidden_assumptions(),
        has_repo_evidence=what.has_repo_summary,
        has_parsed_paper_evidence=bool(parsed_paper_evidence),
    )


def render_research_chat_evidence_context(context: ResearchChatEvidenceContext) -> str:
    """Render a compact system-message form of the structured context."""
    return context.model_dump_json(indent=2)


def _probe_or_empty(run_dir: Path) -> WhatWeKnow:
    try:
        return silent_probe(run_dir.name, runs_root=run_dir.parent)
    except Exception:
        return WhatWeKnow(run_id=run_dir.name)


def _load_registry_or_empty(run_dir: Path) -> dict[str, Any]:
    try:
        return load_source_registry(run_dir)
    except Exception:
        return {"schema_version": 1, "sources": []}


def _known_facts_from_probe(what: WhatWeKnow) -> dict[str, str]:
    facts: dict[str, str] = {}
    if what.baseline_method:
        facts["baseline_method"] = what.baseline_method
    if what.baseline_commit:
        facts["baseline_commit"] = what.baseline_commit
    if what.dataset:
        facts["dataset"] = what.dataset
    if what.primary_metric:
        facts["primary_metric"] = what.primary_metric
    return facts


def _forbidden_assumptions() -> list[str]:
    return [
        "Do not treat candidate references as parsed paper evidence.",
        "Do not claim uploaded_not_parsed files have been read.",
        "Do not infer repository code structure from a repo name or URL.",
        "Do not decide patch hooks, algorithms, hyperparameters, or variants during intent alignment.",
        "Do not treat task confirmation as patch approval or execution approval.",
    ]


def _select_blocking_gaps(missing_fields: list[str]) -> list[str]:
    """Keep at most three gaps, prioritizing fields users must confirm."""
    prioritized = ["category", "metric_direction", "dataset", "primary_metric"]
    selected: list[str] = []
    for field in [*prioritized, *missing_fields]:
        if field in missing_fields and field not in selected:
            selected.append(field)
        if len(selected) == 3:
            break
    return selected


def _clean_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


_REFERENCE_KINDS = {"arxiv_id", "doi", "url", "github_repo"}
_STATUS_VALUES = {
    "user_provided_not_ingested",
    "uploaded_not_parsed",
    "parsing",
    "parsed",
    "failed",
}
