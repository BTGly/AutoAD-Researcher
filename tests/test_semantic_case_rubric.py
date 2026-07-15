from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from autoad_researcher.assistant.v2.mutation_protocol import INTENT_MUTATION_TARGETS
from autoad_researcher.assistant.v2.source_action_planner import plan_explicit_source_actions


PROJECT_ROOT = Path(__file__).parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "bench_semantic_cases.py"
SPEC = importlib.util.spec_from_file_location("bench_semantic_cases", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _corpus():
    return MODULE.load_corpus(
        PROJECT_ROOT / "configs" / "benchmarks" / "research_semantic_cases_v1.json"
    )


def _perfect_observation(case):
    expected = case.expected
    return MODULE.SemanticCaseObservation(
        case_id=case.case_id,
        operation_targets=expected.required_operation_targets,
        advisory_commitments=[],
        conflict_topics=expected.required_conflict_topics,
        pending_confirmation=expected.expected_pending_confirmation,
        execution_mode=expected.expected_execution_mode,
        source_action_types=expected.expected_source_action_types,
    )


def test_nine_case_rubric_has_paraphrase_counterfactual_and_entity_coverage():
    corpus = _corpus()

    assert len(corpus.cases) == 9
    assert len({case.case_id for case in corpus.cases}) == 9
    assert all(len(case.paraphrases) >= 5 for case in corpus.cases)
    assert sum(len(case.paraphrases) for case in corpus.cases) >= 45
    assert all(case.counterfactuals for case in corpus.cases)
    assert all(case.entity_variant for case in corpus.cases)
    assert {case.expected.expected_pending_confirmation for case in corpus.cases} == {True, False}
    assert all(case.expected.expected_execution_mode == "plan_only" for case in corpus.cases)
    pending_cases = {
        case.case_id for case in corpus.cases if case.expected.expected_pending_confirmation
    }
    assert pending_cases == {
        "case01_patchcore_reproduction",
        "case04_time_series_anomaly",
        "case05_kernelbench",
    }
    assert all(
        set(case.expected.required_operation_targets) <= INTENT_MUTATION_TARGETS
        for case in corpus.cases
    )


def test_nine_case_repository_urls_use_structural_repository_intake():
    for case in _corpus().cases:
        plan = plan_explicit_source_actions(
            user_input=case.source_url,
            attachments=None,
            source_registry=[],
        )
        assert plan is not None
        assert [action.action_type for action in plan.actions] == ["register_github_repo"]
        assert plan.actions[0].source_url == case.source_url


def test_nine_case_turns_are_not_copied_into_production_prompt_source():
    prompt_source = (
        PROJECT_ROOT / "src" / "autoad_researcher" / "assistant" / "prompt_registry.py"
    ).read_text(encoding="utf-8")

    for case in _corpus().cases:
        for turn in case.turns:
            assert turn not in prompt_source
        assert case.entity_variant not in prompt_source


def test_semantic_release_gate_requires_each_case_average_and_no_vetoes():
    corpus = _corpus()
    report = MODULE.SemanticObservationReport(
        schema_version=1,
        cases=[_perfect_observation(case) for case in corpus.cases],
    )

    scored = MODULE.score_report(corpus, report)

    assert scored["release_gate_passed"] is True
    assert scored["minimum_score"] == 100.0
    assert scored["average_score"] == 100.0
    assert scored["veto_failure_count"] == 0


def test_semantic_release_gate_fails_missing_fields_and_plan_only_violation():
    corpus = _corpus()
    observations = [_perfect_observation(case) for case in corpus.cases]
    observations[0] = observations[0].model_copy(update={
        "operation_targets": ["research_goal"],
        "experiment_session_created": True,
    })
    report = MODULE.SemanticObservationReport(schema_version=1, cases=observations)

    scored = MODULE.score_report(corpus, report)
    first = scored["results"][0]

    assert scored["release_gate_passed"] is False
    assert first["score"] < 85.0
    assert "plan_only_boundary_violated" in first["veto_failures"]
    assert "research_object" in first["missing_operation_targets"]


def test_semantic_release_gate_rejects_unknown_or_missing_case_records():
    corpus = _corpus()
    observations = [_perfect_observation(case) for case in corpus.cases]

    missing_report = MODULE.SemanticObservationReport(schema_version=1, cases=observations[:-1])
    try:
        MODULE.score_report(corpus, missing_report)
    except ValueError as exc:
        assert "missing cases" in str(exc)
    else:
        raise AssertionError("missing semantic case must be rejected")

    unknown = observations[0].model_copy(update={"case_id": "case99_unknown"})
    unknown_report = MODULE.SemanticObservationReport(schema_version=1, cases=[*observations, unknown])
    try:
        MODULE.score_report(corpus, unknown_report)
    except ValueError as exc:
        assert "unknown cases" in str(exc)
    else:
        raise AssertionError("unknown semantic case must be rejected")
