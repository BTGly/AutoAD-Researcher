"""Safe deterministic fallback when no configured narrative model is available."""

from autoad_researcher.reporting.facts import ExperimentReportFactsV1
from autoad_researcher.reporting.narrative import NarrativeSectionV1, NarrativeSectionsV1


def build_default_narrative(facts: ExperimentReportFactsV1) -> NarrativeSectionsV1:
    uncertainty = "；".join(facts.uncertainties) or "未记录额外不确定性。"
    return NarrativeSectionsV1(
        sections=[
            NarrativeSectionV1(section_id="summary", text="本报告仅汇总已冻结的实验控制面事实。", claim_kind="explanation"),
            NarrativeSectionV1(section_id="interpretation", text="科学解释受 OutcomeCard 与 ScientificAssessment 的已记录状态约束，未新增任何指标或比较结论。", claim_kind="explanation"),
            NarrativeSectionV1(section_id="limitations", text=uncertainty, claim_kind="limitation"),
            NarrativeSectionV1(section_id="next_steps", text="后续行动须基于用户确认的 Proposal；本报告不会创建实验任务。", claim_kind="recommendation"),
        ]
    )
