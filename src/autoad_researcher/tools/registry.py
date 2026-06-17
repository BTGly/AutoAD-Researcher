"""In-memory generic tool registry."""

from pydantic import BaseModel, ConfigDict, Field, model_validator

from autoad_researcher.tools.contracts import ToolSpec


class ToolRegistry(BaseModel):
    """Deterministic registry of available tool specs."""

    model_config = ConfigDict(extra="forbid")

    tools: dict[str, ToolSpec] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_keys_match_specs(self):
        for key, spec in self.tools.items():
            if key != spec.name:
                raise ValueError(f"tool registry key must match spec name: {key} != {spec.name}")
        return self

    def register(self, spec: ToolSpec) -> "ToolRegistry":
        """Return a new registry with `spec` registered."""
        if spec.name in self.tools:
            raise ValueError(f"duplicate tool spec: {spec.name}")
        return ToolRegistry(tools={**self.tools, spec.name: spec})

    def get(self, name: str) -> ToolSpec:
        """Return a registered tool spec by exact name."""
        try:
            return self.tools[name]
        except KeyError as exc:
            raise KeyError(f"tool spec not registered: {name}") from exc

    def require(self, names: set[str]) -> None:
        """Raise if any names are absent from the registry."""
        missing = sorted(names - set(self.tools))
        if missing:
            raise KeyError(f"required tool specs missing: {missing}")

    def deferred_tool_names(self) -> list[str]:
        """Return registered deferred tool names in deterministic order."""
        return sorted(name for name, spec in self.tools.items() if spec.deferred)
