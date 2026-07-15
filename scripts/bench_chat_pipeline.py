#!/usr/bin/env python3
"""Run an observational live benchmark of the V2 chat pipeline.

The benchmark records model-call topology, timing, and semantic-oracle results.
It deliberately does not enforce a global per-turn call limit or a live-model
CI threshold: users may choose to spend their provider budget on ordinary
conversation or future multi-step reasoning.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.assistant.v2.event_service import load_events_since
from autoad_researcher.assistant.v2.llm_trace_service import TRACE_DIR, TRACE_INDEX
from autoad_researcher.assistant.v2.source_action_planner import SourceActionType
from autoad_researcher.server.main import app
from autoad_researcher.server.routes import chat as chat_route


DEFAULT_CASES = Path("configs/benchmarks/chat_pipeline_cases_v1.json")


class BenchmarkTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    user_input: str = Field(min_length=1)
    transcript_tail: list[dict[str, Any]] = Field(default_factory=list)
    attachments: list[str] = Field(default_factory=list)
    expected_router: bool
    expected_turn_type: Literal[
        "contract_update",
        "contract_confirmation",
        "contract_question",
        "source_intake",
        "ordinary_chat",
        "joke",
        "frustration",
        "identity_question",
        "ambiguous",
    ]
    expected_contract_action: Literal[
        "update_contract",
        "confirm_contract",
        "answer_without_contract_update",
        "ask_clarifying_question",
    ]
    expected_confirmation_action: Literal["none", "suspend", "resume", "supersede"]
    expected_task_profile: Literal[
        "empirical_model_research",
        "systems_optimization",
        "code_diagnosis",
        "general_research",
    ]
    expected_source_action_types: list[SourceActionType]
    expected_contract_mutation: bool


class BenchmarkCorpus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    cases: list[BenchmarkTurn] = Field(min_length=1)


def load_corpus(path: Path) -> BenchmarkCorpus:
    payload = json.loads(path.read_text(encoding="utf-8"))
    corpus = BenchmarkCorpus.model_validate(payload)
    case_ids = [case.case_id for case in corpus.cases]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("benchmark case_id values must be unique")
    return corpus


def load_trace_records(run_dir: Path) -> list[dict[str, Any]]:
    trace_path = run_dir / TRACE_DIR / TRACE_INDEX
    if not trace_path.is_file():
        return []
    return [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def summarize_case(
    *,
    case: BenchmarkTurn,
    status_code: int,
    elapsed_ms: float,
    first_progress_ms: float | None,
    traces: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    router_traces = [trace for trace in traces if trace.get("call_site") == "conversation_router"]
    legacy_traces = [
        trace
        for trace in traces
        if trace.get("call_site") in {"source_action_planner", "turn_gate"}
    ]
    first_router = router_traces[0] if router_traces else None
    schema_status = first_router.get("schema_validation") if first_router else None
    route_events = [
        event for event in events if event.get("type") == "planner.conversation_route.decided"
    ]
    route_payload = route_events[0].get("payload", {}) if len(route_events) == 1 else {}
    semantic_actual = {
        "expected_turn_type": route_payload.get("turn_type"),
        "expected_contract_action": route_payload.get("contract_action"),
        "expected_confirmation_action": route_payload.get("confirmation_action_proposal"),
        "expected_task_profile": route_payload.get("task_profile_proposal"),
        "expected_source_action_types": sorted(set(route_payload.get("action_types") or [])),
        "expected_contract_mutation": any(
            event.get("type") == "contract.draft.updated" for event in events
        ),
    }
    semantic_expected = {
        "expected_turn_type": case.expected_turn_type,
        "expected_contract_action": case.expected_contract_action,
        "expected_confirmation_action": case.expected_confirmation_action,
        "expected_task_profile": case.expected_task_profile,
        "expected_source_action_types": sorted(set(case.expected_source_action_types)),
        "expected_contract_mutation": case.expected_contract_mutation,
    }
    semantic_mismatches = [
        {
            "field": field.removeprefix("expected_"),
            "expected": expected,
            "actual": semantic_actual[field],
        }
        for field, expected in semantic_expected.items()
        if semantic_actual[field] != expected
    ]
    return {
        "case_id": case.case_id,
        "status_code": status_code,
        "elapsed_ms": round(elapsed_ms, 3),
        "first_progress_ms": round(first_progress_ms, 3) if first_progress_ms is not None else None,
        "model_call_count": len(traces),
        "call_sites": [str(trace.get("call_site") or "") for trace in traces],
        "queue_wait_ms": [trace.get("queue_wait_ms") for trace in traces],
        "fallback_reasons": [
            str(trace.get("fallback_reason") or "")
            for trace in traces
            if trace.get("fallback_reason")
        ],
        "router_expected": case.expected_router,
        "router_call_count": len(router_traces),
        "router_first_schema_status": schema_status,
        "router_first_schema_topology_success": (
            len(router_traces) == 1 and schema_status in {"ok", "recovered"}
            if case.expected_router else len(router_traces) == 0
        ),
        "route_event_count": len(route_events),
        "semantic_oracle_success": len(route_events) == 1 and not semantic_mismatches,
        "semantic_mismatches": semantic_mismatches,
        "legacy_semantic_planner_calls": len(legacy_traces),
    }


def summarize_run(results: list[dict[str, Any]]) -> dict[str, Any]:
    router_cases = [result for result in results if result["router_expected"]]
    router_successes = sum(
        1 for result in router_cases if result["router_first_schema_topology_success"]
    )
    route_successes = sum(
        1 for result in results if result["router_first_schema_topology_success"]
    )
    semantic_successes = sum(1 for result in results if result["semantic_oracle_success"])
    return {
        "schema_version": 2,
        "case_count": len(results),
        "http_success_count": sum(1 for result in results if result["status_code"] == 200),
        "router_case_count": len(router_cases),
        "router_first_schema_topology_success_count": router_successes,
        "router_first_schema_topology_success_rate": (
            router_successes / len(router_cases) if router_cases else None
        ),
        "route_first_schema_topology_success_count": route_successes,
        "route_first_schema_topology_success_rate": (
            route_successes / len(results) if results else None
        ),
        "semantic_oracle_case_count": len(results),
        "semantic_oracle_success_count": semantic_successes,
        "semantic_oracle_success_rate": semantic_successes / len(results) if results else None,
        "legacy_semantic_planner_call_count": sum(
            result["legacy_semantic_planner_calls"] for result in results
        ),
        "observed_model_call_count": sum(result["model_call_count"] for result in results),
        "results": results,
    }


async def run_live_benchmark(
    corpus: BenchmarkCorpus,
    *,
    api_key: str,
    provider_url: str,
    model: str,
    concurrency: int,
) -> dict[str, Any]:
    progress_times: dict[str, float] = {}
    started_times: dict[str, float] = {}
    original_broadcast = chat_route.manager.broadcast
    original_runs_root = chat_route.RUNS_ROOT

    async def capture_broadcast(run_id: str, message: dict[str, Any]) -> None:
        if message.get("type") == "assistant.progress" and run_id not in progress_times:
            progress_times[run_id] = time.perf_counter()

    chat_route.manager.broadcast = capture_broadcast
    try:
        with tempfile.TemporaryDirectory(prefix="autoad_chat_bench_") as temp_dir:
            runs_root = Path(temp_dir) / "runs"
            runs_root.mkdir()
            chat_route.RUNS_ROOT = str(runs_root)
            semaphore = asyncio.Semaphore(max(1, concurrency))
            transport = httpx.ASGITransport(app=app)
            headers = {
                "X-AutoAD-API-Key": api_key,
                "X-AutoAD-Base-URL": provider_url,
                "X-AutoAD-Model": model,
            }

            async with httpx.AsyncClient(transport=transport, base_url="http://benchmark") as client:
                async def run_case(index: int, case: BenchmarkTurn) -> dict[str, Any]:
                    async with semaphore:
                        run_id = f"run_bench_{index:03d}_{case.case_id}"
                        run_dir = runs_root / run_id
                        run_dir.mkdir()
                        started_times[run_id] = time.perf_counter()
                        response = await client.post(
                            "/api/chat/send",
                            headers=headers,
                            json={
                                "run_id": run_id,
                                "user_input": case.user_input,
                                "attachments": case.attachments,
                                "transcript_tail": case.transcript_tail,
                            },
                        )
                        finished = time.perf_counter()
                        first_progress = progress_times.get(run_id)
                        return summarize_case(
                            case=case,
                            status_code=response.status_code,
                            elapsed_ms=(finished - started_times[run_id]) * 1000,
                            first_progress_ms=(
                                (first_progress - started_times[run_id]) * 1000
                                if first_progress is not None else None
                            ),
                            traces=load_trace_records(run_dir),
                            events=load_events_since(run_dir),
                        )

                results = await asyncio.gather(*[
                    run_case(index, case) for index, case in enumerate(corpus.cases, start=1)
                ])
    finally:
        chat_route.RUNS_ROOT = original_runs_root
        chat_route.manager.broadcast = original_broadcast

    return summarize_run(results)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--model", default="deepseek-v4-flash")
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true", help="Validate and summarize the corpus without model calls.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    corpus = load_corpus(args.cases)
    if args.dry_run:
        print(json.dumps({
            "schema_version": corpus.schema_version,
            "case_count": len(corpus.cases),
            "router_case_count": sum(1 for case in corpus.cases if case.expected_router),
            "semantic_oracle_case_count": len(corpus.cases),
        }, ensure_ascii=False, sort_keys=True))
        return 0

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    provider_url = os.environ.get("DEEPSEEK_BASE_URL", "")
    if not api_key or not provider_url:
        raise SystemExit("DEEPSEEK_API_KEY and DEEPSEEK_BASE_URL are required for the live benchmark")
    report = asyncio.run(run_live_benchmark(
        corpus,
        api_key=api_key,
        provider_url=provider_url,
        model=args.model,
        concurrency=max(1, args.concurrency),
    ))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
