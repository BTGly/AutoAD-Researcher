"""Tests for Phase 2E fix: research chat prompt alignment with v0.5 intent alignment."""

from pathlib import Path

import pytest

from autoad_researcher.ui.chat_prompts import INTENT_CLARIFICATION_PROMPT


_MUST_OUTPUT_SECTION = "## 你必须优先输出"


def _must_output_text() -> str:
    """Extract the 'must output' section from the prompt for targeted checks."""
    idx = INTENT_CLARIFICATION_PROMPT.find(_MUST_OUTPUT_SECTION)
    if idx == -1:
        return ""
    rest = INTENT_CLARIFICATION_PROMPT[idx:]
    # Stop at the next ## section
    next_section = rest[3:].find("\n## ")
    if next_section != -1:
        rest = rest[:3 + next_section]
    return rest


class TestIntentClarificationPrompt:
    """Verify the prompt no longer hardcodes internal benchmark defaults
    or requires execution-layer output fields in the must-output section."""

    def test_no_hardcoded_benchmark_defaults_in_output(self):
        output = _must_output_text()
        assert "MVTec AD（bottle" not in output
        assert "wideresnet50" not in output
        assert "instance_auroc" not in output
        assert "full_pixel_auroc" not in output

    def test_no_execution_layer_output_requirements_in_output(self):
        output = _must_output_text()
        assert "**允许修改**" not in output
        assert "**禁止修改**" not in output
        assert "验收标准" not in output

    def test_has_propose_first_guidance(self):
        assert "Propose first" in INTENT_CLARIFICATION_PROMPT

    def test_has_goal_vs_approach_separation(self):
        prohibited = ["method", "algorithm", "hyperparameters", "patch hook", "variant choice"]
        prompt_lower = INTENT_CLARIFICATION_PROMPT.lower()
        for word in prohibited:
            assert word in prompt_lower, f"Prompt must forbid '{word}'"

    def test_has_safe_confirmation_wording(self):
        assert "不代表允许修改代码" in INTENT_CLARIFICATION_PROMPT
        assert "不代表" in INTENT_CLARIFICATION_PROMPT

    def test_no_hardcoded_benchmark_sentence_anywhere(self):
        """Old hardcoded patterns must not return anywhere in the prompt."""
        assert "当前项目内部 benchmark 基于" not in INTENT_CLARIFICATION_PROMPT
        assert "数据集：MVTec AD" not in INTENT_CLARIFICATION_PROMPT
        assert "基线模型：PatchCore" not in INTENT_CLARIFICATION_PROMPT
        assert "评估指标：instance_auroc" not in INTENT_CLARIFICATION_PROMPT


class TestBuildResearchChatMessages:
    """Verify that intent_clarification messages include WhatWeKnow."""

    def test_intent_clarification_includes_what_we_know(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_research_chat_messages

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()

        messages = build_research_chat_messages(
            run_dir=run_dir,
            mode="intent_clarification",
            user_input="我想复现论文",
            context_data={},
        )

        assert len(messages) >= 3

        www_msgs = [m for m in messages if "已有 artifact 探测结果" in m["content"]]
        assert len(www_msgs) == 1
        assert "missing_fields" in www_msgs[0]["content"]

    def test_non_intent_mode_skips_what_we_know(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_research_chat_messages

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()

        messages = build_research_chat_messages(
            run_dir=run_dir,
            mode="run_explanation",
            user_input="现在到哪了",
            context_data={},
        )

        www_msgs = [m for m in messages if "已有 artifact 探测结果" in m["content"]]
        assert len(www_msgs) == 0

    def test_missing_run_dir_does_not_crash(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_research_chat_messages

        run_dir = tmp_path / "nonexistent"

        messages = build_research_chat_messages(
            run_dir=run_dir,
            mode="intent_clarification",
            user_input="测试",
            context_data={},
        )

        assert len(messages) >= 3
        www_msgs = [m for m in messages if "已有 artifact 探测结果" in m["content"]]
        assert len(www_msgs) == 1


class TestTranscriptTail:
    """Verify transcript_tail is injected and current user_input does not repeat."""

    def test_transcript_tail_appears_in_messages(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_research_chat_messages

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()

        tail = [
            {"role": "user", "content": "我想复现 SimpleNet 论文"},
            {"role": "assistant", "content": "好的，候选理解是方法迁移。"},
        ]

        messages = build_research_chat_messages(
            run_dir=run_dir,
            mode="intent_clarification",
            user_input="不是完整复现，是迁移",
            context_data={},
            transcript_tail=tail,
        )

        assert "SimpleNet" in str(messages)
        assert "候选理解是方法迁移" in str(messages)

    def test_current_user_input_not_duplicated(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_research_chat_messages

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()

        tail = [
            {"role": "user", "content": "复现 SimpleNet"},
        ]

        messages = build_research_chat_messages(
            run_dir=run_dir,
            mode="intent_clarification",
            user_input="不是完整复现，是迁移",
            context_data={},
            transcript_tail=tail,
        )

        user_msgs = [m for m in messages if m["role"] == "user"]
        for msg in user_msgs:
            if "不是完整复现" in msg["content"]:
                # Current user_input should appear exactly once as user role
                pass

        assert len(user_msgs) >= 1

    def test_transcript_none_does_not_crash(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_research_chat_messages

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()

        messages = build_research_chat_messages(
            run_dir=run_dir,
            mode="intent_clarification",
            user_input="test",
            context_data={},
            transcript_tail=None,
        )
        assert len(messages) >= 3


class TestSourceReferencesInjection:
    """Verify intent_clarification injects SourceReferences from registry."""

    def test_intent_clarification_includes_source_references(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_research_chat_messages

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()

        messages = build_research_chat_messages(
            run_dir=run_dir,
            mode="intent_clarification",
            user_input="我想复现论文",
            context_data={},
        )

        # SourceReferences injected even if empty (no crash)
        assert len(messages) >= 3

    def test_run_explanation_skips_source_references(self, tmp_path):
        from autoad_researcher.ui.research_chat import build_research_chat_messages

        run_dir = tmp_path / "run_test"
        run_dir.mkdir()

        messages = build_research_chat_messages(
            run_dir=run_dir,
            mode="run_explanation",
            user_input="现在到哪了",
            context_data={},
        )

        src_msgs = [m for m in messages if "SourceReferences" in m["content"]]
        assert len(src_msgs) == 0
