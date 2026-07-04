"""Prompt profile definitions for AutoAD Assistant prompts."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoad_researcher.assistant.prompt_io import (
    AssistantStage,
    PromptIOContract,
    PromptLayer,
    PromptVisibility,
)

GLOBAL_INVARIANTS_PROMPT_ID = "assistant.global_invariants.v1"

GLOBAL_INVARIANTS_TEXT = """AutoAD Assistant global invariants:
1. Do not fabricate execution results.
2. Do not claim code was modified, experiments were run, or reports were generated unless artifacts prove it.
3. Do not silently decide baseline, dataset, metrics, category, compute budget, or evaluation protocol.
4. Candidate parameters must be described as candidates, never as confirmed facts.
5. User-confirmed facts must not be rewritten by the LLM.
6. Hide raw paths, run_id, provider, stage names, and JSON/internal field names from ordinary user-facing replies.
7. Do not bypass Pydantic schemas or write free-form JSON artifacts.
8. Do not treat chat text as approval for patching, execution, or budget expansion.
9. Do not write unsupported inference as fact.
10. Explicitly state risks, failures, missing evidence, and uncertainty when they matter.
"""

_PROMPT_ID_RE = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*\.v[0-9]+$")


class PromptProfile(BaseModel):
    """A versioned prompt profile managed by the Prompt Registry."""

    model_config = ConfigDict(extra="forbid")

    prompt_id: str = Field(min_length=1)
    prompt_version: str = Field(pattern=r"^v[0-9]+$")
    layer: PromptLayer
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    system_prompt: str = Field(min_length=1)
    io: PromptIOContract = Field(default_factory=PromptIOContract)
    assistant_stage: AssistantStage | None = None
    visibility: PromptVisibility = "internal"
    source_references: list[str] = Field(default_factory=list)
    changelog: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_profile(self) -> "PromptProfile":
        if not _PROMPT_ID_RE.match(self.prompt_id):
            raise ValueError("prompt_id must be dotted lowercase and end with .vN")
        if not self.prompt_id.endswith(f".{self.prompt_version}"):
            raise ValueError("prompt_id suffix must match prompt_version")
        if self.layer == "assistant_state" and self.assistant_stage is None:
            raise ValueError("assistant_state prompts require assistant_stage")
        if self.layer != "assistant_state" and self.assistant_stage in {"collecting_goal", "guiding_materials", "registering_sources", "parsing_materials", "understanding_intent", "confirming_task_draft", "ready_for_pipeline"}:
            raise ValueError("non-assistant_state prompt uses an assistant state stage")
        return self
