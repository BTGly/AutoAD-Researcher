"""Prompt selection for AutoAD Assistant modes.

PromptSelector is intentionally small: it maps the current coarse assistant mode
to a prompt profile. It does not classify user text, read artifacts, call LLMs,
or execute pipeline steps.
"""

from __future__ import annotations

from autoad_researcher.assistant.prompt_io import AssistantStage
from autoad_researcher.assistant.prompt_profiles import PromptProfile
from autoad_researcher.assistant.prompt_registry import PromptRegistry, get_default_prompt_registry
from autoad_researcher.assistant.session import AssistantMode


MODE_TO_STAGE: dict[AssistantMode, AssistantStage] = {
    "goal_alignment": "collecting_goal",
    "material_alignment": "guiding_materials",
    "artifact_processing": "parsing_materials",
    "intent_structuring": "understanding_intent",
    "task_confirmation": "confirming_task_draft",
    "pipeline_ready": "ready_for_pipeline",
    "progress_reporting": "progress_reporting",
}

MODE_TO_PROMPT_ID: dict[AssistantMode, str] = {
    "goal_alignment": "assistant.collecting_goal.v1",
    "material_alignment": "assistant.guiding_materials.v1",
    "artifact_processing": "assistant.progress_digest.v1",
    "intent_structuring": "assistant.understanding_intent.v1",
    "task_confirmation": "assistant.confirming_task_draft.v1",
    "pipeline_ready": "assistant.confirming_task_draft.v1",
    "progress_reporting": "assistant.progress_digest.v1",
}

RESEARCH_TASK_DRAFT_PROMPT_ID = "assistant.research_task_draft.v1"


class PromptSelector:
    """Select prompt profiles from coarse assistant modes."""

    def __init__(self, registry: PromptRegistry | None = None) -> None:
        self._registry = registry or get_default_prompt_registry()

    def stage_for_mode(self, mode: AssistantMode) -> AssistantStage:
        return _require_mapping(MODE_TO_STAGE, mode, "stage")

    def prompt_id_for_mode(self, mode: AssistantMode) -> str:
        return _require_mapping(MODE_TO_PROMPT_ID, mode, "prompt")

    def profile_for_mode(self, mode: AssistantMode) -> PromptProfile:
        return self._registry.require(self.prompt_id_for_mode(mode))

    def build_system_prompt_for_mode(self, mode: AssistantMode, *, include_global: bool = True) -> str:
        return self._registry.build_system_prompt(self.prompt_id_for_mode(mode), include_global=include_global)

    def research_task_draft_profile(self) -> PromptProfile:
        return self._registry.require(RESEARCH_TASK_DRAFT_PROMPT_ID)

    def build_research_task_draft_prompt(self, *, include_global: bool = True) -> str:
        return self._registry.build_system_prompt(RESEARCH_TASK_DRAFT_PROMPT_ID, include_global=include_global)


def _require_mapping(mapping: dict[AssistantMode, str], mode: AssistantMode, label: str) -> str:
    try:
        return mapping[mode]
    except KeyError as exc:
        raise KeyError(f"unsupported assistant mode for {label} selection: {mode!r}") from exc
