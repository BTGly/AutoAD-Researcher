"""Deterministic Markdown assembly from validated report artifacts."""

from __future__ import annotations

import json

from autoad_researcher.reporting.facts import ExperimentReportFactsV1
from autoad_researcher.reporting.narrative import NarrativeSectionsV1
from autoad_researcher.reporting.validator import resolve_fact

MARKDOWN_RENDERER_VERSION = "v2"


def render_markdown(*, facts: ExperimentReportFactsV1, narrative: NarrativeSectionsV1, evidence=None) -> str:
    sections = {item.section_id: item for item in narrative.sections}
    claims = {item.claim_id: item for item in narrative.claims}
    lines = ["# 研究报告", "", "## 1. 研究摘要", "", _section_text(facts, sections["summary"], claims), ""]
    lines.extend(["## 2. 研究目标与约束", "", f"- Task: `{facts.research_objective.get('task_ref') or 'unknown'}`", ""])
    lines.extend(["## 3. 实验配置", "", f"- Session: `{facts.session_id}`", ""])
    lines.extend(["## 4. Baseline 与 Champion", "", _attempt_table(facts.baseline), "", _champion_table(facts.candidate_and_champion), ""])
    lines.extend(["## 5. 探索的假设", "", _rows(facts.ideas), ""])
    lines.extend(["## 6. 执行结果", "", _attempt_table(facts.attempts), ""])
    lines.extend(["## 7. 量化结果", "", "### Primary", "", _metric_table(facts.primary_metrics), "", "### Guardrails", "", _metric_table(facts.guardrail_metrics), ""])
    lines.extend(["## 8. 失败与不可比较实验", "", _failure_table([*facts.failed_attempts, *facts.non_comparable_attempts]), ""])
    lines.extend(["## 9. 科学解释", "", _section_text(facts, sections["interpretation"], claims), ""])
    lines.extend(["## 10. 局限与不确定性", "", _section_text(facts, sections["limitations"], claims), ""])
    lines.extend(["## 11. 建议的下一步", "", _section_text(facts, sections["next_steps"], claims), ""])
    lines.extend(["## 12. 证据与制品引用", "", *_evidence_lines(evidence, facts), ""])
    return "\n".join(lines)


def _rows(items: list[dict]) -> str:
    if not items:
        return "- 无可用记录。"
    return "\n".join(f"- `{item.get('attempt_id') or item.get('node_id') or 'record'}`: `{item.get('runtime_status') or item.get('status') or 'available'}`" for item in items)


def _attempt_table(items: list[dict]) -> str:
    if not items:
        return "- 无可用记录。"
    rows = []
    for item in items:
        outcome = item.get("outcome") if isinstance(item.get("outcome"), dict) else {}
        assessment = item.get("assessment") if isinstance(item.get("assessment"), dict) else {}
        failure = item.get("failure_classification") if isinstance(item.get("failure_classification"), dict) else {}
        rows.append([
            item.get("attempt_id", ""), item.get("attempt_purpose", ""), item.get("runtime_status", ""),
            outcome.get("execution_status", ""), assessment.get("evaluation_status", ""),
            assessment.get("scientific_effect", ""), assessment.get("primary_delta", ""),
            failure.get("failure_code", item.get("failure_code", "")),
            item.get("execution_result_binding", {}).get("status", "") if isinstance(item.get("execution_result_binding"), dict) else "",
        ])
    return _table(["Attempt", "Purpose", "Runtime", "Execution", "Validity", "Scientific", "Primary delta", "Failure", "Execution evidence"], rows)


def _metric_table(items: list[dict]) -> str:
    if not items:
        return "- 无可用记录。"
    return _table(["Attempt", "Metric", "Value"], [[item.get("attempt_id", ""), item.get("metric", ""), item.get("value", "")] for item in items])


def _champion_table(champion: dict) -> str:
    candidates = champion.get("candidates") if isinstance(champion, dict) else None
    if not isinstance(candidates, list) or not candidates:
        return "- 未记录 Champion。"
    rows = [[item.get("candidate_id", ""), item.get("attempt_id", ""), item.get("status", ""), _display(item.get("primary_metric_value"))] for item in candidates if isinstance(item, dict)]
    return _table(["Candidate", "Attempt", "Status", "Primary metric"], rows)


def _failure_table(items: list[dict]) -> str:
    if not items:
        return "- 无失败或不可比较实验。"
    rows = []
    seen = set()
    for item in items:
        attempt_id = item.get("attempt_id", "")
        if attempt_id in seen:
            continue
        seen.add(attempt_id)
        outcome = item.get("outcome") if isinstance(item.get("outcome"), dict) else {}
        assessment = item.get("assessment") if isinstance(item.get("assessment"), dict) else {}
        failure = item.get("failure_classification") if isinstance(item.get("failure_classification"), dict) else {}
        rows.append([attempt_id, item.get("runtime_status", ""), assessment.get("evaluation_status", ""), failure.get("failure_code", item.get("failure_code", "")), _display(outcome.get("protocol_errors", ""))])
    return _table(["Attempt", "Runtime", "Validity", "Failure", "Protocol errors"], rows)


def _table(headers: list[str], rows: list[list[object]]) -> str:
    return "\n".join([
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
        *("| " + " | ".join(_display(value) for value in row) + " |" for row in rows),
    ])


def _display(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value).replace("|", "\\|").replace("\n", " ")


def _section_text(facts: ExperimentReportFactsV1, section, claims) -> str:
    values = []
    for paragraph in section.paragraphs:
        if paragraph.paragraph_kind in {"interpretation", "limitation"}:
            values.extend(_render_template(facts, claims[claim_id].statement_template) for claim_id in paragraph.claim_ids)
        else:
            values.append(_render_template(facts, paragraph.prose_template))
    return "\n\n".join(values)


def _evidence_lines(evidence, facts: ExperimentReportFactsV1) -> list[str]:
    if evidence is None:
        return [f"- `{ref.artifact_id}`: `{ref.locator}`" for ref in facts.source_refs]
    return [f"- [{item.evidence_id}](#evidence-{item.evidence_id}): `{item.evidence_kind}`" for item in evidence.entries]


def _render_template(facts: ExperimentReportFactsV1, template: str) -> str:
    import re

    return re.sub(r"\{\{fact:([A-Za-z0-9_.-]+)\}\}", lambda match: resolve_fact(facts, match.group(1)), template)
