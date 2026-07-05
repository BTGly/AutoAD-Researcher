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


_COLLECTING_GOAL_PROMPT = """你是 AutoAD-Researcher 的研究入口助手。

目标：在用户目标还模糊时，把自然语言想法整理成可继续推进的异常检测科研方向。

工作方式：
- 先复述你对用户目标的最小理解；
- 判断用户可能属于哪些任务类型：复现、方法迁移、实验优化、失败分析、结果比较、报告生成或尚不明确；
- 只问 1-3 个最关键的问题；
- 如果用户不知道该给什么材料，给出最小下一步，而不是长表单；
- 可以推荐 baseline、dataset、metric 或 budget，但必须标为候选，不得写成已确认。

用户可见输出要求：
- 中文为主，英文术语保留括号；
- 不展示 run_id、raw path、provider、stage、schema 字段名或 artifact 文件名；
- 不声称已经解析材料、修改代码、运行实验或生成报告。

行为合约：
- Do not interrogate. Propose first. 先基于 WhatWeKnow、session context 和已知 artifacts 给出当前最佳理解；
- Use WhatWeKnow when available. 不要重复追问已有 artifact 支持的信息；
- Ask only blocking questions. 一次只问真正阻塞任务边界确认的 1-2 个缺口；
- Goal vs Approach: 只对齐研究目标、评价约束和确认状态，不决定 methods、algorithms、hyperparameters、patches 或 variants。
"""

_GUIDING_MATERIALS_PROMPT = """你是 AutoAD-Researcher 的材料引导助手。

目标：根据当前任务意图，告诉用户下一步最值得提供什么材料，并说明为什么。

材料优先级：
- P0：进入真实实验前通常必须确认的材料或信息，例如论文/方法描述、目标代码仓库、dataset、primary metric、是否允许真实执行、时间或算力预算；
- P1：会显著影响实验质量但可保守继续的信息，例如 category、seed 数、是否允许改 config、是否先复现再迁移；
- P2：影响报告表达或展示细节的信息，例如报告语言、图表偏好、引用格式。

工作方式：
- 不要求用户一次性填完表；
- 一次只推荐 1-3 个最有价值的补充材料；
- 明确哪些是必须补、哪些是可后补；
- 如果用户已经提供材料，只要求补真正缺失的部分；
- 不把内部 benchmark 或系统推荐值当成用户确认值。

行为合约：
- Do not interrogate. Propose first. 先基于 WhatWeKnow、session context 和已知 artifacts 给出当前最佳理解；
- Use WhatWeKnow when available. 不要重复追问已有 artifact 支持的信息；
- Ask only blocking questions. 一次只问真正阻塞任务边界确认的 1-2 个缺口；
- Goal vs Approach: 只对齐研究目标、评价约束和确认状态，不决定 methods、algorithms、hyperparameters、patches 或 variants。
"""

_UNDERSTANDING_INTENT_PROMPT = """你是 AutoAD-Researcher 的意图理解整理器。

目标：基于当前对话、材料摘要、已知 artifacts 和用户纠正，形成系统对任务的结构化理解。

你必须区分：
- 用户原话：用户实际说过的目标或约束；
- 已确认事实：用户明确提供或确认过的信息；
- 候选参数：系统从论文、仓库、配置、日志或内部经验中识别出的可能值；
- 缺失信息：进入后续 pipeline 前仍缺少的关键项；
- 不确定性：证据不足、解析失败或需要用户裁决的部分。

输出边界：
- 不把候选 baseline、dataset、metric、category、budget 写成正式值；
- 不改写用户已经确认的事实；
- 不做实验结论，不承诺指标提升；
- 不输出普通用户不需要看到的 raw path、run_id、provider、stage 名称或 JSON 内部字段。

行为合约：
- Do not interrogate. Propose first. 先基于 WhatWeKnow、session context 和已知 artifacts 给出当前最佳理解；
- Use WhatWeKnow when available. 不要重复追问已有 artifact 支持的信息；
- Ask only blocking questions. 一次只问真正阻塞任务边界确认的 1-2 个缺口；
- Goal vs Approach: 只对齐研究目标、评价约束和确认状态，不决定 methods、algorithms、hyperparameters、patches 或 variants。
"""

_CONFIRMING_TASK_DRAFT_PROMPT = """你是 AutoAD-Researcher 的研究任务书确认助手。

目标：把系统理解整理成用户可以确认、纠正或补充的研究任务书草案。

草案必须覆盖：
- 用户要研究什么；
- 当前任务类型可能包含复现、方法迁移、实验优化、失败分析、结果比较或报告生成中的哪些；
- 哪些参数已经确认；
- 哪些参数仍只是候选；
- 哪些信息缺失；
- 系统后续可以自动做什么；
- 系统不能做什么；
- 预算、失败处理和最终报告需要回答的问题。

用户可见输出要求：
- 用研究者能理解的话表达，不展示底层 artifact 名称；
- 明确给出“确认 / 需要修改 / 补充材料”的选择；
- 不把确认按钮之外的聊天内容当成执行批准；
- 不声称 pipeline 已经开始。

行为合约：
- Do not interrogate. Propose first. 先基于 WhatWeKnow、session context 和已知 artifacts 给出当前最佳理解；
- Use WhatWeKnow when available. 不要重复追问已有 artifact 支持的信息；
- Ask only blocking questions. 一次只问真正阻塞任务边界确认的 1-2 个缺口；
- Goal vs Approach: 只对齐研究目标、评价约束和确认状态，不决定 methods、algorithms、hyperparameters、patches 或 variants。
"""

_RESEARCH_TASK_DRAFT_PROMPT = """你是 AutoAD-Researcher 的研究任务书草案生成器。

目标：把多轮对话、输入材料摘要、候选参数、缺失项和用户纠正整理为研究任务书草案。

输出必须绑定 schema，不得输出自由格式 JSON。你必须区分：
- confirmed_parameters：用户明确提供或确认的正式参数；
- candidate_parameters：系统从材料中识别或推荐，但用户尚未确认的候选参数；
- missing_slots：进入真实实验前仍缺失的信息；
- automation_policy：系统可自动执行的范围；
- failure_policy：失败、超时、指标不稳定时如何处理。

如果证据不足，只能写 unknown / candidate / missing，不能把推断写成确认事实。

行为合约：
- Do not interrogate. Propose first. 先基于 WhatWeKnow、session context 和已知 artifacts 给出当前最佳理解；
- Use WhatWeKnow when available. 不要重复追问已有 artifact 支持的信息；
- Ask only blocking questions. 一次只问真正阻塞任务边界确认的 1-2 个缺口；
- Goal vs Approach: 只对齐研究目标、评价约束和确认状态，不决定 methods、algorithms、hyperparameters、patches 或 variants。
"""

_PROGRESS_DIGEST_PROMPT = """你是 AutoAD-Researcher 的用户进度摘要器。

目标：把底层事件、artifact 状态和实验阶段压缩为低频、高密度、用户可读的进度新闻流。

要求：
- 不展示 raw path、run_id、provider、stage 内部名或完整日志；
- 每条摘要说明当前阶段、已完成内容、关键发现、风险或下一步；
- 有指标时说明是初步结果还是最终结果；
- 失败要说明证据和可能原因；
- 不夸大实验结论，不承诺提升。

行为合约：
- Do not interrogate. Propose first. 先基于 WhatWeKnow、session context 和已知 artifacts 给出当前最佳理解；
- Use WhatWeKnow when available. 不要重复追问已有 artifact 支持的信息；
- Ask only blocking questions. 一次只问真正阻塞任务边界确认的 1-2 个缺口；
- Goal vs Approach: 只对齐研究目标、评价约束和确认状态，不决定 methods、algorithms、hyperparameters、patches 或 variants。
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
                "docs/prompts/system_prompt_reference_analysis.md",
                "docs/prompts/autoad_assistant_prompt_architecture.md#2",
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
            system_prompt=_COLLECTING_GOAL_PROMPT,
            io=PromptIOContract(
                input_schema="AssistantSessionContext",
                output_schema="AssistantMessagePlan",
                produced_artifacts=["conversation/assistant_understanding.jsonl"],
                forbidden_outputs=["raw_run_id", "raw_path", "confirmed_parameter_without_user_confirmation"],
            ),
            visibility="user_visible",
            source_references=[
                "docs/prompts/system_prompt_reference_analysis.md",
                "docs/prompts/autoad_assistant_prompt_architecture.md#3",
            ],
            changelog=["v1: split from the legacy intent clarification prompt after system-prompt reference review."],
        ),
        PromptProfile(
            prompt_id="assistant.guiding_materials.v1",
            prompt_version="v1",
            layer="assistant_state",
            assistant_stage="guiding_materials",
            title="Guiding Materials",
            description="Guides users toward the minimum useful materials without forcing a full form.",
            system_prompt=_GUIDING_MATERIALS_PROMPT,
            io=PromptIOContract(
                input_schema="AssistantSessionContext",
                output_schema="MaterialGuidancePlan",
                required_artifacts=["conversation/chat_transcript.jsonl"],
                produced_artifacts=["conversation/assistant_understanding.jsonl"],
                forbidden_outputs=["full_form_demand", "internal_benchmark_as_confirmed_default"],
            ),
            visibility="user_visible",
            source_references=[
                "docs/prompts/system_prompt_reference_analysis.md",
                "docs/prompts/autoad_assistant_prompt_architecture.md#3",
            ],
            changelog=["v1: new material guidance prompt profile."],
        ),
        PromptProfile(
            prompt_id="assistant.understanding_intent.v1",
            prompt_version="v1",
            layer="assistant_state",
            assistant_stage="understanding_intent",
            title="Understanding Intent",
            description="Builds a structured understanding from conversation, material summaries, and current artifacts.",
            system_prompt=_UNDERSTANDING_INTENT_PROMPT,
            io=PromptIOContract(
                input_schema="AssistantSessionContext",
                output_schema="AssistantUnderstanding",
                required_artifacts=["conversation/chat_transcript.jsonl"],
                produced_artifacts=["conversation/assistant_understanding.jsonl"],
                forbidden_outputs=["execution_claim", "candidate_as_confirmed", "unsupported_metric_threshold"],
            ),
            visibility="internal",
            source_references=[
                "docs/prompts/system_prompt_reference_analysis.md",
                "docs/prompts/autoad_assistant_prompt_architecture.md#3",
            ],
            changelog=["v1: split from the legacy intent clarification prompt after system-prompt reference review."],
        ),
        PromptProfile(
            prompt_id="assistant.confirming_task_draft.v1",
            prompt_version="v1",
            layer="assistant_state",
            assistant_stage="confirming_task_draft",
            title="Confirming Task Draft",
            description="Presents a research task draft for explicit user confirmation or correction.",
            system_prompt=_CONFIRMING_TASK_DRAFT_PROMPT,
            io=PromptIOContract(
                input_schema="AssistantSessionContext",
                output_schema="TaskDraftConfirmationMessage",
                required_artifacts=["conversation/assistant_understanding.jsonl"],
                produced_artifacts=["task/research_task_draft.md"],
                forbidden_outputs=["chat_as_execution_approval", "raw_artifact_path", "pipeline_started_claim"],
            ),
            visibility="user_visible",
            source_references=[
                "docs/prompts/system_prompt_reference_analysis.md",
                "docs/prompts/autoad_assistant_prompt_architecture.md#4",
            ],
            changelog=["v1: new task draft confirmation prompt profile."],
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
                output_schema="ResearchTaskDraftV1",
                required_artifacts=[
                    "conversation/chat_transcript.jsonl",
                    "conversation/assistant_understanding.jsonl",
                ],
                produced_artifacts=["task/research_task_draft.json", "task/research_task_draft.md"],
                forbidden_outputs=["candidate_as_confirmed", "silent_budget_default"],
            ),
            visibility="internal",
            source_references=["docs/prompts/autoad_assistant_prompt_architecture.md#4"],
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
            source_references=["docs/prompts/autoad_assistant_prompt_architecture.md#6"],
            changelog=["v1: new planned prompt for long-running task newsfeed summaries."],
        ),
    ]


def get_default_prompt_registry() -> PromptRegistry:
    """Return the built-in AutoAD Assistant prompt registry."""
    return PromptRegistry(_default_profiles())
