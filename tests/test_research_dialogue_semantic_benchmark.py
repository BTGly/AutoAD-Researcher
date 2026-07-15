from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from autoad_researcher.assistant.v2.source_actions import plan_explicit_source_actions


PROJECT_ROOT = Path(__file__).parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "bench_research_dialogue.py"
SPEC = importlib.util.spec_from_file_location("bench_research_dialogue", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def _corpus():
    return MODULE.load_corpus(
        PROJECT_ROOT / "configs" / "benchmarks" / "research_semantic_cases_v1.json"
    )


def test_corpus_has_exact_nine_case_release_shape():
    corpus = _corpus()

    assert len(corpus.cases) == 9
    assert len({case.case_id for case in corpus.cases}) == 9
    assert all(case.expected.expected_execution_mode == "plan_only" for case in corpus.cases)
    assert all(len(case.paraphrases) >= 5 for case in corpus.cases)
    assert all(case.counterfactuals for case in corpus.cases)


def test_all_case_urls_use_deterministic_repository_actions():
    for case in _corpus().cases:
        plan = plan_explicit_source_actions(
            user_input=case.source_url,
            attachments=None,
        )

        assert plan is not None
        assert [action.action_type for action in plan.actions] == ["register_github_repo"]
        assert plan.actions[0].source_url == case.source_url


def test_case_turns_are_absent_from_production_dialogue_prompt():
    prompt_source = (
        PROJECT_ROOT
        / "src"
        / "autoad_researcher"
        / "assistant"
        / "v2"
        / "research_dialogue_agent.py"
    ).read_text(encoding="utf-8")

    for case in _corpus().cases:
        for turn in case.turns:
            assert turn not in prompt_source
        assert case.entity_variant not in prompt_source


def test_score_requires_average_minimum_and_zero_vetoes():
    corpus = _corpus()
    observations = []
    for case in corpus.cases:
        observations.append(
            MODULE.CaseRuntimeObservation(
                case_id=case.case_id,
                reply_transcript=[],
                summary={},
                source_action_types=case.expected.expected_source_action_types,
                experiment_session_created=False,
                experiment_jobs_created=False,
                code_modified=False,
                evidence_checks={},
                judge={
                    "operation_targets": case.expected.required_operation_targets,
                    "advisory_commitments": [],
                    "conflict_topics": case.expected.required_conflict_topics,
                    "execution_mode": "plan_only",
                    "blocking_question_appropriate": True,
                    "veto_failures": [],
                    "rationale": "synthetic scorer contract test",
                },
            )
        )

    report = MODULE.score_report(corpus, observations)

    assert report["release_gate_passed"] is True
    assert report["average_score"] == 100.0
    assert report["minimum_score"] == 100.0
    assert report["veto_failure_count"] == 0

    same_model_report = MODULE.score_report(
        corpus,
        observations,
        judge_independent=False,
    )
    assert same_model_report["release_gate_passed"] is False
    assert same_model_report["judge_independent"] is False


def test_judge_prompt_uses_only_case_specific_prohibitions(monkeypatch):
    case = next(item for item in _corpus().cases if item.case_id == "case09_cross_domain_negative")
    captured: dict[str, object] = {}

    def fake_call(api_key, provider_url, messages, **kwargs):
        captured["messages"] = messages
        captured["temperature"] = kwargs.get("temperature")
        return {
            "reply": json.dumps({
                "operation_targets": case.expected.required_operation_targets,
                "advisory_commitments": [],
                "conflict_topics": case.expected.required_conflict_topics,
                "execution_mode": "plan_only",
                "blocking_question_appropriate": True,
                "veto_failures": [],
                "rationale": "case-specific rubric",
            }),
            "error": "",
        }

    monkeypatch.setattr(MODULE, "call_research_chat", fake_call)
    MODULE._judge_case(
        case,
        transcript=[],
        summary=MODULE.ResearchIntentSummary(goal="先检查跨领域兼容性"),
        evidence=[],
        api_key="sk-test",
        provider_url="https://provider.test",
        model="judge-model",
        temperature=0.0,
    )

    system = captured["messages"][0]["content"]
    payload = json.loads(captured["messages"][1]["content"])
    for case_shaped_term in (
        "fusion architecture",
        "patch serialization",
        "parallel encoder",
        "loss combination",
        "score combination",
    ):
        assert case_shaped_term not in system
    assert payload["prohibited_advisory_commitments"] == case.expected.prohibited_advisory_commitments
    assert captured["temperature"] == 0.0


def test_manifest_fingerprints_prompt_corpus_models_and_provider(tmp_path: Path):
    corpus_path = PROJECT_ROOT / "configs" / "benchmarks" / "research_semantic_cases_v1.json"
    manifest = MODULE.build_run_manifest(
        corpus=_corpus(),
        corpus_path=corpus_path,
        dialogue_model="dialogue-model",
        judge_model="judge-model",
        provider_url="https://api.example.test/v1",
        dialogue_temperature=0.0,
        judge_temperature=0.0,
        judge_call_limit=12,
        wall_time_limit_seconds=90.0,
        created_at="2026-07-15T00:00:00+00:00",
    )

    assert manifest.dialogue_model == "dialogue-model"
    assert manifest.judge_model == "judge-model"
    assert manifest.judge_independent is True
    assert manifest.provider_host == "api.example.test"
    assert manifest.prompt_id == "assistant.research_dialogue.v1"
    assert manifest.prompt_version == "v1"
    assert len(manifest.prompt_sha256) == 64
    assert manifest.corpus_sha256 == MODULE._sha256_file(corpus_path)
    assert manifest.dialogue_temperature == 0.0
    assert manifest.judge_temperature == 0.0
    assert manifest.variant_count == 0

    path = MODULE.ensure_suite_manifest(tmp_path, manifest, resuming=False)
    assert path.name == "semantic_run_manifest.json"
    assert MODULE.SemanticRunManifest.model_validate_json(path.read_text()) == manifest
    assert not path.with_suffix(".json.tmp").exists()


def test_resume_rejects_manifest_fingerprint_drift(tmp_path: Path):
    corpus_path = PROJECT_ROOT / "configs" / "benchmarks" / "research_semantic_cases_v1.json"
    manifest = MODULE.build_run_manifest(
        corpus=_corpus(),
        corpus_path=corpus_path,
        dialogue_model="dialogue-model",
        judge_model="judge-model",
        provider_url="https://api.example.test",
        dialogue_temperature=0.0,
        judge_temperature=0.0,
        judge_call_limit=0,
        wall_time_limit_seconds=0.0,
    )
    MODULE.ensure_suite_manifest(tmp_path, manifest, resuming=False)

    changed = manifest.model_copy(update={"judge_model": "different-judge"})
    with pytest.raises(ValueError, match="fingerprint"):
        MODULE.ensure_suite_manifest(tmp_path, changed, resuming=True)


def test_model_environment_priority_and_run_budget(monkeypatch):
    monkeypatch.setenv("AUTOAD_DIALOGUE_MODEL", "dialogue-from-env")
    monkeypatch.setenv("AUTOAD_JUDGE_MODEL", "judge-from-env")
    args = MODULE.build_parser().parse_args([])
    override = MODULE.build_parser().parse_args(["--judge-model", "judge-from-cli"])

    assert args.model == "dialogue-from-env"
    assert args.judge_model == "judge-from-env"
    assert override.judge_model == "judge-from-cli"

    budget = MODULE.SemanticRunBudget(judge_call_limit=1, wall_time_limit_seconds=0)
    budget.reserve_judge_call()
    with pytest.raises(MODULE.SemanticBudgetExceeded, match="judge_call_limit_exceeded"):
        budget.reserve_judge_call()


def test_judge_boolean_map_variant_is_normalized_without_name_guessing():
    normalized = MODULE._normalize_judge_collections(
        {
            "operation_targets": {"research_goal": True, "dataset": False},
            "advisory_commitments": {},
            "conflict_topics": ["repository compatibility"],
            "veto_failures": {"repository_conflict_ignored": False},
        }
    )

    assert normalized == {
        "operation_targets": ["research_goal"],
        "advisory_commitments": [],
        "conflict_topics": ["repository compatibility"],
        "veto_failures": [],
    }


def test_judge_explanation_map_variant_uses_only_explicit_keys():
    normalized = MODULE._normalize_judge_collections(
        {
            "operation_targets": {
                "research_goal": "preserved in summary goal",
                "research_object": "preserved in confirmed facts",
            },
            "advisory_commitments": [],
            "conflict_topics": {},
            "veto_failures": {},
        }
    )

    assert normalized["operation_targets"] == ["research_goal", "research_object"]
    assert normalized["conflict_topics"] == []


def test_judge_json_parser_accepts_one_complete_object_with_transport_text():
    assert MODULE._parse_json_object(
        'analysis omitted\n{"operation_targets": [], "execution_mode": "plan_only"}\ndone'
    ) == {
        "operation_targets": [],
        "execution_mode": "plan_only",
    }


def test_next_case_run_dir_preserves_incomplete_attempts(tmp_path: Path):
    (tmp_path / "case08_deepspeed_feasibility").mkdir()
    (tmp_path / "case08_deepspeed_feasibility_retry_01").mkdir()

    assert MODULE._next_case_run_dir(
        tmp_path,
        "case08_deepspeed_feasibility",
    ) == tmp_path / "case08_deepspeed_feasibility_retry_02"


def test_judge_rejects_zero_targets_for_nonempty_summary():
    observation = MODULE.SemanticJudgeObservation(
        operation_targets=[],
        advisory_commitments=[],
        conflict_topics=[],
        execution_mode="plan_only",
        blocking_question_appropriate=True,
        veto_failures=[],
    )

    assert MODULE._judge_observation_is_consistent(
        observation,
        MODULE.ResearchIntentSummary(goal="明确研究目标"),
    ) is False
    assert MODULE._judge_observation_is_consistent(
        observation,
        MODULE.ResearchIntentSummary(),
    ) is True
