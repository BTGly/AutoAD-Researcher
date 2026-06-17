"""Generic tool contracts for AutoAD Tool Foundation."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoad_researcher.repository_intelligence.ids import IdentifierPattern


class ToolSpec(BaseModel):
    """Generic tool schema advertised to agent stages."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(pattern=IdentifierPattern)
    description: str = Field(min_length=1)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    read_only: bool
    destructive: bool
    concurrency_safe: bool
    deferred: bool
    permission_category: str = Field(pattern=IdentifierPattern)

    @model_validator(mode="after")
    def _validate_safety_flags(self):
        if self.read_only and self.destructive:
            raise ValueError("read_only tools cannot be destructive")
        return self


class ToolContext(BaseModel):
    """Generic per-call tool context placeholder.

    The 3.1 plan seals `ToolContext.active_repository` later in T7. Until then,
    this contract deliberately preserves extension fields without inventing
    required keys.
    """

    model_config = ConfigDict(extra="allow")


class ToolResult(BaseModel):
    """Generic tool result placeholder.

    Concrete tool result schemas are added with the corresponding tool
    implementations. This wrapper prevents T1 from guessing tool-specific
    payload fields.
    """

    model_config = ConfigDict(extra="allow")
