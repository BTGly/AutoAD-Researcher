from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from autoad_researcher.assistant.v2.experiment.candidate_control import CandidateLaunchInput, CandidateLaunchResult
from autoad_researcher.assistant.v2.experiment.candidate_proposal import (
    CandidateInterventionDraft,
    CandidateProposal,
    CandidateProposalDraft,
    CandidateProposalService,
    CandidateProposalStore,
)
from autoad_researcher.benchmarks.hashing import canonical_sha256
from autoad_researcher.experiment.coordinator import IdeaCandidate
from autoad_researcher.experiment.evaluation_contract import EvaluationContract, EvaluationMetric, EvaluationResourceBudget
from autoad_researcher.experiment.executor_adapters import ExecutorAdapterEvidence, ExecutorEvaluationCommand
from autoad_researcher.experiment.executor_agent import ExecutorProposal
from autoad_researcher.experiment.executor_contracts import InterventionContract
from autoad_researcher.experiment.idea_tree import IdeaNode, IdeaTree
from autoad_researcher.experiment.patch_protocol import SearchReplaceEdit


NOW = "2026-07-23T00:00:00+00:00"


def _contract() -> EvaluationContract:
    return EvaluationContract(
        contract_id="evaluation_contract_000001",
        session_id="session_aaaaaaaaaaaaaaaa",
        revision=0,
        baseline_commit="a" * 40,
        dataset_identity="fixture-dataset",
        split_identity="fixture-split",
        b_dev_ref="splits/b_dev.json",
        b_test_ref="splits/b_test.json",
        metrics=[EvaluationMetric(name="score", direction="maximize", implementation_ref="metric.py")],
        primary_metric="score",
        aggregation="mean",
        seeds=[7],
        checkpoint_selection="fixed",
        resource_budget=EvaluationResourceBudget(max_wall_seconds=60, max_gpu_seconds=0),
        protected_paths=["splits/b_dev.json", "splits/b_test.json", "evaluate.py", "metric.py"],
    )


def _adapter() -> ExecutorAdapterEvidence:
    return ExecutorAdapterEvidence(
        adapter_id="generic_python",
        entrypoint="run.py",
        smoke_argv=["run.py"],
        metrics_output="metrics.json",
        allowed_paths=["model.py"],
        protected_paths=["evaluate.py", "metric.py"],
        evaluation_commands={
            "b_dev": ExecutorEvaluationCommand(args=["run.py", "--split", "**FROZEN_SPLIT**"], metrics_output="metrics.json", split_ref_arg_index=2),
            "b_test": ExecutorEvaluationCommand(args=["run.py", "--split", "**FROZEN_SPLIT**"], metrics_output="metrics.json", split_ref_arg_index=2),
        },
    )


def _draft() -> CandidateProposalDraft:
    return CandidateProposalDraft(
        mechanism="local peak weighting",
        hypothesis="local weighting improves score on concentrated defects",
        observable="score",
        research_axis="localized residuals",
        minimal_intervention="replace the global reduction with a bounded local peak",
        falsification="B_dev does not exceed the frozen comparison threshold",
        expected_cost="low",
        relationship_to_previous_ideas="first candidate after baseline",
        grounding=["baseline outcome"],
        intervention=CandidateInterventionDraft(
            target_modules=["model.py"],
            allowed_paths=["model.py"],
            allowed_parameters=["peak_window"],
            evaluation_invariants=["fixed evaluator and split"],
            time_budget=30,
        ),
        executor_proposal=ExecutorProposal(
            edits=[SearchReplaceEdit(path="model.py", search="return mean(residuals)\n", replace="return max(residuals)\n")],
            changed_symbols=["score"],
            confidence=0.8,
        ),
    )


def _idea(node_id: str = "idea_000001", revision: int = 1) -> IdeaTree:
    root = IdeaNode(node_id="idea_000000", is_root=True, depth=0, status="REVIEWED", children=[node_id], created_at=NOW, updated_at=NOW)
    child = IdeaNode(node_id=node_id, parent_id="idea_000000", depth=1, mechanism="local peak weighting", hypothesis="local weighting improves score on concentrated defects", observable="score", research_axis="localized residuals", minimal_intervention="replace the global reduction with a bounded local peak", falsification="B_dev does not exceed the frozen comparison threshold", relationship_to_previous_ideas="first candidate after baseline", expected_cost="low", created_at=NOW, updated_at=NOW)
    return IdeaTree(run_id="run_test", session_id="session_aaaaaaaaaaaaaaaa", nodes=[root, child], revision=revision, created_at=NOW, updated_at=NOW)


def _candidate(proposal_id: str = "proposal_0123456789abcdef") -> CandidateLaunchInput:
    return CandidateLaunchInput(
        intervention_contract=InterventionContract(
            idea_id="idea_000001",
            mechanism="local peak weighting",
            hypothesis="local weighting improves score on concentrated defects",
            target_modules=["model.py"],
            allowed_paths=["model.py"],
            forbidden_paths=["metric.py"],
            allowed_parameters=["peak_window"],
            time_budget=30,
        ),
        approved_proposal=_draft().executor_proposal,
        comparison_seed=7,
        idempotency_key=f"candidate:{proposal_id}",
    )


def _proposal() -> CandidateProposal:
    value = CandidateProposal(
        proposal_id="proposal_0123456789abcdef",
        run_id="run_test",
        session_id="session_aaaaaaaaaaaaaaaa",
        idempotency_key="ui-proposal:session_aaaaaaaaaaaaaaaa",
        status="pending_review",
        idea_node_id="idea_000001",
        idea_tree_revision=1,
        evaluation_contract_ref="experiments/evaluation_contracts/session_aaaaaaaaaaaaaaaa/evaluation_contract_000001.json",
        evaluation_contract_sha256=_contract().sha256,
        idea=IdeaCandidate(**_draft().model_dump(exclude={"intervention", "executor_proposal"})),
        candidate=_candidate(),
        content_sha256="0" * 64,
        created_at=NOW,
        updated_at=NOW,
    )
    return value.model_copy(update={"content_sha256": canonical_sha256(CandidateProposalStore._content(value))})


def test_store_replays_and_rejects_tampered_proposal(tmp_path: Path):
    store = CandidateProposalStore()
    value = _proposal()
    assert store.create(tmp_path, value) == value
    assert store.create(tmp_path, value) == value
    assert store.load(tmp_path, session_id=value.session_id, proposal_id=value.proposal_id) == value

    path = tmp_path / "experiments" / "candidate_proposals" / value.session_id / f"{value.proposal_id}.json"
    raw = path.read_text(encoding="utf-8").replace("local weighting improves score", "tampered wording changes the proposal")
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError, match="content hash"):
        store.load(tmp_path, session_id=value.session_id, proposal_id=value.proposal_id)


class _FakeSessionStore:
    def __init__(self, session):
        self.session = session

    def load(self, _run_dir, _session_id):
        return self.session


class _FakeTreeStore:
    def __init__(self, tree: IdeaTree):
        self.tree = tree

    def create_or_get(self, _run_dir, *, session_id):
        return self.tree, False

    def load(self, _run_dir, *, session_id):
        return self.tree


def _session():
    return SimpleNamespace(
        session_id="session_aaaaaaaaaaaaaaaa",
        status="READY_FOR_BASELINE",
        baseline_status="b_dev_completed",
        evaluation_contract_ref="experiments/evaluation_contracts/session_aaaaaaaaaaaaaaaa/evaluation_contract_000001.json",
        evaluation_contract_sha256=_contract().sha256,
        authorization=SimpleNamespace(execution_mode="approve_each_step"),
    )


def test_generation_persists_review_without_creating_attempt(tmp_path: Path, monkeypatch):
    tree = IdeaTree(run_id="run_test", session_id="session_aaaaaaaaaaaaaaaa", nodes=[IdeaNode(node_id="idea_000000", is_root=True, depth=0, status="REVIEWED", created_at=NOW, updated_at=NOW)], created_at=NOW, updated_at=NOW)
    service = CandidateProposalService(sessions=_FakeSessionStore(_session()), trees=_FakeTreeStore(tree))
    monkeypatch.setattr(service, "_require_generation_state", lambda *_args, **_kwargs: (_session(), _contract(), SimpleNamespace(model_dump=lambda mode="json": {"metrics": {"score": 0.2}}), _adapter()))
    monkeypatch.setattr(service, "_generation_context", lambda *_args, **_kwargs: {"fixture": True})

    class FakeCycle:
        def run(self, _run_dir, **_kwargs):
            return SimpleNamespace(tree=_idea())

    monkeypatch.setattr("autoad_researcher.assistant.v2.experiment.candidate_proposal.CompactCycleService", lambda: FakeCycle())
    result = service.generate(tmp_path, session_id=_session().session_id, idempotency_key="ui-proposal:test", provider=lambda _context: _draft(), model_profile="fixture")

    assert result.status == "created"
    assert result.proposal.status == "pending_review"
    assert result.proposal.idea_node_id == "idea_000001"
    assert result.proposal.attempt_id is None
    assert not (tmp_path / "attempts").exists()


def test_invalid_provider_output_does_not_create_idea_or_proposal(tmp_path: Path, monkeypatch):
    tree = IdeaTree(run_id="run_test", session_id="session_aaaaaaaaaaaaaaaa", nodes=[IdeaNode(node_id="idea_000000", is_root=True, depth=0, status="REVIEWED", created_at=NOW, updated_at=NOW)], created_at=NOW, updated_at=NOW)
    service = CandidateProposalService(sessions=_FakeSessionStore(_session()), trees=_FakeTreeStore(tree))
    monkeypatch.setattr(service, "_require_generation_state", lambda *_args, **_kwargs: (_session(), _contract(), SimpleNamespace(model_dump=lambda mode="json": {"metrics": {"score": 0.2}}), _adapter()))
    monkeypatch.setattr(service, "_generation_context", lambda *_args, **_kwargs: {"fixture": True})

    with pytest.raises(ValueError, match="at least one edit"):
        service.generate(
            tmp_path,
            session_id=_session().session_id,
            idempotency_key="ui-proposal:invalid",
            provider=lambda _context: _draft().model_copy(update={"executor_proposal": ExecutorProposal(edits=[], confidence=0.2)}),
            model_profile="fixture",
        )
    assert not (tmp_path / "experiments" / "ideas").exists()
    assert not (tmp_path / "experiments" / "candidate_proposals").exists()


def test_approval_is_idempotent_and_rejection_is_terminal(tmp_path: Path, monkeypatch):
    value = _proposal()
    store = CandidateProposalStore()
    store.create(tmp_path, value)
    session = _session()
    tree_store = _FakeTreeStore(_idea())
    service = CandidateProposalService(store=store, sessions=_FakeSessionStore(session), trees=tree_store)
    calls = 0

    class FakeCandidateControl:
        def start(self, _run_dir, *, session_id, value):
            nonlocal calls
            calls += 1
            return CandidateLaunchResult(status="queued", attempt={"attempt_id": "attempt_000001"}, pipeline_job={})

    monkeypatch.setattr("autoad_researcher.assistant.v2.experiment.candidate_proposal.CandidateControlService", lambda: FakeCandidateControl())
    first = service.approve(tmp_path, session_id=value.session_id, proposal_id=value.proposal_id)
    second = service.approve(tmp_path, session_id=value.session_id, proposal_id=value.proposal_id)
    assert first.status == "started"
    assert second.status == "reused"
    assert calls == 1

    rejected = _proposal().model_copy(update={"proposal_id": "proposal_fedcba9876543210", "content_sha256": "0" * 64})
    rejected = rejected.model_copy(update={"content_sha256": canonical_sha256(CandidateProposalStore._content(rejected))})
    store.create(tmp_path, rejected)
    service.reject(tmp_path, session_id=rejected.session_id, proposal_id=rejected.proposal_id)
    with pytest.raises(ValueError, match="rejected"):
        service.approve(tmp_path, session_id=rejected.session_id, proposal_id=rejected.proposal_id)
