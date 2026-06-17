"""Deferred tool loading policy and audit records."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from autoad_researcher.benchmarks.hashing import canonical_sha256
from autoad_researcher.repository_intelligence.ids import IdentifierPattern, Sha256Pattern
from autoad_researcher.tools.contracts import ToolSpec
from autoad_researcher.tools.registry import ToolRegistry

ToolStage = Literal[
    "discovery",
    "candidate_verification",
    "resolution",
    "acquisition",
    "analysis",
    "repair",
    "synthesis",
]

STAGE_ALLOWED_TOOLS: dict[str, set[str]] = {
    "discovery": {"web_search", "web_fetch", "github_read"},
    "candidate_verification": {"web_fetch"},
    "resolution": {"github_read"},
    "acquisition": {"github_read", "filesystem_list", "filesystem_read", "filesystem_search", "filesystem_stat", "process"},
    "analysis": {"filesystem_list", "filesystem_read", "filesystem_search", "filesystem_stat", "process"},
    "repair": {"filesystem_list", "filesystem_read", "filesystem_search", "filesystem_stat"},
    "synthesis": set(),
}


class LoadedToolRecord(BaseModel):
    """Audit record for one loaded tool schema."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    tool_name: str = Field(pattern=IdentifierPattern)
    stage: ToolStage
    trigger_reason: str = Field(min_length=1)
    schema_sha256: str = Field(pattern=Sha256Pattern)
    loaded_at: str = Field(min_length=1)


class StageToolLoad(BaseModel):
    """Loaded tool specs and audit records for a stage."""

    model_config = ConfigDict(extra="forbid")

    stage: ToolStage
    specs: list[ToolSpec]
    audit_records: list[LoadedToolRecord]


def initial_tool_specs(registry: ToolRegistry) -> list[ToolSpec]:
    """Return non-deferred tool specs for initial context."""
    return sorted(
        (spec for spec in registry.tools.values() if not spec.deferred),
        key=lambda spec: spec.name,
    )


def load_stage_tool_specs(
    *,
    registry: ToolRegistry,
    stage: ToolStage,
    trigger_reason: str,
    loaded_at: str,
) -> StageToolLoad:
    """Load tool specs allowed for a stage and produce audit records."""
    allowed = STAGE_ALLOWED_TOOLS[stage]
    specs = sorted(
        (spec for name, spec in registry.tools.items() if name in allowed),
        key=lambda spec: spec.name,
    )
    audit_records = [
        LoadedToolRecord(
            tool_name=spec.name,
            stage=stage,
            trigger_reason=trigger_reason,
            schema_sha256=canonical_sha256(spec),
            loaded_at=loaded_at,
        )
        for spec in specs
    ]
    return StageToolLoad(stage=stage, specs=specs, audit_records=audit_records)
