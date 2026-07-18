"""Coordinator context enriched with Finalizer and Champion authority artifacts."""

from __future__ import annotations

from pathlib import Path

from autoad_researcher.experiment.coordinator import ContextPack, CoordinatorContextBuilder
from autoad_researcher.experiment.finalizer import OutcomeCard
from autoad_researcher.experiment.promotion import CandidateRegistry


class ScientificCoordinatorContextBuilder(CoordinatorContextBuilder):
    """Reuse the established ContextPack while replacing plan-era placeholders.

    The base builder remains responsible for Session, IdeaTree, Attempt, cognition,
    budget, and NoiseFloor state.  This subclass adds only authority artifacts that
    became available in Plan 05: immutable OutcomeCards and the Champion pointer.
    """

    def __init__(self, *, champion_registry: CandidateRegistry | None = None, **kwargs):
        super().__init__(**kwargs)
        self._champions = champion_registry or CandidateRegistry()

    def build(self, run_dir: Path, *, session_id: str, recent_commit_limit: int = 5) -> ContextPack:
        base = super().build(run_dir, session_id=session_id, recent_commit_limit=recent_commit_limit)
        outcomes: list[dict] = []
        for summary in base.outcome_cards:
            attempt_id = summary.get("attempt_id")
            card_path = run_dir / "attempts" / str(attempt_id) / "outcome_card.json"
            if card_path.is_file():
                outcomes.append(OutcomeCard.model_validate_json(card_path.read_text(encoding="utf-8")).model_dump(mode="json", exclude_none=True))
            else:
                outcomes.append(summary)
        values = base.model_dump(mode="json", exclude={"context_sha256"})
        values["outcome_cards"] = outcomes
        values["champion_summary"] = self._champions.current_summary_for_session(run_dir, session_id=session_id)
        return ContextPack.create(**values)
