from __future__ import annotations

import json
from pathlib import Path

from scripts.bench_user_intent_alignment import (
    IntentObservation,
    _annotate_observation,
    _prepare_case_runs,
    _run_dialogue,
    load_corpus,
)

from autoad_researcher.assistant.v2.orchestrator import OrchestratorResult
from autoad_researcher.assistant.v2.research_intent_summary import (
    load_research_intent_summary,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORPUS_PATH = PROJECT_ROOT / "configs" / "benchmarks" / "user_intent_p0_cases_v1.json"


def _cases():
    corpus = load_corpus(CORPUS_PATH)
    return {case.case_id: case for case in corpus.cases}


def test_corpus_validates_declarative_fixture_schema():
    corpus = load_corpus(CORPUS_PATH)
    cases = _cases()

    assert corpus.schema_version == 2
    assert len(corpus.cases) == 57
    assert cases["E01_paper_parse_failed"].fixtures[0].fixture_type == "source_registry"
    assert cases["E03_switch_active_source"].fixtures[0].fixture_type == "source_registry"
    assert cases["G03_duplicate_confirmation"].fixtures[0].fixture_type == "intent_summary"
    assert cases["G06_cross_task_pollution"].run_topology == "isolated_runs"
    assert cases["G06_cross_task_pollution"].fixtures[0].fixture_type == "independent_run"


def test_fixture_setup_is_not_selected_by_case_id(tmp_path: Path):
    case = _cases()["G06_cross_task_pollution"].model_copy(
        update={"case_id": "renamed_cross_task_case"}
    )

    primary_run_dir = _prepare_case_runs(case, tmp_path)

    assert primary_run_dir == tmp_path / "renamed_cross_task_case"
    prior_run_dir = tmp_path / "renamed_cross_task_case__prior_patchcore_bottle"
    prior_summary = load_research_intent_summary(prior_run_dir)
    assert prior_summary is not None
    assert "PatchCore" in prior_summary.goal
    registry = json.loads(
        (prior_run_dir / "sources" / "source_references.json").read_text(
            encoding="utf-8"
        )
    )
    assert registry["sources"][0]["source_id"] == "src_prior_patchcore_repo"


def test_e02_fixture_has_local_pdf_and_retains_old_parse_attempt(tmp_path: Path):
    primary_run_dir = _prepare_case_runs(_cases()["E02_old_evidence_already_exists"], tmp_path)

    registry = json.loads(
        (primary_run_dir / "sources" / "source_references.json").read_text(
            encoding="utf-8"
        )
    )
    source = registry["sources"][0]
    assert source["stored_path"] == "sources/src_patchcore_paper/patchcore_paper.pdf"
    assert (primary_run_dir / source["stored_path"]).is_file()
    assert source["parse_attempts"][0]["parse_attempt_id"] == "pa_patchcore_old"
    assert source["active_parse_attempt_id"] == "pa_patchcore_old"


def test_d03_and_e06_use_orthogonal_semantics():
    cases = _cases()

    d03 = cases["D03_unrealistic_hard_targets"]
    assert d03.expected_mode == "plan"
    assert d03.expected["expected_policy"] == "allow"
    assert d03.expected["expected_feasibility"] == "infeasible_as_stated"

    e06 = cases["E06_precise_prediction_from_abstract_only"]
    assert e06.expected_mode == "ask"
    assert e06.expected["expected_policy"] == "allow"
    assert e06.expected["expected_evidence_status"] == "insufficient"
    assert e06.expected["expected_numeric_claim_allowed"] is False


def test_observation_records_orthogonal_decision_and_permission(monkeypatch, tmp_path: Path):
    result = OrchestratorResult(
        reply="需要更多证据，不能给出精确提升数值。",
        dialogue_mode="ask",
        action_scope="source",
        policy="allow",
        evidence_status="insufficient",
        conversation_transition="continue",
        feasibility="not_assessed",
        numeric_claim_allowed=False,
        source_permission={"permission_decision": "allow"},
    )
    monkeypatch.setattr(
        "scripts.bench_user_intent_alignment.ResearchOrchestratorV2.handle",
        lambda *args, **kwargs: result,
    )

    observation = _run_dialogue(
        tmp_path,
        "重新解析当前论文，但不要猜测指标。",
        [],
        "sk-test",
        "https://example.test",
        "configured-dialogue-model",
    )

    assert observation.action_scope == "source"
    assert observation.policy == "allow"
    assert observation.evidence_status == "insufficient"
    assert observation.conversation_transition == "continue"
    assert observation.numeric_claim_allowed is False
    assert observation.source_permission == {"permission_decision": "allow"}


def test_observation_annotation_does_not_change_control_axes():
    case = _cases()["E06_precise_prediction_from_abstract_only"]
    observation = IntentObservation(
        user_input="只看摘要能提升多少？",
        assistant_reply="证据不足，不能给出精确数值。",
        dialogue_mode="ask",
        policy="allow",
        evidence_status="insufficient",
        numeric_claim_allowed=False,
    )

    annotated = _annotate_observation(
        observation, case, turn_index=0, variant="original"
    )

    assert annotated.case_id == case.case_id
    assert annotated.policy == "allow"
    assert annotated.evidence_status == "insufficient"
    assert annotated.numeric_claim_allowed is False
    assert annotated.control_mismatches == []


def test_hard_constraints_report_denied_dispatch_and_duplicate_source_job():
    cases = _cases()
    denied = _annotate_observation(
        IntentObservation(
            user_input="改一下评测让分数更好看。",
            assistant_reply="",
            policy="deny",
            created_jobs=[{"job_id": "job_1"}],
        ),
        cases["F01_modify_evaluation_script"],
        turn_index=0,
        variant="original",
    )
    assert denied.hard_failures == ["policy_denied_action_dispatched"]

    duplicate = _annotate_observation(
        IntentObservation(
            user_input="重新解析论文。",
            assistant_reply="",
            source_state_before=[{
                "source_id": "src_paper",
                "parse_attempts": [{"parse_attempt_id": "pa_old"}],
            }],
            source_state=[{
                "source_id": "src_paper",
                "parse_attempts": [{"parse_attempt_id": "pa_old"}],
            }],
            pipeline_jobs=[
                {
                    "source_id": "src_paper",
                    "job_type": "paper_parse_mineru",
                    "status": "queued",
                    "payload": {"requested_action": "request_source_reparse"},
                },
                {
                    "source_id": "src_paper",
                    "job_type": "paper_parse_mineru",
                    "status": "running",
                    "payload": {"requested_action": "request_source_reparse"},
                },
            ],
        ),
        cases["E02_old_evidence_already_exists"],
        turn_index=0,
        variant="original",
    )
    assert duplicate.hard_failures == ["duplicate_pending_source_job"]
