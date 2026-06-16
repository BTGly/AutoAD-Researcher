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
        if hasattr(task, k) and k != "baseline":
            setattr(task, k, v)
    # baseline goes directly to input_task for user_provided flow
    if "baseline" in overrides:
        task.baseline = overrides["baseline"]
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
        assert q.answer_type == "single_choice"

        m = next(m for m in result.missing_information if m.item_id == "missing_baseline")
        assert len(m.references) >= 1
        assert any(r.locator == "baseline_methods" for r in m.references)

    def test_dataset_missing_suggested_from_paper(self):
        ctx = _make_context(baseline="PatchCore", compute_budget="single GPU")
        result = RuleBasedIntentClarifierBackend().clarify(context=ctx)

        assert result.dataset is None
        q = next(q for q in result.questions if q.missing_item_id == "missing_dataset")
        assert q.options == ["MVTec AD", "VisA"]
        assert q.answer_type == "single_choice"

    def test_metrics_hint_from_paper(self):
        ctx = _make_context(baseline="PatchCore")
        result = RuleBasedIntentClarifierBackend().clarify(context=ctx)

        q = next(q for q in result.questions if q.missing_item_id == "missing_metrics")
        assert q.options == ["image AUROC"]
        assert q.answer_type == "multiple_choice"

        m = next(m for m in result.missing_information if m.item_id == "missing_metrics")
        assert any(r.locator == "metrics" for r in m.references)

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

    def test_user_provided_baseline_decision(self):
        ctx = _make_context(baseline="UniAD")
        result = RuleBasedIntentClarifierBackend().clarify(context=ctx)

        assert result.baseline == "UniAD"
        assert result.baseline_decision is not None
        assert result.baseline_decision.source == "user_provided"
        assert result.baseline_candidates == []
        # No baseline question when already provided
        assert not any(q.missing_item_id == "missing_baseline" for q in result.questions)

    def test_paper_only_candidate(self):
        ctx = ClarificationContext(
            run_id="run_demo",
            task=InputTask(run_id="run_demo", request="迁移方法", source_ids=["paper_main"]),
            paper_summary=PaperSummary(
                run_id="run_demo", source_id="paper_main",
                research_problem="x", core_idea="y",
                compared_methods=["PaDiM"],
            ),
            repo_summary=None,
        )
        result = RuleBasedIntentClarifierBackend().clarify(context=ctx)

        assert result.baseline is None
        assert len(result.baseline_candidates) == 1
        c = result.baseline_candidates[0]
        assert c.value == "PaDiM"
        assert all(e.source == "paper_mentioned" for e in c.evidence)

    def test_repo_paper_merge(self):
        ctx = ClarificationContext(
            run_id="run_demo",
            task=InputTask(run_id="run_demo", request="迁移方法", source_ids=["p", "r"]),
            paper_summary=PaperSummary(
                run_id="run_demo", source_id="p",
                research_problem="x", core_idea="y",
                compared_methods=["PatchCore"],
            ),
            repo_summary=RepositorySummary(
                run_id="run_demo", source_id="r",
                baseline_methods=["PatchCore"],
            ),
        )
        result = RuleBasedIntentClarifierBackend().clarify(context=ctx)

        assert len(result.baseline_candidates) == 1
        c = result.baseline_candidates[0]
        assert c.value == "PatchCore"
        sources = {e.source for e in c.evidence}
        assert sources == {"repo_detected", "paper_mentioned"}

    def test_blank_methods_filtered(self):
        ctx = ClarificationContext(
            run_id="run_demo",
            task=InputTask(run_id="run_demo", request="x", source_ids=["r"]),
            repo_summary=RepositorySummary(
                run_id="run_demo", source_id="r",
                baseline_methods=["PatchCore", "", "  "],
            ),
        )
        result = RuleBasedIntentClarifierBackend().clarify(context=ctx)

        assert len(result.baseline_candidates) == 1
        assert result.baseline_candidates[0].value == "PatchCore"
