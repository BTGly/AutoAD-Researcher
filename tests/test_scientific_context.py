from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from autoad_researcher.experiment.finalizer import OutcomeCard
from autoad_researcher.experiment.idea_tree import IdeaTreeStore
from autoad_researcher.experiment.promotion import CandidateRegistry, CandidateSnapshot, ChampionPointer
from autoad_researcher.experiment.scientific_context import ScientificCoordinatorContextBuilder
from autoad_researcher.experiment.session_store import ExperimentSessionStore


@dataclass
class _Attempt:
    attempt_id: str = "attempt_000001"
    runtime_status: str = "COMPLETED"
    failure_code: str | None = None
    execution_result_ref: str | None = "attempts/attempt_000001/execution_result.json"
    retry_of: str | None = None


class _AttemptStore:
    def list_for_session(self, _run_dir, *, session_id):
        assert session_id
        return [_Attempt()]


def _session(run_dir: Path) -> str:
    session, _ = ExperimentSessionStore().create_or_get(
        run_dir,
        task_ref="input_task.yaml",
        task_hash="d" * 64,
        execution_mode="agent_assisted_after_approval",
        budget={"configured_by": "scientific context fixture"},
    )
    IdeaTreeStore().create_or_get(run_dir, session_id=session.session_id)
    return session.session_id


def test_scientific_context_reads_outcome_cards_and_champion_pointer(tmp_path: Path):
    session_id = _session(tmp_path)
    attempt_dir = tmp_path / "attempts" / "attempt_000001"
    attempt_dir.mkdir(parents=True)
    card = OutcomeCard(
        attempt_id="attempt_000001",
        runtime_status="COMPLETED",
        attempt_category="scientifically_evaluable",
        execution_result_ref="attempts/attempt_000001/execution_result.json",
        metrics={"score": 0.9},
        protocol_valid=True,
        execution_status="COMPLETED",
        patch_applied=True,
        smoke_passed=True,
        metrics_parsed=True,
        protocol_intact=True,
        evaluation_status="COMPARABLE",
        scientific_effect="IMPROVEMENT",
        primary_delta=0.1,
    )
    (attempt_dir / "outcome_card.json").write_text(card.model_dump_json(), encoding="utf-8")

    registry = CandidateRegistry()
    candidate = CandidateSnapshot(
        candidate_id="candidate_000001",
        session_id=session_id,
        evaluation_contract_hash="a" * 64,
        idea_id="idea_000001",
        attempt_id="attempt_000001",
        source_branch="executor/attempt_000001",
        source_commit="b" * 40,
        patch_sha256="c" * 64,
        metrics_ref="attempts/attempt_000001/metrics.json",
        resource_ref="attempts/attempt_000001/execution_result.json",
        b_dev_evidence_ref="attempts/attempt_000001/outcome_card.json",
        b_test_evidence_ref="attempts/attempt_000002/outcome_card.json",
        b_test_passed=True,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    registry.create_candidate(tmp_path, candidate)
    registry.update_pointer(
        tmp_path,
        contract_hash="a" * 64,
        pointer=ChampionPointer(
            candidate_id="candidate_000001",
            event_id="champion:promotion_000001",
            trunk_commit="d" * 40,
            updated_at=datetime.now(timezone.utc).isoformat(),
        ),
    )

    context = ScientificCoordinatorContextBuilder(
        attempt_store=_AttemptStore(),
        champion_registry=registry,
    ).build(tmp_path, session_id=session_id)
    assert context.outcome_cards[0]["scientific_effect"] == "IMPROVEMENT"
    assert context.champion_summary["a" * 64]["candidate"]["candidate_id"] == "candidate_000001"
