"""Tests for Assistant PromptSelector mode mapping."""

from inspect import signature
from typing import get_args

import pytest

from autoad_researcher.assistant.prompt_selector import (
    MODE_TO_PROMPT_ID,
    MODE_TO_STAGE,
    PromptSelector,
    RESEARCH_CHAT_MODE_TO_PROMPT_ID,
    RESEARCH_TASK_DRAFT_PROMPT_ID,
    V2_COMPONENT_TO_PROMPT_ID,
)
from autoad_researcher.assistant.session import AssistantMode
from autoad_researcher.assistant.v2.need_discovery import _build_need_discovery_messages
from autoad_researcher.assistant.v2.reply_planner import _llm_reply
from autoad_researcher.assistant.v2.source_action_planner import _build_source_action_messages
from autoad_researcher.assistant.v2.turn_gate import _build_turn_gate_messages


def test_all_assistant_modes_have_stage_and_prompt_mapping():
    modes = set(get_args(AssistantMode))

    assert set(MODE_TO_STAGE) == modes
    assert set(MODE_TO_PROMPT_ID) == modes


def test_mode_to_stage_mapping_is_explicit_for_v05_mismatch():
    assert MODE_TO_STAGE == {
        "goal_alignment": "collecting_goal",
        "material_alignment": "guiding_materials",
        "artifact_processing": "parsing_materials",
        "intent_structuring": "understanding_intent",
        "task_confirmation": "confirming_task_draft",
        "pipeline_ready": "ready_for_pipeline",
        "progress_reporting": "progress_reporting",
    }


def test_mode_to_prompt_mapping_uses_coarse_profiles_not_user_behaviors():
    assert MODE_TO_PROMPT_ID == {
        "goal_alignment": "assistant.collecting_goal.v1",
        "material_alignment": "assistant.material_alignment.v1",
        "artifact_processing": "assistant.progress_digest.v1",
        "intent_structuring": "assistant.understanding_intent.v1",
        "task_confirmation": "assistant.confirming_task_draft.v1",
        "pipeline_ready": "assistant.confirming_task_draft.v1",
        "progress_reporting": "assistant.progress_digest.v1",
    }
    assert not any("user_" in prompt_id for prompt_id in MODE_TO_PROMPT_ID.values())


def test_selector_returns_registered_profiles():
    selector = PromptSelector()

    assert selector.profile_for_mode("goal_alignment").prompt_id == "assistant.collecting_goal.v1"
    assert selector.profile_for_mode("material_alignment").prompt_id == "assistant.material_alignment.v1"
    assert selector.profile_for_mode("intent_structuring").prompt_id == "assistant.understanding_intent.v1"
    assert selector.profile_for_mode("pipeline_ready").prompt_id == "assistant.confirming_task_draft.v1"


def test_selector_builds_global_prompt_for_mode():
    rendered = PromptSelector().build_system_prompt_for_mode("goal_alignment")

    assert "Do not fabricate execution results" in rendered
    assert "Do not interrogate. Propose first." in rendered
    assert "Use WhatWeKnow when available." in rendered


def test_selector_does_not_route_from_user_text():
    params = signature(PromptSelector.prompt_id_for_mode).parameters

    assert list(params) == ["self", "mode"]
    assert "user_text" not in params
    assert "payload" not in params


def test_research_task_draft_prompt_is_schema_bound():
    selector = PromptSelector()
    profile = selector.research_task_draft_profile()
    rendered = selector.build_research_task_draft_prompt()

    assert profile.prompt_id == RESEARCH_TASK_DRAFT_PROMPT_ID
    assert profile.io.output_schema == "ResearchTaskDraftV1"
    assert profile.system_prompt in rendered
    assert "研究任务书草案生成器" in rendered
    assert "ResearchTaskDraftV1" in profile.io.output_schema
    assert "Do not interrogate. Propose first." in rendered


def test_selector_rejects_unsupported_mode_at_runtime():
    selector = PromptSelector()

    with pytest.raises(KeyError, match="unsupported assistant mode"):
        selector.prompt_id_for_mode("user_uploaded_pdf")  # type: ignore[arg-type]


def test_selector_routes_research_chat_modes_through_registry():
    selector = PromptSelector()

    assert RESEARCH_CHAT_MODE_TO_PROMPT_ID == {
        "intent_clarification": "assistant.material_alignment.v1",
        "run_explanation": "assistant.run_explanation.v1",
        "next_experiment": "assistant.next_experiment.v1",
    }
    assert selector.prompt_id_for_research_chat_mode("intent_clarification") == "assistant.material_alignment.v1"

    rendered = selector.build_system_prompt_for_research_chat_mode("intent_clarification")
    assert "AutoAD Assistant global invariants" in rendered
    assert "AutoAD Research Assistant" in rendered
    assert "资料对齐助手" in rendered
    assert "web_search" in rendered and "web_fetch" in rendered and "git_clone" in rendered

    with pytest.raises(KeyError, match="unsupported research chat mode"):
        selector.prompt_id_for_research_chat_mode("unknown")


def test_prompt_selector_excludes_execution_tools():
    selector = PromptSelector()
    blocked = {"runner_execute", "patch_apply", "benchmark_run", "experiment_execution"}

    for mode in get_args(AssistantMode):
        profile = selector.profile_for_mode(mode)
        rendered = selector.build_system_prompt_for_mode(mode)
        forbidden_outputs = set(profile.io.forbidden_outputs)
        assert blocked.isdisjoint(forbidden_outputs)
        assert blocked.isdisjoint(set(rendered.split()))


def test_selector_routes_v2_components_through_registry():
    selector = PromptSelector()

    assert V2_COMPONENT_TO_PROMPT_ID == {
        "source_action_planner": "assistant.v2.source_action_plan.v1",
        "turn_gate": "assistant.v2.turn_gate.v1",
        "need_discovery": "assistant.v2.need_discovery.v1",
        "reply_planner": "assistant.v2.reply_plan.v1",
    }
    for component, prompt_id in V2_COMPONENT_TO_PROMPT_ID.items():
        assert selector.prompt_id_for_v2_component(component) == prompt_id
        rendered = selector.build_system_prompt_for_v2_component(component)
        profile = selector._registry.require(prompt_id)
        assert rendered == profile.system_prompt
        assert "AutoAD Assistant global invariants" not in rendered

    with pytest.raises(KeyError, match="unsupported v2 prompt component"):
        selector.prompt_id_for_v2_component("unknown")


def test_v2_message_builders_use_registered_prompt_profiles(monkeypatch):
    selector = PromptSelector()

    source_messages = _build_source_action_messages(
        user_input="clone repo",
        transcript_tail=[],
        existing_contract_draft={},
        source_registry=[],
        pending_jobs=[],
        tool_capabilities=[],
        repository_hints=[],
    )
    turn_messages = _build_turn_gate_messages(
        user_input="继续",
        transcript_tail=[],
        existing_contract_draft={},
        created_sources=[],
        created_jobs=[],
        answerability={},
    )
    need_messages = _build_need_discovery_messages(
        user_input="做实验",
        transcript_tail=[],
        existing_contract_draft={},
        source_registry=[],
        usable_evidence=[],
        created_jobs=[],
        current_stage_goal="generate_plan",
        answerability={},
        run_artifacts_summary={},
    )

    captured: dict[str, object] = {}

    def fake_call(api_key, provider_base_url, messages, **kwargs):
        captured["messages"] = messages
        return {"reply": '{"reply_to_user":"ok"}', "error": ""}

    monkeypatch.setattr("autoad_researcher.ui.chat_client.call_research_chat", fake_call)
    _llm_reply({}, "你好", "sk-test", "https://example.test")
    reply_messages = captured["messages"]

    assert source_messages[0]["content"] == selector.build_system_prompt_for_v2_component("source_action_planner")
    assert turn_messages[0]["content"] == selector.build_system_prompt_for_v2_component("turn_gate")
    assert need_messages[0]["content"] == selector.build_system_prompt_for_v2_component("need_discovery")
    assert reply_messages[0]["content"] == selector.build_system_prompt_for_v2_component("reply_planner")
