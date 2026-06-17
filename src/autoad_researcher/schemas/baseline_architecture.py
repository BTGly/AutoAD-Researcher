"""Step 3.4 — Baseline Architecture Contract schemas.

Schema owner: Step 3.4 Idea & Transfer Design.
Producer: Step 3.1 Repository Intelligence (on-demand).
Consumer: Step 3.4 ArchitectureAligner.

These schemas are shared via `src/autoad_researcher/schemas/`
so both 3.1 and 3.4 import the same definitions.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.paper_intelligence.ids import IdentifierPattern, Sha256Pattern


# ---------------------------------------------------------------------------
# TensorSpec
# ---------------------------------------------------------------------------


class TensorAxis(BaseModel):
    """One axis of a tensor, with semantic role and typical value."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    semantic_role: str = Field(min_length=1)
    dynamic: bool
    typical_value: int | None = None


class TensorSpec(BaseModel):
    """Structured description of a tensor in the baseline architecture."""

    model_config = ConfigDict(extra="forbid")

    tensor_name: str = Field(min_length=1)
    rank: int | None = None
    axes: list[TensorAxis] = Field(default_factory=list)
    dtype: str | None = None
    layout: str | None = None
    semantic_role: str = Field(min_length=1)
    location_hook_id: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# InterfaceSpec
# ---------------------------------------------------------------------------


class InterfaceSpec(BaseModel):
    """Input or output interface of an execution phase."""

    model_config = ConfigDict(extra="forbid")

    interface_name: str = Field(min_length=1)
    tensors: list[TensorSpec] = Field(default_factory=list)
    is_batched: bool = False


# ---------------------------------------------------------------------------
# ExecutionPhaseContract
# ---------------------------------------------------------------------------


class ExecutionPhaseContract(BaseModel):
    """Description of a single execution phase (fit, train, infer, etc.)."""

    model_config = ConfigDict(extra="forbid")

    phase_id: str = Field(pattern=IdentifierPattern)
    phase: Literal["fit", "train", "infer", "postprocess", "evaluate"]
    uses_gradient: bool | None = None
    mutates_model_state: bool | None = None
    mutates_which_state: list[str] = Field(default_factory=list)
    inputs: list[InterfaceSpec] = Field(default_factory=list)
    outputs: list[InterfaceSpec] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# ModificationHook
# ---------------------------------------------------------------------------


class ModificationHook(BaseModel):
    """A well-defined insertion/modification point in the baseline."""

    model_config = ConfigDict(extra="forbid")

    hook_id: str = Field(pattern=IdentifierPattern)
    hook_name: str = Field(min_length=1)
    module_path: str = Field(min_length=1)
    symbol: str | None = None
    semantic_role: str = Field(min_length=1)

    path_classification: Literal[
        "modifiable_candidate",
        "protected_candidate",
        "generated_or_vendor",
        "unknown",
    ]
    protected_reasons: list[str] = Field(default_factory=list)
    allowed_for_transfer_design: bool

    inputs: list[TensorSpec] = Field(default_factory=list)
    outputs: list[TensorSpec] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# ArchitectureComponent
# ---------------------------------------------------------------------------


class ArchitectureComponent(BaseModel):
    """A named architectural component of the baseline model."""

    model_config = ConfigDict(extra="forbid")

    component_id: str = Field(pattern=IdentifierPattern)
    name: str = Field(min_length=1)
    role: str = Field(min_length=1)
    semantic_description: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# BaselineArchitectureContract
# ---------------------------------------------------------------------------


class BaselineArchitectureContract(BaseModel):
    """Full architectural contract of the target baseline.

    Produced by Step 3.1 Repository Intelligence.
    Consumed by Step 3.4 ArchitectureAligner.
    """

    model_config = ConfigDict(extra="forbid")

    model_name: str = Field(min_length=1)
    repository_source_id: str = Field(min_length=1)
    repository_commit: str = Field(pattern=Sha256Pattern)

    architecture_components: list[ArchitectureComponent] = Field(default_factory=list)
    phases: list[ExecutionPhaseContract] = Field(default_factory=list)
    tensors: list[TensorSpec] = Field(default_factory=list)
    modifiable_hooks: list[ModificationHook] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
