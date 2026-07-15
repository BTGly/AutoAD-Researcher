from __future__ import annotations

from pathlib import Path

from scripts.bench_user_intent_alignment import (
    _setup_confirmable_goal,
    load_corpus,
)

from autoad_researcher.assistant.v2.research_intent_summary import (
    load_research_intent_summary,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = PROJECT_ROOT / "configs" / "benchmarks" / "user_intent_p0_cases_v1.json"


def test_d04_covers_absent_and_registered_repository_states():
    corpus = load_corpus(CORPUS_PATH)
    cases = {case.case_id: case for case in corpus.cases}

    absent = cases["D04_user_unknown_code_details"]
    registered = cases["D04_registered_repo_unknown_code_details"]
    assert absent.source_url == ""
    assert registered.source_url == "https://github.com/amazon-science/patchcore-inspection"
    assert registered.setup_note is not None


def test_g03_setup_creates_the_required_goal_state(tmp_path: Path):
    _setup_confirmable_goal(tmp_path)

    summary = load_research_intent_summary(tmp_path)
    assert summary is not None
    assert summary.goal
    assert summary.blocking_question is None
    assert any("测试 mask" in fact for fact in summary.confirmed_facts)
