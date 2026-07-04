"""Prompt Registry for AutoAD Assistant prompt profiles.

The registry is intentionally static in this first implementation. It records
which prompt profile should be used for a state or product mode, but it does not
call an LLM and does not alter the existing Stage 3 pipeline.
"""

from __future__ import annotations

from collections.abc import Iterable

from autoad_researcher.assistant.prompt_io import PromptIOContract, PromptLayer
from autoad_researcher.assistant.prompt_profiles import (
    GLOBAL_INVARIANTS_PROMPT_ID,
    GLOBAL_INVARIANTS_TEXT,
    PromptProfile,
)
from autoad_researcher.ui.chat_prompts import (
    INTENT_CLARIFICATION_PROMPT,
    NEXT_EXPERIMENT_PROMPT,
    RUN_EXPLANATION_PROMPT,
)


class PromptRegistry:
    """In-memory registry for versioned prompt profiles."""

    def __init__(self, profiles: Iterable[PromptProfile] | None = None) -> None:
        self._profiles: dict[str, PromptProfile] = {}
        for profile in profiles or []:
            self.register(profile)

    def register(self, profile: PromptProfile) -> None:
        if profile.prompt_id in self._profiles:
            raise ValueError(f"duplicate prompt profile: {profile.prompt_id}")
        self._profiles[profile.prompt_id] = profile

    def get(self, prompt_id: str) -> PromptProfile | None:
        return self._profiles.get(prompt_id)

    def require(self, prompt_id: str) -> PromptProfile:
        profile = self.get(prompt_id)
        if profile is None:
            raise KeyError(f"unknown prompt profile: {prompt_id}")
        return profile

    def all_profiles(self) -> list[PromptProfile]:
        return sorted(self._profiles.values(), key=lambda profile: profile.prompt_id)

    def by_layer(self, layer: PromptLayer) -> list[PromptProfile]:
        return [profile for profile in self.all_profiles() if profile.layer == layer]

    def by_stage(self, stage: str) -> list[PromptProfile]:
        return [profile for profile in self.all_profiles() if profile.assistant_stage == stage]

    def user_visible(self) -> list[PromptProfile]:
        return [profile for profile in self.all_profiles() if profile.visibility == "user_visible"]

    def build_system_prompt(self, prompt_id: str, *, include_global: bool = True) -> str:
        profile = self.require(prompt_id)
        if not include_global or profile.prompt_id == GLOBAL_INVARIANTS_PROMPT_ID:
            return profile.system_prompt
        return GLOBAL_INVARIANTS_TEXT.rstrip() + "\n\n" + profile.system_prompt.lstrip()


_RESEARCH_TASK_DRAFT_PROMPT = """你是 AutoAD-Researcher 的研究任务书草案生成器。

目标：把多轮对话、输入材料摘要、候选参数、缺失项和用户纠正整理为研究任务书草案。

输出必须绑定 schema，不得输出自由格式 JSON。你必须区分：
- confirmed_parameters：用户明确提供或确认的正式参数；
- candidate_parameters：系统从材料中识别或推荐，但用户尚未确认的候选参数；
- missing_slots：进入真实实验前仍缺失的信息；
- automation_policy：系统可自动执行的范围；
- failure_policy：失败、超时、指标不稳定时如何处理。

如果证据不足，只能写 unknown / candidate / missing，不能把推断写成确认事实。
"""

_PROGRESS_DIGEST_PROMPT = """你是 AutoAD-Researcher 的用户进度摘要器。

目标：把底层事件、artifact 状态和实验阶段压缩为低频、高密度、用户可读的进度新闻流。

要求：
- 不展示 raw path、run_id、provider、stage 内部名或完整日志；
- 每条摘要说明当前阶段、已完成内容、关键发现、风险或下一步；
- 有指标时说明是初步结果还是最终结果；
- 失败要说明证据和可能原因；
- 不夸大实验结论，不承诺提升。
"""


def _default_profiles() -> list[PromptProfile]:
    return [
        PromptProfile(
            prompt_id=GLOBAL_INVARIANTS_PROMPT_ID,
            prompt_version="v1",
            layer="global_invariants",
            title="AutoAD Assistant Global Invariants",
            description="Rules inherited by all AutoAD Assistant prompts.",
            system_prompt=GLOBAL_INVARIANTS_TEXT,
            visibility="internal",
            source_references=[
                "参考/input_intent_processing_architecture_v0_2.md#5",
                "参考/target_autonomous_ad_research_loop_draft_v0_3.md#7",
            ],
            changelog=["v1: initial invariant set derived from prompt architecture discussion."],
        ),
        PromptProfile(
            prompt_id="assistant.collecting_goal.v1",
            prompt_version="v1",
            layer="assistant_state",
            assistant_stage="collecting_goal",
            title="Collecting Goal",
            description="Open-ended intent exploration for vague user research goals.",
            system_prompt=INTENT_CLARIFICATION_PROMPT,
            io=PromptIOContract(
                input_schema="AssistantSessionContext",
                output_schema="AssistantMessagePlan",
                produced_artifacts=["conversation/assistant_understanding.jsonl"],
                forbidden_outputs=["raw_run_id", "raw_path", "confirmed_parameter_without_user_confirmation"],
            ),
            visibility="user_visible",
            source_references=["src/autoad_researcher/ui/chat_prompts.py::INTENT_CLARIFICATION_PROMPT"],
            changelog=["v1: maps existing Research Assistant intent clarification prompt."],
        ),
        PromptProfile(
            prompt_id="assistant.understanding_intent.v1",
            prompt_version="v1",
            layer="assistant_state",
            assistant_stage="understanding_intent",
            title="Understanding Intent",
            description="Builds a structured understanding from conversation and current artifacts.",
            system_prompt=INTENT_CLARIFICATION_PROMPT,
            io=PromptIOContract(
                input_schema="AssistantSessionContext",
                output_schema="ResearchIntentDraft",
                required_artifacts=["conversation/chat_transcript.jsonl"],
                produced_artifacts=["ui_chat/intent_draft.json", "ui_chat/clarification_input.json"],
                forbidden_outputs=["execution_claim", "unsupported_metric_threshold"],
            ),
            visibility="user_visible",
            source_references=["src/autoad_researcher/ui/intent_draft.py::ResearchIntentDraft"],
            changelog=["v1: captures current intent_draft flow without changing UI execution."],
        ),
        PromptProfile(
            prompt_id="assistant.research_task_draft.v1",
            prompt_version="v1",
            layer="schema_bound_draft",
            title="Schema-Bound Research Task Draft",
            description="Drafts the future research task book from confirmed and candidate task facts.",
            system_prompt=_RESEARCH_TASK_DRAFT_PROMPT,
            io=PromptIOContract(
                input_schema="AutoADAssistantSession",
                output_schema="ResearchTaskDraft",
                required_artifacts=[
                    "conversation/chat_transcript.jsonl",
                    "conversation/assistant_understanding.jsonl",
                ],
                produced_artifacts=["task/research_task_draft.json", "task/research_task_draft.md"],
                forbidden_outputs=["candidate_as_confirmed", "silent_budget_default"],
            ),
            visibility="internal",
            source_references=["参考/input_intent_processing_architecture_v0_2.md#8"],
            changelog=["v1: placeholder profile for the planned research task book layer."],
        ),
        PromptProfile(
            prompt_id="assistant.run_explanation.v1",
            prompt_version="v1",
            layer="user_facing_progress",
            assistant_stage="progress_reporting",
            title="Run Explanation",
            description="Explains current run state from existing artifacts without inventing missing results.",
            system_prompt=RUN_EXPLANATION_PROMPT,
            io=PromptIOContract(
                input_schema="RunArtifactContext",
                output_schema="UserFacingAssistantReply",
                forbidden_outputs=["unsupported_scientific_claim", "raw_stack_trace_by_default"],
            ),
            visibility="user_visible",
            source_references=["src/autoad_researcher/ui/chat_prompts.py::RUN_EXPLANATION_PROMPT"],
            changelog=["v1: maps existing Research Assistant run explanation prompt."],
        ),
        PromptProfile(
            prompt_id="assistant.next_experiment.v1",
            prompt_version="v1",
            layer="user_facing_progress",
            assistant_stage="progress_reporting",
            title="Next Experiment Advice",
            description="Suggests next experiment directions from final facts and evidence.",
            system_prompt=NEXT_EXPERIMENT_PROMPT,
            io=PromptIOContract(
                input_schema="RunArtifactContext",
                output_schema="UserFacingAssistantReply",
                forbidden_outputs=["unverified_improvement_claim", "pipeline_execution_claim"],
            ),
            visibility="user_visible",
            source_references=["src/autoad_researcher/ui/chat_prompts.py::NEXT_EXPERIMENT_PROMPT"],
            changelog=["v1: maps existing Research Assistant next experiment prompt."],
        ),
        PromptProfile(
            prompt_id="assistant.progress_digest.v1",
            prompt_version="v1",
            layer="user_facing_progress",
            assistant_stage="progress_reporting",
            title="Progress Digest",
            description="Creates low-frequency user-facing newsfeed summaries from progress events.",
            system_prompt=_PROGRESS_DIGEST_PROMPT,
            io=PromptIOContract(
                input_schema="ProgressEventBatch",
                output_schema="ProgressDigest",
                required_artifacts=["events.jsonl"],
                produced_artifacts=["conversation/progress_digest.jsonl"],
                forbidden_outputs=["raw_path", "raw_run_id", "unsupported_final_claim"],
            ),
            visibility="user_visible",
            source_references=["参考/input_intent_processing_architecture_v0_2.md#10"],
            changelog=["v1: new planned prompt for long-running task newsfeed summaries."],
        ),
    ]


def get_default_prompt_registry() -> PromptRegistry:
    """Return the built-in AutoAD Assistant prompt registry."""
    return PromptRegistry(_default_profiles())
