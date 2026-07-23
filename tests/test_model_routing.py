import pytest

from autoad_researcher.assistant.model_routing import select_model_route


def test_role_defaults_use_flash_for_dialogue_and_report_and_pro_for_experiments():
    assert select_model_route("research_dialogue").model_id == "deepseek-v4-flash"
    assert select_model_route("report").model_id == "deepseek-v4-flash"
    experiment = select_model_route("experiment_agent")
    assert experiment.model_id == "deepseek-v4-pro"
    assert experiment.thinking_type == "enabled"
    assert experiment.reasoning_effort == "max"


def test_requested_model_does_not_change_role_reasoning_policy():
    report = select_model_route("report", "deepseek-v4-pro")
    assert report.model_id == "deepseek-v4-pro"
    assert report.thinking_type == "disabled"
    assert report.reasoning_effort is None


def test_legacy_model_aliases_are_normalized():
    assert select_model_route("research_dialogue", "deepseek-chat").model_id == "deepseek-v4-flash"
    assert select_model_route("experiment_agent", "deepseek-reasoner").model_id == "deepseek-v4-pro"


def test_unknown_model_is_rejected():
    with pytest.raises(ValueError, match="unsupported AutoAD model"):
        select_model_route("report", "unknown-model")
