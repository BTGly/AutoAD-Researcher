"""Assistant prompt architecture primitives for AutoAD-Researcher."""

from autoad_researcher.assistant.prompt_io import (
    AssistantStage,
    PromptLayer,
    PromptVisibility,
    PromptIOContract,
)
from autoad_researcher.assistant.prompt_profiles import (
    GLOBAL_INVARIANTS_PROMPT_ID,
    GLOBAL_INVARIANTS_TEXT,
    PromptProfile,
)
from autoad_researcher.assistant.prompt_registry import (
    PromptRegistry,
    get_default_prompt_registry,
)

__all__ = [
    "AssistantStage",
    "PromptLayer",
    "PromptVisibility",
    "PromptIOContract",
    "GLOBAL_INVARIANTS_PROMPT_ID",
    "GLOBAL_INVARIANTS_TEXT",
    "PromptProfile",
    "PromptRegistry",
    "get_default_prompt_registry",
]
