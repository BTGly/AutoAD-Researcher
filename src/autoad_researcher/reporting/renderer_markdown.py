"""Deterministic Markdown assembly from validated report artifacts."""

from __future__ import annotations

from autoad_researcher.reporting.facts import ExperimentReportFactsV1
from autoad_researcher.reporting.narrative import NarrativeSectionsV1


def render_markdown(*, facts: ExperimentReportFactsV1, narrative: NarrativeSectionsV1) -> str:
    sections = {item.section_id: item for item in narrative.sections}
    lines = ["# 研究报告", "", "## 1. 研究摘要", "", sections["summary"].text, ""]
    lines.extend(["## 2. 研究目标与约束", "", f"- Task: `{facts.research_objective.get('task_ref') or 'unknown'}`", ""])
    lines.extend(["## 3. 实验配置", "", f"- Session: `{facts.session_id}`", ""])
    lines.extend(["## 4. Baseline 与 Champion", "", _rows(facts.baseline), ""])
    lines.extend(["## 5. 探索的假设", "", _rows(facts.ideas), ""])
    lines.extend(["## 6. 执行结果", "", _rows(facts.attempts), ""])
    lines.extend(["## 7. 量化结果", "", _rows(facts.primary_metrics), ""])
    lines.extend(["## 8. 失败与不可比较实验", "", _rows([*facts.failed_attempts, *facts.non_comparable_attempts]), ""])
    lines.extend(["## 9. 科学解释", "", sections["interpretation"].text, ""])
    lines.extend(["## 10. 局限与不确定性", "", sections["limitations"].text, *[f"- {item}" for item in facts.uncertainties], ""])
    lines.extend(["## 11. 建议的下一步", "", sections["next_steps"].text, ""])
    lines.extend(["## 12. 证据与制品引用", "", *[f"- `{ref.artifact_id}`: `{ref.locator}`" for ref in facts.source_refs], ""])
    return "\n".join(lines)


def _rows(items: list[dict]) -> str:
    if not items:
        return "- 无可用记录。"
    return "\n".join(f"- `{item.get('attempt_id') or item.get('node_id') or 'record'}`: `{item.get('runtime_status') or item.get('status') or 'available'}`" for item in items)
