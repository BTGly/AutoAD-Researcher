"""Bounded, report-local deep-read tools for Discussion."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.reporting.digest import ReportDigest
from autoad_researcher.reporting.evidence import EvidenceIndex
from autoad_researcher.reporting.facts import ExperimentReportFactsV1
from autoad_researcher.reporting.snapshot import resolve_run_relative_file, sha256_file

ToolName = Literal[
    "get_report_digest", "get_report_section", "list_attempts", "get_outcome_card",
    "get_scientific_assessment", "get_metrics", "get_patch_diff", "search_log",
    "read_log_range", "get_evaluation_contract", "get_environment_snapshot",
    "get_champion", "get_budget_usage", "resolve_evidence",
]


class ReportToolCall(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ToolName
    arguments: dict[str, Any] = Field(default_factory=dict)


TOOL_CATALOG = {
    "get_report_digest": "Read the frozen report digest.",
    "get_report_section": "Read one numbered Markdown section from the frozen report.",
    "list_attempts": "List frozen attempt status summaries.",
    "get_outcome_card": "Read one Attempt's frozen OutcomeCard projection by attempt_id.",
    "get_scientific_assessment": "Read one Attempt's frozen ScientificAssessment by attempt_id.",
    "get_metrics": "Read one Attempt's registered metrics by attempt_id.",
    "get_patch_diff": "Read a registered patch diff by evidence_id when available.",
    "search_log": "Search a registered text-log evidence item by evidence_id and query.",
    "read_log_range": "Read a bounded line range from registered text-log evidence.",
    "get_evaluation_contract": "Read the frozen evaluation contract.",
    "get_environment_snapshot": "Read frozen environment summary.",
    "get_champion": "Read frozen candidate and Champion facts.",
    "get_budget_usage": "Read frozen cognitive and compute usage facts.",
    "resolve_evidence": "Resolve one registered evidence_id to its SHA-bound metadata.",
}

MAX_TOOL_CALLS = 4
MAX_LOG_RESULTS = 20
MAX_LOG_LINES = 120
MAX_TEXT_BYTES = 48_000


def execute_tools(run_dir, *, report_id: str, calls: list[ReportToolCall]) -> list[dict[str, Any]]:
    if len(calls) > MAX_TOOL_CALLS:
        raise ValueError("report discussion requested too many typed tools")
    facts, evidence, digest, markdown = _context(run_dir, report_id)
    return [
        {"name": call.name, "arguments": call.arguments, "result": _execute(run_dir, call, facts, evidence, digest, markdown)}
        for call in calls
    ]


def _context(run_dir, report_id: str):
    directory = run_dir / "reports" / report_id
    return (
        ExperimentReportFactsV1.model_validate_json((directory / "report_facts.json").read_text(encoding="utf-8")),
        EvidenceIndex.model_validate_json((directory / "evidence_index.json").read_text(encoding="utf-8")),
        ReportDigest.model_validate_json((directory / "report_digest.json").read_text(encoding="utf-8")),
        (directory / "report.md").read_text(encoding="utf-8"),
    )


def _execute(run_dir, call: ReportToolCall, facts, evidence, digest, markdown: str) -> dict[str, Any]:
    name, args = call.name, call.arguments
    if name == "get_report_digest":
        return digest.model_dump(mode="json")
    if name == "get_report_section":
        return _section(markdown, args.get("section"))
    if name == "list_attempts":
        return {"attempts": [_attempt_summary(item) for item in facts.attempts]}
    if name in {"get_outcome_card", "get_scientific_assessment", "get_metrics"}:
        attempt = _attempt(facts, args)
        key = {"get_outcome_card": "outcome", "get_scientific_assessment": "assessment", "get_metrics": "attempt_metrics"}[name]
        value = attempt.get(key)
        if name == "get_metrics" and value is None:
            value = (attempt.get("outcome") or {}).get("metrics") if isinstance(attempt.get("outcome"), dict) else None
        return {"attempt_id": attempt["attempt_id"], "value": value, "status": "available" if value is not None else "unavailable"}
    if name == "get_evaluation_contract":
        return facts.evaluation_contract
    if name == "get_environment_snapshot":
        return facts.repository_and_environment
    if name == "get_champion":
        return facts.candidate_and_champion
    if name == "get_budget_usage":
        return {"cognitive_cost_summary": facts.cognitive_cost_summary, "compute_resource_summary": facts.compute_resource_summary}
    if name == "resolve_evidence":
        entry = _evidence(evidence, args)
        return entry.model_dump(mode="json")
    if name == "get_patch_diff":
        return _text_evidence(run_dir, evidence, args, expected=("patch", "diff"), query=None, start=None, end=None)
    if name == "search_log":
        return _text_evidence(run_dir, evidence, args, expected=("log",), query=args.get("query"), start=None, end=None)
    if name == "read_log_range":
        return _text_evidence(run_dir, evidence, args, expected=("log",), query=None, start=args.get("start_line"), end=args.get("end_line"))
    raise ValueError("unsupported report discussion tool")


def _attempt(facts, args: dict[str, Any]) -> dict[str, Any]:
    attempt_id = args.get("attempt_id")
    if not isinstance(attempt_id, str):
        raise ValueError("typed Attempt tools require attempt_id")
    result = next((item for item in facts.attempts if item.get("attempt_id") == attempt_id), None)
    if result is None:
        raise ValueError("unknown frozen Attempt")
    return result


def _attempt_summary(item: dict[str, Any]) -> dict[str, Any]:
    outcome = item.get("outcome") if isinstance(item.get("outcome"), dict) else {}
    assessment = item.get("assessment") if isinstance(item.get("assessment"), dict) else {}
    return {"attempt_id": item.get("attempt_id"), "runtime_status": item.get("runtime_status"), "execution_status": outcome.get("execution_status"), "evaluation_status": assessment.get("evaluation_status"), "scientific_effect": assessment.get("scientific_effect")}


def _evidence(index: EvidenceIndex, args: dict[str, Any]):
    evidence_id = args.get("evidence_id")
    if not isinstance(evidence_id, str):
        raise ValueError("typed Evidence tools require evidence_id")
    entry = next((item for item in index.entries if item.evidence_id == evidence_id), None)
    if entry is None:
        raise ValueError("unknown registered Evidence")
    return entry


def _text_evidence(run_dir, index: EvidenceIndex, args, *, expected: tuple[str, ...], query, start, end) -> dict[str, Any]:
    entry = _text_entry(index, args)
    kind = entry.evidence_kind.lower()
    if not any(value in kind for value in expected):
        return {"status": "unavailable", "reason": "requested evidence is not a registered compatible text artifact"}
    path = resolve_run_relative_file(run_dir, entry.artifact_ref.locator)
    if sha256_file(path) != entry.artifact_ref.sha256:
        raise ValueError("registered text Evidence SHA-256 no longer matches")
    text = path.read_bytes()[:MAX_TEXT_BYTES].decode("utf-8", errors="replace")
    lines = text.splitlines()
    if query is not None:
        if not isinstance(query, str) or not query:
            raise ValueError("search_log requires a non-empty query")
        matches = [{"line": index + 1, "text": line} for index, line in enumerate(lines) if query in line][:MAX_LOG_RESULTS]
        return {"status": "available", "evidence_id": entry.evidence_id, "matches": matches, "truncated": len(path.read_bytes()) > MAX_TEXT_BYTES}
    first = 1 if start is None else start
    last = min(len(lines), first + MAX_LOG_LINES - 1) if end is None else end
    if not isinstance(first, int) or not isinstance(last, int) or first < 1 or last < first or last - first >= MAX_LOG_LINES:
        raise ValueError("read_log_range requires a bounded positive line range")
    return {"status": "available", "evidence_id": entry.evidence_id, "lines": [{"line": index + 1, "text": line} for index, line in enumerate(lines[first - 1:last])], "truncated": len(path.read_bytes()) > MAX_TEXT_BYTES}


def _text_entry(index: EvidenceIndex, args: dict[str, Any]):
    if isinstance(args.get("evidence_id"), str):
        return _evidence(index, args)
    attempt_id = args.get("attempt_id")
    stream = args.get("stream", "stdout")
    if not isinstance(attempt_id, str) or stream not in {"stdout", "stderr"}:
        raise ValueError("text tools require evidence_id or attempt_id with stream")
    kind = f"attempt_{stream}_log"
    entry = next((item for item in index.entries if item.evidence_kind == kind and item.attempt_id == attempt_id), None)
    if entry is None:
        raise ValueError("no registered text Evidence for the requested Attempt stream")
    return entry


def _section(markdown: str, number: object) -> dict[str, Any]:
    if not isinstance(number, int) or number < 1 or number > 12:
        raise ValueError("section must be a report section number from 1 through 12")
    pattern = re.compile(rf"^## {number}\. .*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)
    match = pattern.search(markdown)
    return {"section": number, "content": match.group(0)[:MAX_TEXT_BYTES] if match else "", "status": "available" if match else "unavailable"}
