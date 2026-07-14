from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "bench_chat_pipeline.py"
SPEC = importlib.util.spec_from_file_location("bench_chat_pipeline", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_chat_benchmark_corpus_has_twenty_unique_valid_cases():
    corpus = MODULE.load_corpus(
        Path(__file__).parents[1] / "configs" / "benchmarks" / "chat_pipeline_cases_v1.json"
    )

    assert corpus.schema_version == 1
    assert len(corpus.cases) == 20
    assert len({case.case_id for case in corpus.cases}) == 20
    assert any(case.attachments for case in corpus.cases)
    assert any(not case.expected_router for case in corpus.cases)
    assert any(case.transcript_tail for case in corpus.cases)


def test_chat_benchmark_summary_observes_calls_without_global_call_cap():
    corpus = MODULE.load_corpus(
        Path(__file__).parents[1] / "configs" / "benchmarks" / "chat_pipeline_cases_v1.json"
    )
    case = corpus.cases[0]
    result = MODULE.summarize_case(
        case=case,
        status_code=200,
        elapsed_ms=123.4,
        first_progress_ms=1.2,
        traces=[
            {
                "call_site": "conversation_router",
                "schema_validation": "ok",
                "queue_wait_ms": 0.5,
                "fallback_reason": "",
            },
            {"call_site": "reply_planner", "queue_wait_ms": 0.1, "fallback_reason": ""},
            {"call_site": "future_user_selected_tool", "queue_wait_ms": 0.0, "fallback_reason": ""},
        ],
    )
    report = MODULE.summarize_run([result])

    assert result["model_call_count"] == 3
    assert result["router_call_count"] == 1
    assert result["legacy_semantic_planner_calls"] == 0
    assert report["observed_model_call_count"] == 3
    assert report["route_first_success_count"] == 1
    assert "model_call_limit" not in report


def test_live_benchmark_path_observes_progress_and_restores_global_routes(monkeypatch):
    original_runs_root = MODULE.chat_route.RUNS_ROOT

    def fake_call(api_key, provider_base_url, messages, **kwargs):
        system = messages[0]["content"]
        if "ConversationRouter" in system:
            return {
                "reply": json.dumps({
                    "turn_gate": {
                        "turn_type": "ordinary_chat",
                        "contract_action": "answer_without_contract_update",
                        "contract_update_allowed": False,
                        "need_discovery_allowed": False,
                        "save_draft_allowed": False,
                        "confirmation_action_proposal": "none",
                        "task_profile_proposal": "general_research",
                        "task_profile_evidence": None,
                        "requires_need_discovery_enrichment": False,
                        "suggested_task_title": None,
                        "suggested_task_summary": None,
                        "user_intent_summary": "ordinary chat",
                        "evidence_from_current_turn": [],
                        "evidence_from_context": [],
                        "confidence": 0.9,
                        "reason": "ordinary chat",
                        "next_reply_instruction": None,
                    },
                    "source_action_plan": {
                        "actions": [],
                        "user_visible_summary": "",
                        "confidence": 1.0,
                        "reason": "no source action",
                    },
                    "task_profile_proposal": "general_research",
                    "task_profile_evidence": None,
                    "suggested_task_title": None,
                    "suggested_task_summary": None,
                    "requires_need_discovery_enrichment": False,
                }, ensure_ascii=False),
                "error": "",
            }
        return {
            "reply": json.dumps({
                "reply_to_user": "可以继续聊天。",
                "contract_updates": {},
                "new_user_confirmed_fields": [],
                "missing_required_fields": [],
                "primary_metrics": [],
                "secondary_metrics": [],
                "metric_priority": None,
                "optional_hints_detected": {},
                "next_question": "",
                "ready_for_confirmation": False,
                "ready_for_experiment_agents": False,
            }, ensure_ascii=False),
            "error": "",
        }

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    corpus = MODULE.BenchmarkCorpus(cases=[
        MODULE.BenchmarkTurn(
            case_id="chat",
            user_input="随便聊聊",
            expected_router=True,
        ),
        MODULE.BenchmarkTurn(
            case_id="url",
            user_input="https://example.test/paper",
            expected_router=False,
        ),
    ])

    report = asyncio.run(MODULE.run_live_benchmark(
        corpus,
        api_key="sk-test",
        provider_url="https://example.test",
        model="test-model",
        concurrency=2,
    ))

    assert report["case_count"] == 2
    assert report["http_success_count"] == 2
    assert report["route_first_success_count"] == 2
    assert report["legacy_semantic_planner_call_count"] == 0
    assert all(result["first_progress_ms"] <= 300 for result in report["results"])
    assert MODULE.chat_route.RUNS_ROOT == original_runs_root
