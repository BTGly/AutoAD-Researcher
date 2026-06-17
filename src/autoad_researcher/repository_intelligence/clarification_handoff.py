"""Handoff from Repository Intelligence uncertainties to Intent Clarifier."""

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.repository_intelligence.ids import IdentifierPattern

BlockingArea = Literal[
    "repository_selection",
    "environment",
    "entrypoint",
    "dataset",
    "evaluation",
    "path_policy",
    "user_goal",
]
QuestionPriority = Literal["blocking", "high", "medium", "low"]


class ClarificationQuestionCandidate(BaseModel):
    """Repository-specific question candidate for Intent Clarifier handoff."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question_id: str = Field(pattern=IdentifierPattern)
    question: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    blocking_area: BlockingArea
    priority: QuestionPriority
    evidence_ids: list[str] = Field(default_factory=list)
    known_facts: list[str] = Field(default_factory=list)
    expected_answer_type: str = Field(min_length=1)


class ClarificationQuestionCandidatesArtifact(BaseModel):
    """Persisted clarification question candidates."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    questions: list[ClarificationQuestionCandidate] = Field(default_factory=list)
    backlog: list[ClarificationQuestionCandidate] = Field(default_factory=list)


def build_clarification_question_candidates(
    *,
    artifact_dir: Path,
    output_path: Path,
    max_priority_questions: int = 3,
) -> ClarificationQuestionCandidatesArtifact:
    """Build clarification question candidates from uncertainties only."""
    uncertainties = json.loads((artifact_dir / "uncertainties.json").read_text(encoding="utf-8"))
    candidates = _questions_from_uncertainties(uncertainties)
    deduped = _dedupe_by_area(candidates)
    priority = [candidate for candidate in deduped if candidate.priority in {"blocking", "high"}]
    questions = priority[:max_priority_questions]
    backlog = priority[max_priority_questions:] + [candidate for candidate in deduped if candidate.priority not in {"blocking", "high"}]
    artifact = ClarificationQuestionCandidatesArtifact(schema_version=1, questions=questions, backlog=backlog)
    _write_json_atomic(output_path, artifact)
    return artifact


def _questions_from_uncertainties(payload) -> list[ClarificationQuestionCandidate]:
    questions: list[ClarificationQuestionCandidate] = []
    for index, group in enumerate(payload.get("groups", []), 1):
        category = group.get("category", "")
        area = _area_for_category(category)
        if area is None:
            continue
        evidence_ids = _collect_evidence_ids(group)
        questions.append(
            ClarificationQuestionCandidate(
                question_id=f"repo_clarify_{index:03d}",
                question=_question_text(area),
                reason=f"Repository Intelligence reported unresolved uncertainty: {category}",
                blocking_area=area,
                priority="blocking" if category.startswith("blocking") else "high",
                evidence_ids=evidence_ids,
                known_facts=[],
                expected_answer_type=_answer_type_for_area(area),
            )
        )
    return questions


def _area_for_category(category: str) -> BlockingArea | None:
    mapping: dict[str, BlockingArea] = {
        "blocking_environment_plan": "environment",
        "blocking_entrypoint_selection": "entrypoint",
        "blocking_evaluation_contract": "evaluation",
        "blocking_dataset_asset_access": "dataset",
        "scientific_validity_risks": "evaluation",
        "low_priority_unknowns": "user_goal",
    }
    return mapping.get(category)


def _question_text(area: BlockingArea) -> str:
    return {
        "repository_selection": "Which repository should AutoAD use for this task?",
        "environment": "Which environment assumption should AutoAD use or verify first?",
        "entrypoint": "Which repository entrypoint should AutoAD treat as primary?",
        "dataset": "Which dataset or external asset should AutoAD assume is available?",
        "evaluation": "Which evaluation protocol or metric should AutoAD prioritize?",
        "path_policy": "Which paths should AutoAD protect from modification?",
        "user_goal": "Which user goal detail should AutoAD preserve as mandatory?",
    }[area]


def _answer_type_for_area(area: BlockingArea) -> str:
    if area in {"entrypoint", "dataset", "evaluation", "repository_selection"}:
        return "single_choice_or_free_text"
    return "free_text"


def _collect_evidence_ids(value) -> list[str]:
    ids: list[str] = []
    if isinstance(value, dict):
        for evidence_id in value.get("evidence_ids", []):
            if evidence_id not in ids:
                ids.append(evidence_id)
        for child in value.values():
            for evidence_id in _collect_evidence_ids(child):
                if evidence_id not in ids:
                    ids.append(evidence_id)
    elif isinstance(value, list):
        for child in value:
            for evidence_id in _collect_evidence_ids(child):
                if evidence_id not in ids:
                    ids.append(evidence_id)
    return ids


def _dedupe_by_area(candidates: list[ClarificationQuestionCandidate]) -> list[ClarificationQuestionCandidate]:
    seen: set[str] = set()
    deduped: list[ClarificationQuestionCandidate] = []
    for candidate in candidates:
        if candidate.blocking_area in seen:
            continue
        seen.add(candidate.blocking_area)
        deduped.append(candidate)
    return deduped


def _write_json_atomic(path: Path, value: BaseModel) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        data = json.dumps(value.model_dump(mode="json", exclude_none=True), ensure_ascii=False, indent=2, sort_keys=True)
        with tmp.open("wb") as f:
            f.write(data.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
