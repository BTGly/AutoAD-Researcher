"""Assistant prompt architecture primitives for AutoAD-Researcher."""

from autoad_researcher.assistant.draft_schema import ResearchTaskDraftV1
from autoad_researcher.assistant.events import AssistantEvent, AssistantEventType, RouterLabel
from autoad_researcher.assistant.prompt_selector import (
    MODE_TO_PROMPT_ID,
    MODE_TO_STAGE,
    PromptSelector,
    RESEARCH_TASK_DRAFT_PROMPT_ID,
)
from autoad_researcher.assistant.session import (
    AssistantMode,
    AutoADAssistantSession,
    InteractionState,
    SourceState,
    TaskControlState,
)
from autoad_researcher.assistant.runtime import (
    AssistantRuntimeResult,
    DeterministicAssistantRuntime,
    FakeIntentAlignmentBackend,
    route_user_text,
)
from autoad_researcher.assistant.session_store import (
    AssistantTransitionRecord,
    SessionStore,
)
from autoad_researcher.assistant.probe import KNOWN_ARTIFACT_MAP, WhatWeKnow, silent_probe
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
    "route_user_text",
    "FakeIntentAlignmentBackend",
    "DeterministicAssistantRuntime",
    "AssistantRuntimeResult",
    "silent_probe",
    "WhatWeKnow",
    "KNOWN_ARTIFACT_MAP",
    "SessionStore",
    "AssistantTransitionRecord",
    "TaskControlState",
    "SourceState",
    "InteractionState",
    "AutoADAssistantSession",
    "AssistantMode",
    "RESEARCH_TASK_DRAFT_PROMPT_ID",
    "PromptSelector",
    "MODE_TO_STAGE",
    "MODE_TO_PROMPT_ID",
    "RouterLabel",
    "AssistantEventType",
    "AssistantEvent",
    "ResearchTaskDraftV1",
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
