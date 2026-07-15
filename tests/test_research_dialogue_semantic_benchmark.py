from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

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


def _passing_variants():
    shared = {
        "reply_transcript": [],
        "summary": {},
        "source_action_types": ["register_github_repo"],
        "boundary_violations": [],
        "evidence_checks": {},
        "passed": True,
    }
    return [
        MODULE.VariantResult(
            label="entity",
            kind="entity",
            user_input="entity",
            judge={"semantic_equivalent": True},
            **shared,
        ),
        MODULE.VariantResult(
            label="paraphrase_01",
            kind="paraphrase",
            user_input="p1",
            judge={"semantic_equivalent": True},
            **shared,
        ),
        MODULE.VariantResult(
            label="paraphrase_02",
            kind="paraphrase",
            user_input="p2",
            judge={"semantic_equivalent": True},
            **shared,
        ),
        MODULE.VariantResult(
            label="counterfactual_01",
            kind="counterfactual",
            user_input="counter",
            judge={"counterfactual_applied": True, "stale_constraints": []},
            **shared,
        ),
    ]


def test_corpus_has_exact_nine_case_release_shape():
    corpus = _corpus()

    assert len(corpus.cases) == 9
    assert len({case.case_id for case in corpus.cases}) == 9
    assert all(case.expected.expected_execution_mode == "plan_only" for case in corpus.cases)
    assert all(len(case.paraphrases) >= 5 for case in corpus.cases)
    assert all(case.counterfactuals for case in corpus.cases)


def test_variant_matrix_is_stable_and_uses_each_variant_contract():
    corpus = _corpus()
    first = MODULE.select_variant_matrix(corpus, seed=23, variant_limit=0)
    second = MODULE.select_variant_matrix(corpus, seed=23, variant_limit=0)

    assert first == second
    assert sum(len(plans) for plans in first.values()) == 36
    for case in corpus.cases:
        plans = first[case.case_id]
        assert [plan.kind for plan in plans] == [
            "entity",
            "paraphrase",
            "paraphrase",
            "counterfactual",
        ]
        assert plans[0].turns == [case.entity_variant]
        for plan in plans[1:3]:
            assert plan.turns[:-1] == case.turns
            assert plan.turns[-1] == plan.user_input
        assert plans[3].turns[:-1] == case.turns
        assert plans[3].turns[-1] == plans[3].user_input

    limited = MODULE.select_variant_matrix(corpus, seed=23, variant_limit=7)
    assert sum(len(plans) for plans in limited.values()) == 7
    assert limited == MODULE.select_variant_matrix(corpus, seed=23, variant_limit=7)


def test_variant_result_requires_typed_judgment_to_match_passed_flag():
    with pytest.raises(ValueError, match="cannot pass"):
        MODULE.VariantResult(
            label="entity",
            kind="entity",
            user_input="entity",
            reply_transcript=[],
            summary={},
            source_action_types=[],
            boundary_violations=[],
            evidence_checks={},
            judge=None,
            passed=True,
        )

    with pytest.raises(ValueError, match="conflicts"):
        MODULE.VariantResult(
            label="counterfactual_01",
            kind="counterfactual",
            user_input="counter",
            reply_transcript=[],
            summary={},
            source_action_types=[],
            boundary_violations=[],
            evidence_checks={},
            judge={"counterfactual_applied": False, "stale_constraints": []},
            passed=True,
        )


def test_deterministic_boundary_gate_needs_no_judge(tmp_path: Path):
    case = _corpus().cases[0]
    clean = MODULE._collect_deterministic_state(
        case,
        run_dir=tmp_path,
        source_action_types=case.expected.expected_source_action_types,
        evidence=[],
    )
    assert clean.hard_failures == []
    assert clean.source_action_matches is True

    (tmp_path / "experiments" / "sessions").mkdir(parents=True)
    violated = MODULE._collect_deterministic_state(
        case,
        run_dir=tmp_path,
        source_action_types=[],
        evidence=[],
    )
    assert violated.hard_failures == ["plan_only_boundary_violated"]
    assert violated.source_action_matches is False


def test_run_case_skips_semantic_judge_after_hard_boundary_failure(monkeypatch, tmp_path: Path):
    case = _corpus().cases[0]

    def fake_dialogue_turn(run_dir, user_input, **kwargs):
        (run_dir / "experiments" / "sessions").mkdir(parents=True, exist_ok=True)
        return SimpleNamespace(created_sources=[{"kind": "github_repo"}])

    def fail_judge(*args, **kwargs):
        raise AssertionError("semantic Judge must not run after a hard deterministic failure")

    monkeypatch.setattr(MODULE, "_dialogue_turn", fake_dialogue_turn)
    monkeypatch.setattr(MODULE, "_judge_case", fail_judge)
    observation = MODULE.run_case(
        case,
        run_dir=tmp_path / "case",
        api_key="sk-test",
        provider_url="https://provider.test",
        model="dialogue-model",
        judge_model="judge-model",
        variant_plans=[],
    )

    assert observation.judge is None
    assert observation.deterministic_failures == ["plan_only_boundary_violated"]


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
                variant_results=_passing_variants(),
                semantic_stability=1.0,
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
    assert report["variant_count"] == 36
    assert report["variant_failure_count"] == 0

    same_model_report = MODULE.score_report(
        corpus,
        observations,
        judge_independent=False,
    )
    assert same_model_report["release_gate_passed"] is False
    assert same_model_report["judge_independent"] is False

    failed_variant = observations[0].variant_results[0].model_copy(
        update={
            "judge": MODULE.VariantJudgeObservation(semantic_equivalent=False),
            "passed": False,
        }
    )
    observations[0] = observations[0].model_copy(update={
        "variant_results": [failed_variant, *observations[0].variant_results[1:]],
        "semantic_stability": 0.75,
    })
    unstable_report = MODULE.score_report(corpus, observations)
    assert unstable_report["release_gate_passed"] is False
    assert unstable_report["variant_failure_count"] == 1


def test_judge_prompt_uses_only_case_specific_prohibitions(monkeypatch):
    case = next(item for item in _corpus().cases if item.case_id == "case09_cross_domain_negative")
    captured: dict[str, object] = {}

    def fake_call(api_key, provider_url, messages, **kwargs):
        captured["messages"] = messages
        captured["temperature"] = kwargs.get("temperature")
        captured["max_tokens"] = kwargs.get("max_tokens")
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
    assert captured["max_tokens"] == 4096


def test_variant_judge_uses_typed_equivalence_and_counterfactual_fields(monkeypatch):
    case = _corpus().cases[0]
    plans = MODULE._case_variant_plans(case, seed=0)
    replies = iter([
        {
            "semantic_equivalent": True,
            "counterfactual_applied": None,
            "stale_constraints": [],
            "rationale": "same intent structure",
        },
        {
            "semantic_equivalent": None,
            "counterfactual_applied": True,
            "stale_constraints": [],
            "rationale": "correction applied",
        },
    ])
    captured_messages = []

    def fake_call(api_key, provider_url, messages, **kwargs):
        captured_messages.append(messages)
        return {"reply": json.dumps(next(replies)), "error": ""}

    monkeypatch.setattr(MODULE, "call_research_chat", fake_call)
    entity = MODULE._judge_variant(
        case,
        plan=plans[0],
        base_summary=MODULE.ResearchIntentSummary(goal="base"),
        variant_summary=MODULE.ResearchIntentSummary(goal="entity"),
        transcript=[],
        evidence=[],
        api_key="sk-test",
        provider_url="https://provider.test",
        model="judge-model",
        temperature=0.0,
        budget=None,
    )
    counter = MODULE._judge_variant(
        case,
        plan=plans[-1],
        base_summary=MODULE.ResearchIntentSummary(goal="base"),
        variant_summary=MODULE.ResearchIntentSummary(goal="updated"),
        transcript=[],
        evidence=[],
        api_key="sk-test",
        provider_url="https://provider.test",
        model="judge-model",
        temperature=0.0,
        budget=None,
    )

    assert entity.semantic_equivalent is True
    assert counter.counterfactual_applied is True
    assert counter.stale_constraints == []
    entity_system = captured_messages[0][0]["content"]
    entity_payload = json.loads(captured_messages[0][1]["content"])
    assert "never require facts or constraints absent" in entity_system
    assert "necessary clarification about placeholders is allowed" in entity_system
    assert entity_payload["base_summary"] is None
    counter_payload = json.loads(captured_messages[1][1]["content"])
    assert counter_payload["base_summary"]["goal"] == "base"


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
        variant_seed=17,
        variant_limit=4,
        judge_call_limit=12,
        wall_time_limit_seconds=90.0,
        created_at="2026-07-15T00:00:00+00:00",
    )

    assert manifest.dialogue_model == "dialogue-model"
    assert manifest.judge_model == "judge-model"
    assert manifest.judge_independent is True
    assert manifest.provider_host == "api.example.test"
    assert manifest.prompt_id == "assistant.research_dialogue.v2"
    assert manifest.prompt_version == "v2"
    assert len(manifest.prompt_sha256) == 64
    assert manifest.corpus_sha256 == MODULE._sha256_file(corpus_path)
    assert manifest.dialogue_temperature == 0.0
    assert manifest.judge_temperature == 0.0
    assert manifest.variant_seed == 17
    assert manifest.variant_limit == 4
    assert manifest.variant_count == 4
    assert sum(len(labels) for labels in manifest.selected_variants.values()) == 4

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
        variant_seed=0,
        variant_limit=0,
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


def test_main_writes_manifest_and_per_case_variant_artifacts_quietly(
    monkeypatch,
    tmp_path: Path,
    capsys,
):
    def fake_run_case(case, *, variant_plans, **kwargs):
        kwargs["run_dir"].mkdir(parents=True, exist_ok=False)
        variants = []
        for plan in variant_plans:
            judge = (
                {"counterfactual_applied": True, "stale_constraints": []}
                if plan.kind == "counterfactual"
                else {"semantic_equivalent": True}
            )
            variants.append(MODULE.VariantResult(
                label=plan.label,
                kind=plan.kind,
                user_input=plan.user_input,
                reply_transcript=[],
                summary={},
                source_action_types=case.expected.expected_source_action_types,
                boundary_violations=[],
                evidence_checks={},
                judge=judge,
                passed=True,
            ))
        return MODULE.CaseRuntimeObservation(
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
            },
            variant_results=variants,
            semantic_stability=1.0 if variants else 0.0,
        )

    monkeypatch.setattr(MODULE, "run_case", fake_run_case)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://provider.test")
    monkeypatch.setattr(sys, "argv", [
        "bench_research_dialogue.py",
        "--runs-root",
        str(tmp_path),
        "--model",
        "dialogue-model",
        "--judge-model",
        "judge-model",
        "--variant-limit",
        "1",
    ])

    assert MODULE.main() == 1
    output = capsys.readouterr().out.strip().splitlines()
    assert len(output) == 1
    assert output[0].startswith("FAIL (")
    assert "[semantic] running" not in output[0]
    suite_dir = next(tmp_path.iterdir())
    assert (suite_dir / "semantic_run_manifest.json").is_file()
    completed = suite_dir / "completed_observations"
    assert len(list(completed.glob("*_variants.json"))) == 9


def test_main_persists_partial_report_on_case_runtime_failure(
    monkeypatch,
    tmp_path: Path,
    capsys,
):
    def fail_run_case(*args, **kwargs):
        raise RuntimeError("private detail")

    monkeypatch.setattr(MODULE, "run_case", fail_run_case)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://provider.test")
    monkeypatch.setattr(sys, "argv", [
        "bench_research_dialogue.py",
        "--runs-root",
        str(tmp_path),
        "--model",
        "dialogue-model",
        "--judge-model",
        "judge-model",
    ])

    assert MODULE.main() == 1
    output = capsys.readouterr().out
    assert "case_runtime_error:case01_patchcore_reproduction" in output
    assert "private detail" not in output
    suite_dir = next(tmp_path.iterdir())
    report = json.loads((suite_dir / "semantic_acceptance_report.json").read_text())
    assert report["run_failure"] == "case_runtime_error:case01_patchcore_reproduction"
    assert report["release_gate_passed"] is False


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


def test_invalid_response_diagnostics_do_not_include_response_content(monkeypatch):
    case = _corpus().cases[0]
    secret_response = "private-provider-response-without-an-object"
    monkeypatch.setattr(
        MODULE,
        "call_research_chat",
        lambda *args, **kwargs: {"reply": secret_response, "error": ""},
    )

    with pytest.raises(RuntimeError) as exc_info:
        MODULE._judge_case(
            case,
            transcript=[],
            summary=MODULE.ResearchIntentSummary(goal="goal"),
            evidence=[],
            api_key="sk-test",
            provider_url="https://provider.test",
            model="judge-model",
        )

    diagnostic = str(exc_info.value)
    assert f"length={len(secret_response)}" in diagnostic
    assert "contains_object_start=False" in diagnostic
    assert secret_response not in diagnostic


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
