from pathlib import Path

from autoad_researcher.experiment.cognitive_budget import CognitiveBudget, CognitiveBudgetStore, new_usage
from autoad_researcher.experiment.cost_summary import CognitiveCostSummaryBuilder


def test_cost_summary_is_rebuilt_from_existing_usage_ledger(tmp_path: Path):
    budget = CognitiveBudget(
        max_calls=4,
        max_tokens=100,
        max_compact_cycles=3,
        max_exploratory_cycles=1,
        max_subagent_calls=1,
        max_wall_seconds=20,
    )
    store = CognitiveBudgetStore()
    store.append(
        tmp_path,
        session_id="session",
        budget=budget,
        usage=new_usage(
            cycle_id="compact_1",
            cycle_kind="compact",
            role="coordinator",
            input_tokens=10,
            output_tokens=5,
            wall_seconds=2,
        ),
    )
    store.append(
        tmp_path,
        session_id="session",
        budget=budget,
        usage=new_usage(
            cycle_id="explore_1",
            cycle_kind="exploratory",
            role="idea_explorer",
            input_tokens=20,
            output_tokens=10,
            wall_seconds=3,
        ),
    )
    summary = CognitiveCostSummaryBuilder(store=store).build_and_persist(
        tmp_path,
        session_id="session",
        budget=budget,
    )
    assert summary.total_calls == 2
    assert summary.total_tokens == 45
    assert summary.compact_cycles == 1
    assert summary.exploratory_cycles == 1
    assert summary.coordinator_calls == 1
    assert summary.specialist_calls == 1
    assert summary.compact_to_exploratory_ratio == 1
    assert summary.remaining_calls == 2
    assert summary.remaining_tokens == 55
    assert summary.exceeded_limits == []
    assert (tmp_path / "experiments" / "cognition" / "session" / "cost_summary.json").is_file()


def test_cost_summary_reports_but_does_not_rewrite_budget_overage(tmp_path: Path):
    budget = CognitiveBudget(
        max_calls=0,
        max_tokens=0,
        max_compact_cycles=0,
        max_exploratory_cycles=0,
        max_subagent_calls=0,
        max_wall_seconds=0,
    )
    usage_path = tmp_path / "experiments" / "cognition" / "session" / "llm_usage.jsonl"
    usage_path.parent.mkdir(parents=True)
    usage_path.write_text(
        new_usage(
            cycle_id="compact_1",
            cycle_kind="compact",
            role="coordinator",
            input_tokens=1,
            output_tokens=1,
            wall_seconds=1,
        ).model_dump_json()
        + "\n",
        encoding="utf-8",
    )
    summary = CognitiveCostSummaryBuilder().build(tmp_path, session_id="session", budget=budget)
    assert set(summary.exceeded_limits) == {
        "max_calls",
        "max_tokens",
        "max_compact_cycles",
        "max_wall_seconds",
    }
    assert usage_path.read_text(encoding="utf-8").count("\n") == 1
