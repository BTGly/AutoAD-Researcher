"""Role-based model selection for the AutoAD assistant surfaces.

The route owns model capability defaults, while credentials and provider URLs
remain outside the snapshot. Thinking is a role property: selecting the other
DeepSeek model does not silently change the route's reasoning policy.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal, cast


ModelRole = Literal["research_dialogue", "report", "experiment_agent"]
ModelID = Literal["deepseek-v4-flash", "deepseek-v4-pro"]
ThinkingType = Literal["enabled", "disabled"]

ROUTING_SCHEMA_VERSION = "deepseek-v4-role-routing-v1"
CONTEXT_WINDOW = 1_000_000
MAX_OUTPUT_CAPABILITY = 384_000
SUPPORTED_MODEL_IDS: tuple[ModelID, ...] = (
    "deepseek-v4-flash",
    "deepseek-v4-pro",
)


@dataclass(frozen=True)
class ModelRoute:
    role: ModelRole
    model_id: ModelID
    thinking_type: ThinkingType
    reasoning_effort: str | None
    context_window: int = CONTEXT_WINDOW
    max_output_capability: int = MAX_OUTPUT_CAPABILITY
    routing_schema_version: str = ROUTING_SCHEMA_VERSION

    def snapshot(self) -> dict[str, object]:
        """Return non-secret route provenance suitable for durable state."""

        return asdict(self)


def normalize_model_id(value: str | None, *, default: ModelID) -> ModelID:
    candidate = (value or "").strip()
    if not candidate:
        return default
    if candidate not in SUPPORTED_MODEL_IDS:
        raise ValueError(f"unsupported AutoAD model: {candidate}")
    return cast(ModelID, candidate)


def select_model_route(role: ModelRole, requested_model: str | None = None) -> ModelRoute:
    """Resolve one route without inferring thinking from the selected model."""

    default: ModelID = "deepseek-v4-pro" if role == "experiment_agent" else "deepseek-v4-flash"
    model_id = normalize_model_id(requested_model, default=default)
    thinking_type: ThinkingType = "enabled" if role == "experiment_agent" else "disabled"
    return ModelRoute(
        role=role,
        model_id=model_id,
        thinking_type=thinking_type,
        reasoning_effort="max" if thinking_type == "enabled" else None,
    )
