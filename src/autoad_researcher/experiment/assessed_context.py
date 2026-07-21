"""Coordinator ContextPack view that overlays rebuildable scientific assessments."""

from __future__ import annotations

from pathlib import Path

from autoad_researcher.experiment.coordinator import ContextPack
from autoad_researcher.experiment.scientific_assessment import ScientificAssessmentService
from autoad_researcher.experiment.scientific_context import ScientificCoordinatorContextBuilder


class AssessedScientificCoordinatorContextBuilder(ScientificCoordinatorContextBuilder):
    def __init__(self, *, assessment_service: ScientificAssessmentService | None = None, **kwargs):
        super().__init__(**kwargs)
        self._assessments = assessment_service or ScientificAssessmentService()

    def build(self, run_dir: Path, *, session_id: str, recent_commit_limit: int = 5) -> ContextPack:
        base = super().build(run_dir, session_id=session_id, recent_commit_limit=recent_commit_limit)
        assessed: list[dict] = []
        for summary in base.outcome_cards:
            attempt_id = summary.get("attempt_id")
            attempt_dir = run_dir / "attempts" / str(attempt_id)
            if (
                isinstance(attempt_id, str)
                and (attempt_dir / "outcome_card.json").is_file()
                and (attempt_dir / "scientific_evaluation_inputs.json").is_file()
            ):
                assessed.append(
                    self._assessments.effective_assessment(
                        run_dir,
                        attempt_id=attempt_id,
                    ).model_dump(mode="json", exclude_none=True)
                )
            else:
                assessed.append({
                    key: value
                    for key, value in summary.items()
                    if key not in {"evaluation_status", "scientific_effect", "primary_delta", "guardrail_deltas"}
                } | {"assessment_status": "UNASSESSED"})
        values = base.model_dump(mode="json", exclude={"context_sha256"})
        values["outcome_cards"] = assessed
        return ContextPack.create(**values)
