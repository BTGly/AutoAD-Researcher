"""Minimal AutoAD schemas for DeepAgentsHarness spike validation.

These are SPIKE schemas — intentionally permissive to verify harness plumbing.
Production schemas will be stricter with validated sub-models.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict


class ExperimentPlan(BaseModel):
    """Validates that required top-level keys exist. Content types are lenient for spike."""

    model_config = ConfigDict(extra="allow")

    experiment_goal: str
    baseline: str
    dataset: str
    categories: list[Any]
    metrics: list[Any]
    control_group: Any  # str or structured dict
    experiment_group: Any  # str or structured dict
    resource_budget: Any  # str or structured dict
    risks: list[Any]


class PatchPlan(BaseModel):
    """Validates that required top-level keys exist. Content types are lenient for spike."""

    model_config = ConfigDict(extra="allow")

    target_repo: str
    files_to_inspect: list[Any]
    files_to_modify: list[Any]
    planned_changes: list[Any]
    expected_risks: list[Any]
    requires_approval: bool
