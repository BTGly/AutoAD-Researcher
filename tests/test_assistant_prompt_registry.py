"""Tests for AutoAD Assistant prompt registry foundations."""

import pytest

from autoad_researcher.assistant import (
    GLOBAL_INVARIANTS_PROMPT_ID,
    GLOBAL_INVARIANTS_TEXT,
    PromptIOContract,
    PromptProfile,
    get_default_prompt_registry,
)
from autoad_researcher.ui.chat_prompts import (
    NEXT_EXPERIMENT_PROMPT,
    RUN_EXPLANATION_PROMPT,
)


def test_default_registry_has_unique_prompt_ids():
    registry = get_default_prompt_registry()
    prompt_ids = [profile.prompt_id for profile in registry.all_profiles()]

    assert len(prompt_ids) == len(set(prompt_ids))
    assert GLOBAL_INVARIANTS_PROMPT_ID in prompt_ids


def test_existing_progress_chat_prompts_are_mapped_into_registry():
    registry = get_default_prompt_registry()

    assert registry.require("assistant.run_explanation.v1").system_prompt == RUN_EXPLANATION_PROMPT
    assert registry.require("assistant.next_experiment.v1").system_prompt == NEXT_EXPERIMENT_PROMPT


def test_assistant_state_prompts_are_split_by_purpose():
    registry = get_default_prompt_registry()
    collecting = registry.require("assistant.collecting_goal.v1")
    guiding = registry.require("assistant.guiding_materials.v1")
    read_only = registry.require("assistant.read_only_exploration.v1")
    exploration = registry.require("assistant.material_exploration.v1")
    alignment = registry.require("assistant.material_alignment.v1")
    understanding = registry.require("assistant.understanding_intent.v1")
    confirming = registry.require("assistant.confirming_task_draft.v1")

    assert collecting.system_prompt != understanding.system_prompt
    assert "只问 1-3 个最关键的问题" in collecting.system_prompt
    assert "P0" in guiding.system_prompt and "P1" in guiding.system_prompt and "P2" in guiding.system_prompt
    assert "只读资料探索助手" in read_only.system_prompt
    assert "资料探索助手" in exploration.system_prompt
    assert "AutoAD Research Assistant" in alignment.system_prompt
    assert "资料对齐助手" in alignment.system_prompt
    assert "readable_artifacts" in alignment.system_prompt
    assert "paper_context" in alignment.system_prompt
    assert "paper_summary.json" in alignment.system_prompt
    assert "paper.md" in alignment.system_prompt
    assert "sections.json" in alignment.system_prompt
    assert "blocks.jsonl" in alignment.system_prompt and "跳过乱码块" in alignment.system_prompt
    assert "候选参数" in understanding.system_prompt
    assert "确认 / 需要修改 / 补充材料" in confirming.system_prompt
    assert understanding.visibility == "internal"
    assert collecting.visibility == "user_visible"


def test_user_visible_prompt_rendering_inherits_global_invariants():
    registry = get_default_prompt_registry()

    rendered = registry.build_system_prompt("assistant.collecting_goal.v1")

    assert GLOBAL_INVARIANTS_TEXT.strip().splitlines()[0] in rendered
    assert "Do not fabricate execution results" in rendered
    assert "你是 AutoAD-Researcher 的研究入口助手" in rendered


def test_global_invariants_are_not_duplicated_for_global_profile():
    registry = get_default_prompt_registry()

    rendered = registry.build_system_prompt(GLOBAL_INVARIANTS_PROMPT_ID)

    assert rendered == GLOBAL_INVARIANTS_TEXT


def test_registry_selects_by_layer_and_stage():
    registry = get_default_prompt_registry()

    state_prompts = registry.by_layer("assistant_state")
    collecting = registry.by_stage("collecting_goal")
    progress = registry.by_stage("progress_reporting")

    assert {profile.prompt_id for profile in state_prompts} >= {
        "assistant.collecting_goal.v1",
        "assistant.guiding_materials.v1",
        "assistant.read_only_exploration.v1",
        "assistant.material_exploration.v1",
        "assistant.material_alignment.v1",
        "assistant.understanding_intent.v1",
        "assistant.confirming_task_draft.v1",
    }
    assert [profile.prompt_id for profile in collecting] == ["assistant.collecting_goal.v1"]
    assert {profile.prompt_id for profile in progress} >= {
        "assistant.run_explanation.v1",
        "assistant.next_experiment.v1",
        "assistant.progress_digest.v1",
    }


def test_prompt_profiles_reference_repo_docs_not_local_scratch_paths():
    registry = get_default_prompt_registry()

    for profile in registry.all_profiles():
        for ref in profile.source_references:
            assert not ref.startswith("参考/"), ref
            assert ref.startswith("docs/") or ref.startswith("src/"), ref


def test_research_task_draft_profile_separates_candidate_and_confirmed_parameters():
    profile = get_default_prompt_registry().require("assistant.research_task_draft.v1")

    assert profile.layer == "schema_bound_draft"
    assert profile.io.output_schema == "ResearchTaskDraftV1"
    assert "task/research_task_draft.json" in profile.io.produced_artifacts
    assert "candidate_as_confirmed" in profile.io.forbidden_outputs
    assert "confirmed_parameters" in profile.system_prompt
    assert "candidate_parameters" in profile.system_prompt
    assert "Do not interrogate. Propose first." in profile.system_prompt
    assert "Goal vs Approach" in profile.system_prompt


def test_research_dialogue_profile_registers_production_contract():
    profile = get_default_prompt_registry().require("assistant.research_dialogue.v1")

    assert profile.assistant_stage == "understanding_intent"
    assert profile.io.input_schema == "ResearchDialogueContext"
    assert profile.io.output_schema == "ResearchDialogueResponse"
    assert profile.io.produced_artifacts == ["summary.json"]
    assert "preliminary hypothesis" in profile.system_prompt
    assert "request_source_removal" not in profile.system_prompt
    assert "source_action" in profile.system_prompt


def test_progress_digest_profile_is_user_visible_but_hides_raw_internals():
    profile = get_default_prompt_registry().require("assistant.progress_digest.v1")

    assert profile.visibility == "user_visible"
    assert profile.layer == "user_facing_progress"
    assert "raw_path" in profile.io.forbidden_outputs
    assert "raw_run_id" in profile.io.forbidden_outputs
    assert "不展示 raw path" in profile.system_prompt
    assert "Use WhatWeKnow when available." in profile.system_prompt


def test_prompt_io_contract_rejects_unsafe_artifact_paths():
    with pytest.raises(ValueError, match="run-relative"):
        PromptIOContract(required_artifacts=["/tmp/raw.txt"])

    with pytest.raises(ValueError, match="run-relative"):
        PromptIOContract(produced_artifacts=["task/../escape.json"])


def test_prompt_profile_rejects_bad_prompt_id_and_duplicate_registration():
    with pytest.raises(ValueError, match="prompt_id"):
        PromptProfile(
            prompt_id="Assistant.Bad",
            prompt_version="v1",
            layer="global_invariants",
            title="bad",
            description="bad",
            system_prompt="bad",
        )

    registry = get_default_prompt_registry()
    profile = registry.require("assistant.collecting_goal.v1")
    with pytest.raises(ValueError, match="duplicate"):
        registry.register(profile)


def test_user_visible_profiles_have_forbidden_outputs_or_schema_contracts():
    registry = get_default_prompt_registry()

    for profile in registry.user_visible():
        assert profile.io.output_schema is not None
        assert profile.io.forbidden_outputs
        assert "执行" not in profile.description or "without" in profile.description or "Explains" in profile.description
