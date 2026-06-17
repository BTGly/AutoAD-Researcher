"""Model profile loading and deterministic routing for Repository Intelligence."""

import json
import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from autoad_researcher.benchmarks.hashing import canonical_sha256
from autoad_researcher.repository_intelligence.ids import IdentifierPattern, Sha256Pattern
from autoad_researcher.repository_intelligence.skills import RepositoryStage

ModelCapability = Literal["strong_code_reasoning", "tool_use", "structured_output", "long_context"]
ModelAvailability = Literal["available", "unavailable"]
SmokeTestStatus = Literal["passed"]
ModelProfileName = Literal["repository_fast_v1", "repository_primary_v1", "repository_fallback_v1"]
ModelRoutePurpose = Literal[
    "signal_extraction",
    "simple_discovery",
    "ambiguous_resolution",
    "analysis",
    "synthesis",
    "repair",
]
ModelRoutingStatus = Literal["selected", "blocked"]

REQUIRED_PROFILE_NAMES = {"repository_fast_v1", "repository_primary_v1", "repository_fallback_v1"}


class ModelSmokeTests(BaseModel):
    """Offline record that required model capabilities were smoke-tested."""

    model_config = ConfigDict(extra="forbid")

    tool_use: SmokeTestStatus
    structured_output: SmokeTestStatus
    provider_availability: SmokeTestStatus
    context_window: SmokeTestStatus | None = None


class ModelProfile(BaseModel):
    """Concrete provider/model mapping for one logical Repository profile."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    provider: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    required_capabilities: set[ModelCapability]
    context_window: int = Field(gt=0)
    minimum_context_window: int | None = Field(default=None, gt=0)
    availability: ModelAvailability
    smoke_tests: ModelSmokeTests

    @field_validator("provider", "model_id")
    @classmethod
    def _validate_concrete_ids(cls, value: str) -> str:
        if value == "latest" or value.endswith(":latest") or "latest" in value.split("/"):
            raise ValueError("model provider/model IDs must not use latest aliases")
        return value

    @model_validator(mode="after")
    def _validate_capability_smoke_tests(self):
        if "tool_use" in self.required_capabilities and self.smoke_tests.tool_use != "passed":
            raise ValueError("tool_use smoke test is required")
        if "structured_output" in self.required_capabilities and self.smoke_tests.structured_output != "passed":
            raise ValueError("structured_output smoke test is required")
        if self.availability == "available" and self.smoke_tests.provider_availability != "passed":
            raise ValueError("provider availability smoke test is required")
        if self.minimum_context_window is not None and self.context_window < self.minimum_context_window:
            raise ValueError("context_window is below minimum_context_window")
        if "long_context" in self.required_capabilities and self.smoke_tests.context_window != "passed":
            raise ValueError("long_context requires context_window smoke test")
        return self


class RepositoryModelConfig(BaseModel):
    """Repository Intelligence model routing configuration."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    profiles: dict[ModelProfileName, ModelProfile]

    @model_validator(mode="after")
    def _validate_required_profiles(self):
        actual = set(self.profiles)
        if actual != REQUIRED_PROFILE_NAMES:
            raise ValueError(f"model config profiles must be exactly {sorted(REQUIRED_PROFILE_NAMES)}")
        return self

    @property
    def config_sha256(self) -> str:
        return canonical_sha256(self)


class ModelRoutingDecision(BaseModel):
    """Auditable model routing decision."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_version: Literal[1]
    decision_id: str = Field(pattern=IdentifierPattern)
    stage: RepositoryStage
    purpose: ModelRoutePurpose
    status: ModelRoutingStatus
    selected_profile: ModelProfileName | None = None
    provider: str | None = None
    model_id: str | None = None
    fallback_used: bool
    fallback_attempts_used: int = Field(ge=0)
    fallback_attempts_remaining: int = Field(ge=0)
    capability_gate_passed: bool
    blocked_reason: str | None = None
    config_sha256: str = Field(pattern=Sha256Pattern)


class ModelRouter:
    """Deterministic model router with fallback capability gate."""

    def __init__(self, config: RepositoryModelConfig, *, max_fallback_calls: int = 1):
        if max_fallback_calls < 0:
            raise ValueError("max_fallback_calls must be non-negative")
        self.config = config
        self.max_fallback_calls = max_fallback_calls

    def route(
        self,
        *,
        stage: RepositoryStage,
        purpose: ModelRoutePurpose,
        primary_failed: bool = False,
        fallback_attempts_used: int = 0,
        decision_id: str = "model_route_001",
    ) -> ModelRoutingDecision:
        """Choose a concrete model profile without invoking an LLM."""
        if primary_failed:
            return self._route_fallback(
                stage=stage,
                purpose=purpose,
                fallback_attempts_used=fallback_attempts_used,
                decision_id=decision_id,
            )

        profile_name = _primary_profile_for(purpose)
        return self._selected(
            decision_id=decision_id,
            stage=stage,
            purpose=purpose,
            profile_name=profile_name,
            fallback_used=False,
            fallback_attempts_used=fallback_attempts_used,
        )

    def _route_fallback(
        self,
        *,
        stage: RepositoryStage,
        purpose: ModelRoutePurpose,
        fallback_attempts_used: int,
        decision_id: str,
    ) -> ModelRoutingDecision:
        if fallback_attempts_used >= self.max_fallback_calls:
            return self._blocked(
                decision_id=decision_id,
                stage=stage,
                purpose=purpose,
                fallback_attempts_used=fallback_attempts_used,
                reason="fallback quota exhausted",
            )

        primary = self.config.profiles["repository_primary_v1"]
        fallback = self.config.profiles["repository_fallback_v1"]
        required = set(primary.required_capabilities) - {"long_context"}
        missing = sorted(required - fallback.required_capabilities)
        if fallback.availability != "available":
            return self._blocked(
                decision_id=decision_id,
                stage=stage,
                purpose=purpose,
                fallback_attempts_used=fallback_attempts_used,
                reason="fallback provider unavailable",
            )
        if missing:
            return self._blocked(
                decision_id=decision_id,
                stage=stage,
                purpose=purpose,
                fallback_attempts_used=fallback_attempts_used,
                reason=f"fallback missing capabilities: {missing}",
            )

        return self._selected(
            decision_id=decision_id,
            stage=stage,
            purpose=purpose,
            profile_name="repository_fallback_v1",
            fallback_used=True,
            fallback_attempts_used=fallback_attempts_used + 1,
        )

    def _selected(
        self,
        *,
        decision_id: str,
        stage: RepositoryStage,
        purpose: ModelRoutePurpose,
        profile_name: ModelProfileName,
        fallback_used: bool,
        fallback_attempts_used: int,
    ) -> ModelRoutingDecision:
        profile = self.config.profiles[profile_name]
        return ModelRoutingDecision(
            schema_version=1,
            decision_id=decision_id,
            stage=stage,
            purpose=purpose,
            status="selected",
            selected_profile=profile_name,
            provider=profile.provider,
            model_id=profile.model_id,
            fallback_used=fallback_used,
            fallback_attempts_used=fallback_attempts_used,
            fallback_attempts_remaining=max(self.max_fallback_calls - fallback_attempts_used, 0),
            capability_gate_passed=True,
            config_sha256=self.config.config_sha256,
        )

    def _blocked(
        self,
        *,
        decision_id: str,
        stage: RepositoryStage,
        purpose: ModelRoutePurpose,
        fallback_attempts_used: int,
        reason: str,
    ) -> ModelRoutingDecision:
        return ModelRoutingDecision(
            schema_version=1,
            decision_id=decision_id,
            stage=stage,
            purpose=purpose,
            status="blocked",
            fallback_used=False,
            fallback_attempts_used=fallback_attempts_used,
            fallback_attempts_remaining=max(self.max_fallback_calls - fallback_attempts_used, 0),
            capability_gate_passed=False,
            blocked_reason=reason,
            config_sha256=self.config.config_sha256,
        )


def load_model_config(path: Path) -> RepositoryModelConfig:
    """Load a Repository Intelligence model routing YAML file."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"model config must be a mapping: {path}")
    return RepositoryModelConfig.model_validate(raw)


def append_model_routing_decision(path: Path, decision: ModelRoutingDecision) -> None:
    """Append one model routing decision to JSONL audit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(decision.model_dump(mode="json", exclude_none=True), ensure_ascii=False, sort_keys=True)
    with path.open("ab") as f:
        f.write(data.encode("utf-8") + b"\n")
        f.flush()
        os.fsync(f.fileno())


def _primary_profile_for(purpose: ModelRoutePurpose) -> ModelProfileName:
    if purpose in {"signal_extraction", "simple_discovery"}:
        return "repository_fast_v1"
    return "repository_primary_v1"
