"""Tests for AutoAD Assistant prompt registry foundations."""

import hashlib
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


def test_progress_digest_profile_is_user_visible_but_hides_raw_internals():
    profile = get_default_prompt_registry().require("assistant.progress_digest.v1")

    assert profile.visibility == "user_visible"
    assert profile.layer == "user_facing_progress"
    assert "raw_path" in profile.io.forbidden_outputs
    assert "raw_run_id" in profile.io.forbidden_outputs
    assert "不展示 raw path" in profile.system_prompt
    assert "Use WhatWeKnow when available." in profile.system_prompt


def test_v2_core_prompt_profiles_are_registered_with_io_contracts():
    registry = get_default_prompt_registry()
    expected = {
        "assistant.v2.conversation_route.v1": ("ConversationRouteDecision", "assistant_state", "understanding_intent"),
        "assistant.v2.source_action_plan.v1": ("SourceActionPlan", "assistant_state", "registering_sources"),
        "assistant.v2.turn_gate.v1": ("TurnGateDecision", "assistant_state", "understanding_intent"),
        "assistant.v2.need_discovery.v1": ("RequiredNeedSpec", "schema_bound_draft", None),
        "assistant.v2.reply_plan.v1": ("V2ReplyPlanJSON", "assistant_state", "guiding_materials"),
    }

    for prompt_id, (output_schema, layer, stage) in expected.items():
        profile = registry.require(prompt_id)
        assert profile.prompt_version == "v1"
        assert profile.layer == layer
        assert profile.assistant_stage == stage
        assert profile.io.output_schema == output_schema
        assert profile.io.input_schema
        assert profile.io.forbidden_outputs
        assert profile.source_references

    assert "ConversationRouter" in registry.require("assistant.v2.conversation_route.v1").system_prompt
    assert "SourceActionPlanner" in registry.require("assistant.v2.conversation_route.v1").system_prompt
    assert "TurnGateDecision JSON" in registry.require("assistant.v2.conversation_route.v1").system_prompt
    assert "SourceActionPlanner" in registry.require("assistant.v2.source_action_plan.v1").system_prompt
    assert "TurnGateDecision JSON" in registry.require("assistant.v2.turn_gate.v1").system_prompt
    assert "RequiredNeedSpec JSON" in registry.require("assistant.v2.need_discovery.v1").system_prompt
    assert "reply_to_user" in registry.require("assistant.v2.reply_plan.v1").system_prompt
    assert "具体提升多少是可选目标" in registry.require("assistant.v2.need_discovery.v1").system_prompt
    assert "优先理解当前用户消息与最近一轮 assistant 回复的关系" in registry.require("assistant.v2.reply_plan.v1").system_prompt
    assert "你不能独立发起合同确认" in registry.require("assistant.v2.reply_plan.v1").system_prompt
    assert registry.require("assistant.v2.reply_plan.v1").visibility == "user_visible"


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


def test_v2_prompt_profiles_match_reviewed_content_hashes():
    registry = get_default_prompt_registry()
    expected = {
        "assistant.v2.conversation_route.v1": "fe34d255d641b6ae141dd8ec6c617f0e23aee29b5300f1e82b40ad2b9fdc654f",
        "assistant.v2.source_action_plan.v1": "12672a757d47ef7c181d3e9b87c1b6b75a86ed3be85f57f2b783f7824b4db763",
        "assistant.v2.turn_gate.v1": "912c5c3ebcd49fca880a5a614b8627a2f86f27cb88b60cb8eb663ec62c03a769",
        "assistant.v2.need_discovery.v1": "583a477ce4b6ae5449ed76f2ab1f4a46d34870d7500e83efe855142369073158",
        "assistant.v2.reply_plan.v1": "ec993d78d020dd0aab7e8ae2dba813f278c4b211ea38dfadaf60a1faf6619570",
    }

    actual = {
        prompt_id: hashlib.sha256(registry.require(prompt_id).system_prompt.encode("utf-8")).hexdigest()
        for prompt_id in expected
    }

    assert actual == expected
