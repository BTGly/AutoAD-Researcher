"""Assistant prompt architecture primitives for AutoAD-Researcher."""

from autoad_researcher.assistant.draft_schema import ResearchTaskDraftV1
from autoad_researcher.assistant.events import AssistantEvent, AssistantEventType, RouterLabel
from autoad_researcher.assistant.session import (
    AssistantMode,
    AutoADAssistantSession,
    InteractionState,
    SourceState,
    TaskControlState,
)
from autoad_researcher.assistant.transition_policy import apply as apply_transition, validate as validate_invariants
from autoad_researcher.assistant.prompt_selector import (
    MODE_TO_PROMPT_ID,
    MODE_TO_STAGE,
    PromptSelector,
    RESEARCH_DIALOGUE_PROMPT_ID,
    RESEARCH_TASK_DRAFT_PROMPT_ID,
    select_prompt_id,
)
from autoad_researcher.assistant.runtime import (
    DeterministicAssistantRuntime,
    route_user_text,
)
from autoad_researcher.assistant.llm_backend import (
    AssistantTextReplyV1,
    SchemaBoundAssistantBackend,
    SchemaBoundLLMRequest,
    SchemaBoundLLMResult,
    SchemaBoundOutputError,
    StaticSchemaJSONClient,
)
from autoad_researcher.assistant.session_store import (
    AssistantTransitionRecord,
    SessionStore,
    append_event,
    append_transition,
    load_session,
    read_events,
    save_session,
)
from autoad_researcher.assistant.probe import KNOWN_ARTIFACT_MAP, WhatWeKnow, silent_probe
from autoad_researcher.assistant.task_artifacts import (
    ASSISTANT_UNDERSTANDING_ARTIFACT,
    CHAT_TRANSCRIPT_ARTIFACT,
    TASK_CONFIRMED_JSON_ARTIFACT,
    TASK_DRAFT_JSON_ARTIFACT,
    TASK_DRAFT_MD_ARTIFACT,
    USER_CORRECTIONS_ARTIFACT,
    WHAT_WE_KNOW_ARTIFACT,
    AssistantTaskArtifactService,
    AssistantUnderstandingRecord,
)
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
    "AssistantUnderstandingRecord",
    "AssistantTaskArtifactService",
    "WHAT_WE_KNOW_ARTIFACT",
    "USER_CORRECTIONS_ARTIFACT",
    "TASK_DRAFT_MD_ARTIFACT",
    "TASK_DRAFT_JSON_ARTIFACT",
    "TASK_CONFIRMED_JSON_ARTIFACT",
    "CHAT_TRANSCRIPT_ARTIFACT",
    "ASSISTANT_UNDERSTANDING_ARTIFACT",
    "silent_probe",
    "WhatWeKnow",
    "KNOWN_ARTIFACT_MAP",
    "SessionStore",
    "load_session",
    "save_session",
    "append_event",
    "read_events",
    "append_transition",
    "apply_transition",
    "validate_invariants",
    "TaskControlState",
    "SourceState",
    "InteractionState",
    "AutoADAssistantSession",
    "AssistantMode",
    "select_prompt_id",
    "RESEARCH_TASK_DRAFT_PROMPT_ID",
    "RESEARCH_DIALOGUE_PROMPT_ID",
    "PromptSelector",
    "MODE_TO_STAGE",
    "MODE_TO_PROMPT_ID",
    "route_user_text",
    "DeterministicAssistantRuntime",
    "StaticSchemaJSONClient",
    "SchemaBoundOutputError",
    "SchemaBoundLLMResult",
    "SchemaBoundLLMRequest",
    "SchemaBoundAssistantBackend",
    "AssistantTextReplyV1",
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
