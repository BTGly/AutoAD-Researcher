"""Safe deterministic fallback when no configured narrative model is available."""

from autoad_researcher.reporting.facts import ExperimentReportFactsV1
from autoad_researcher.reporting.narrative import (
    NarrativeParagraphV1,
    NarrativeSectionV1,
    NarrativeSectionsV1,
    StructuredClaimV1,
)

NARRATIVE_TEMPLATE_VERSION = "deterministic-v2"
NARRATIVE_MODEL_PROFILE = "deterministic"


def build_default_narrative(facts: ExperimentReportFactsV1) -> NarrativeSectionsV1:
    return NarrativeSectionsV1(
        sections=[
            NarrativeSectionV1(section_id="summary", paragraphs=[NarrativeParagraphV1(paragraph_id="summary_background", paragraph_kind="background", prose_template="本报告仅汇总已冻结的实验控制面事实。", claim_ids=["claim_summary"])]),
            NarrativeSectionV1(section_id="interpretation", paragraphs=[NarrativeParagraphV1(paragraph_id="interpretation_scope", paragraph_kind="interpretation", prose_template="科学解释受 OutcomeCard 与 ScientificAssessment 的已记录状态约束，未新增任何指标或比较结论。", claim_ids=["claim_interpretation"])]),
            NarrativeSectionV1(section_id="limitations", paragraphs=[NarrativeParagraphV1(paragraph_id="limitations_uncertainties", paragraph_kind="limitation", prose_template="已记录的不确定性：{{fact:uncertainties}}", claim_ids=["claim_limitations"])]),
            NarrativeSectionV1(section_id="next_steps", paragraphs=[NarrativeParagraphV1(paragraph_id="next_steps_recommendation", paragraph_kind="recommendation", prose_template="后续行动须基于用户确认的 Proposal；本报告不会创建实验任务。", claim_ids=["claim_next_steps"])]),
        ]
        ,
        claims=[
            StructuredClaimV1(claim_id="claim_summary", claim_kind="explanation", statement_template="报告只使用冻结事实。"),
            StructuredClaimV1(claim_id="claim_interpretation", claim_kind="explanation", statement_template="解释受已记录科学评估约束。"),
            StructuredClaimV1(claim_id="claim_limitations", claim_kind="limitation", statement_template="不确定性为：{{fact:uncertainties}}", fact_refs=["uncertainties"]),
            StructuredClaimV1(claim_id="claim_next_steps", claim_kind="recommendation", statement_template="后续行动需要用户确认。"),
        ],
    )
