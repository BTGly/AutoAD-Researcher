"""测试 RuleBasedIntentClarifierBackend。"""

from autoad_researcher.clarifiers import RuleBasedIntentClarifierBackend
from autoad_researcher.schemas import (
    ClarificationContext,
    InputTask,
    PaperSummary,
    RepositorySummary,
)


def _make_context(**overrides):
    task = InputTask(
        run_id="run_demo",
        request="把论文模块迁移到异常检测",
        source_ids=["paper_main", "baseline_repo"],
    )
    for k, v in overrides.items():
        if hasattr(task, k):
            setattr(task, k, v)
    paper = PaperSummary(
        run_id="run_demo", source_id="paper_main",
        research_problem="representation learning",
        core_idea="multi-scale feature fusion",
        datasets=["MVTec AD", "VisA"],
        metrics=["image AUROC"],
    )
    repo = RepositorySummary(
        run_id="run_demo", source_id="baseline_repo",
        baseline_methods=["PatchCore", "PaDiM"],
        protected_paths=["eval.py"],
    )
    return ClarificationContext(
        run_id="run_demo",
        task=task,
        paper_summary=paper,
        repo_summary=repo,
    )


class TestRuleBasedClarifier:
    def test_full_input_no_baseline_questions(self):
        ctx = _make_context(
            target_domain="visual_anomaly_detection",
            baseline="PatchCore",
            dataset="MVTec AD",
            compute_budget="single GPU",
        )
        result = RuleBasedIntentClarifierBackend().clarify(context=ctx)

        assert result.baseline == "PatchCore"
        assert result.dataset == "MVTec AD"
        # metrics missing → non-blocking questions
        assert result.status == "has_nonblocking_questions"
        assert result.user_idea is None  # legit

    def test_target_domain_missing_blocking(self):
        ctx = _make_context(baseline="PatchCore")
        result = RuleBasedIntentClarifierBackend().clarify(context=ctx)

        assert result.status == "needs_blocking_input"
        blocking = [m for m in result.missing_information if m.blocking]
        assert any(m.field == "target_domain" for m in blocking)

    def test_baseline_missing_suggested_from_repo(self):
        ctx = _make_context(dataset="MVTec AD")
        result = RuleBasedIntentClarifierBackend().clarify(context=ctx)

        assert result.baseline is None
        q = next(q for q in result.questions if q.missing_item_id == "missing_baseline")
        assert q.options == ["PatchCore", "PaDiM"]

    def test_dataset_missing_suggested_from_paper(self):
        ctx = _make_context(baseline="PatchCore", compute_budget="single GPU")
        result = RuleBasedIntentClarifierBackend().clarify(context=ctx)

        assert result.dataset is None
        q = next(q for q in result.questions if q.missing_item_id == "missing_dataset")
        assert q.options == ["MVTec AD", "VisA"]

    def test_user_idea_missing_not_a_problem(self):
        ctx = _make_context(baseline="PatchCore")
        result = RuleBasedIntentClarifierBackend().clarify(context=ctx)

        assert result.user_idea is None
        assert not any(q.missing_item_id == "missing_user_idea" for q in result.questions)

    def test_known_facts_reference_artifacts(self):
        ctx = _make_context(baseline="PatchCore")
        result = RuleBasedIntentClarifierBackend().clarify(context=ctx)

        artifacts = {
            ref.artifact
            for fact in result.known_facts
            for ref in fact.references
        }
        assert "input_task.yaml" in artifacts
        assert "paper_summary.json" in artifacts
        assert "repo_summary.json" in artifacts

    def test_constraints_preserved(self):
        ctx = _make_context(baseline="PatchCore",
                           constraints=["不修改 evaluation script"])
        result = RuleBasedIntentClarifierBackend().clarify(context=ctx)

        assert "不修改 evaluation script" in result.constraints
