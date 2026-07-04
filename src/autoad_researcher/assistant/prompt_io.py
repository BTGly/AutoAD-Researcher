"""Prompt input/output contracts for the AutoAD Assistant layer."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AssistantStage = Literal[
    "collecting_goal",
    "guiding_materials",
    "registering_sources",
    "parsing_materials",
    "understanding_intent",
    "confirming_task_draft",
    "ready_for_pipeline",
    "progress_reporting",
]

PromptLayer = Literal[
    "global_invariants",
    "assistant_state",
    "schema_bound_draft",
    "pipeline_specialist",
    "user_facing_progress",
]

PromptVisibility = Literal["user_visible", "internal"]


class PromptIOContract(BaseModel):
    """Schema names and artifact requirements for one prompt profile.

    This model records the contract. It does not execute prompts or validate LLM
    responses directly; downstream callers still use concrete Pydantic schemas.
    """

    model_config = ConfigDict(extra="forbid")

    input_schema: str | None = None
    output_schema: str | None = None
    required_artifacts: list[str] = Field(default_factory=list)
    produced_artifacts: list[str] = Field(default_factory=list)
    forbidden_outputs: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_artifact_names(self) -> "PromptIOContract":
        all_artifacts = [*self.required_artifacts, *self.produced_artifacts]
        if len(all_artifacts) != len(set(all_artifacts)):
            raise ValueError("duplicate artifact name in prompt IO contract")
        for artifact in all_artifacts:
            if artifact.startswith("/") or ".." in artifact.split("/"):
                raise ValueError(f"artifact path must be run-relative and safe: {artifact!r}")
        return self
