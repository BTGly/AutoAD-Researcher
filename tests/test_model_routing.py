from __future__ import annotations

import pytest

from autoad_researcher.assistant.model_routing import (
    CONTEXT_WINDOW,
    MAX_OUTPUT_CAPABILITY,
    normalize_model_id,
    select_model_route,
)


def test_interactive_roles_default_to_flash_without_thinking():
    for role in ("research_dialogue", "report"):
        route = select_model_route(role)
        assert route.model_id == "deepseek-v4-flash"
        assert route.thinking_type == "disabled"
        assert route.reasoning_effort is None


def test_experiment_role_defaults_to_pro_with_max_reasoning():
    route = select_model_route("experiment_agent")
    assert route.model_id == "deepseek-v4-pro"
    assert route.thinking_type == "enabled"
    assert route.reasoning_effort == "max"
    assert route.context_window == CONTEXT_WINDOW == 1_000_000
    assert route.max_output_capability == MAX_OUTPUT_CAPABILITY == 384_000


def test_switching_models_does_not_change_role_thinking_policy():
    assert select_model_route("experiment_agent", "deepseek-v4-flash").thinking_type == "enabled"
    assert select_model_route("research_dialogue", "deepseek-v4-pro").thinking_type == "disabled"


def test_model_ids_are_exact_and_legacy_aliases_are_rejected():
    assert normalize_model_id("deepseek-v4-pro", default="deepseek-v4-flash") == "deepseek-v4-pro"
    with pytest.raises(ValueError, match="unsupported AutoAD model"):
        normalize_model_id("deepseek-chat", default="deepseek-v4-flash")
