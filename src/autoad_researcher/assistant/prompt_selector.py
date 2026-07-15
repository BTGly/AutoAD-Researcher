"""PromptSelector — maps AssistantMode to prompt profile IDs.

Does NOT call an LLM, read user text, or interpret semantics.
"""

from __future__ import annotations

from autoad_researcher.assistant.prompt_io import AssistantStage
from autoad_researcher.assistant.prompt_registry import get_default_prompt_registry
from autoad_researcher.assistant.session import AssistantMode

_MODE_TO_PROMPT: dict[AssistantMode, str] = {
    "goal_alignment":       "assistant.collecting_goal.v1",
    "material_alignment":   "assistant.material_alignment.v1",
    "artifact_processing":  "assistant.progress_digest.v1",
    "intent_structuring":   "assistant.understanding_intent.v1",
    "task_confirmation":    "assistant.confirming_task_draft.v1",
    "pipeline_ready":       "assistant.confirming_task_draft.v1",
    "progress_reporting":   "assistant.progress_digest.v1",
}

MODE_TO_PROMPT_ID: dict[AssistantMode, str] = _MODE_TO_PROMPT

_MODE_TO_STAGE: dict[AssistantMode, AssistantStage] = {
    "goal_alignment":       "collecting_goal",
    "material_alignment":   "guiding_materials",
    "artifact_processing":  "parsing_materials",
    "intent_structuring":   "understanding_intent",
    "task_confirmation":    "confirming_task_draft",
    "pipeline_ready":       "ready_for_pipeline",
    "progress_reporting":   "progress_reporting",
}

MODE_TO_STAGE: dict[AssistantMode, AssistantStage] = _MODE_TO_STAGE

RESEARCH_TASK_DRAFT_PROMPT_ID = "assistant.research_task_draft.v1"
RESEARCH_DIALOGUE_PROMPT_ID = "assistant.research_dialogue.v3"

_RESEARCH_CHAT_MODE_TO_PROMPT: dict[str, str] = {
    "intent_clarification": "assistant.material_alignment.v1",
    "run_explanation": "assistant.run_explanation.v1",
    "next_experiment": "assistant.next_experiment.v1",
}

RESEARCH_CHAT_MODE_TO_PROMPT_ID: dict[str, str] = _RESEARCH_CHAT_MODE_TO_PROMPT


class PromptSelector:
    """Selects prompt profiles by assistant mode. No LLM, no semantics."""

    def __init__(self) -> None:
        self._registry = get_default_prompt_registry()

    def prompt_id_for_mode(self, mode: AssistantMode) -> str:
        if mode not in _MODE_TO_PROMPT:
            raise KeyError(f"unsupported assistant mode: {mode}")
        return _MODE_TO_PROMPT[mode]

    def profile_for_mode(self, mode: AssistantMode):
        prompt_id = self.prompt_id_for_mode(mode)
        return self._registry.require(prompt_id)

    def build_system_prompt_for_mode(self, mode: AssistantMode) -> str:
        prompt_id = self.prompt_id_for_mode(mode)
        return self._registry.build_system_prompt(prompt_id)

    def prompt_id_for_research_chat_mode(self, mode: str) -> str:
        if mode not in _RESEARCH_CHAT_MODE_TO_PROMPT:
            raise KeyError(f"unsupported research chat mode: {mode}")
        return _RESEARCH_CHAT_MODE_TO_PROMPT[mode]

    def build_system_prompt_for_research_chat_mode(self, mode: str) -> str:
        prompt_id = self.prompt_id_for_research_chat_mode(mode)
        return self._registry.build_system_prompt(prompt_id)

    def research_task_draft_profile(self):
        return self._registry.require(RESEARCH_TASK_DRAFT_PROMPT_ID)

    def build_research_task_draft_prompt(self) -> str:
        return self.build_system_prompt_for_mode("task_confirmation")

    def research_dialogue_profile(self):
        return self._registry.require(RESEARCH_DIALOGUE_PROMPT_ID)

    def build_research_dialogue_prompt(self) -> str:
        return self._registry.build_system_prompt(RESEARCH_DIALOGUE_PROMPT_ID)

    def select_prompt_id(self, mode: AssistantMode) -> str:
        return self.prompt_id_for_mode(mode)

    def select_stage(self, mode: AssistantMode) -> AssistantStage:
        return _MODE_TO_STAGE[mode]


def select_prompt_id(mode: AssistantMode) -> str:
    """Return the prompt profile ID for the given AssistantMode."""
    return _MODE_TO_PROMPT[mode]
