"""Tests for research_chat.py safety boundaries."""

from autoad_researcher.ui.research_chat import _SAFETY_WARNING, _MODE_LABELS, _extract_intent_draft


def test_safety_warning_not_empty():
    assert len(_SAFETY_WARNING) > 10
    assert "不会修改" in _SAFETY_WARNING or "不" in _SAFETY_WARNING


def test_mode_labels_have_all_modes():
    assert set(_MODE_LABELS.keys()) == {
        "intent_clarification", "run_explanation", "next_experiment",
    }


def test_mode_labels_are_chinese():
    for label in _MODE_LABELS.values():
        assert any('\u4e00' <= c <= '\u9fff' for c in label)


def test_extract_intent_draft_parses_research_goal():
    text = """研究目标：降低 PatchCore 运行时峰值显存。

优化指标：
- peak_gpu_memory_mb
- wall_time_seconds

禁止修改范围：
- configs/
- tests/
"""
    draft = _extract_intent_draft(text)
    assert "PatchCore" in draft["research_goal"]
    assert "peak_gpu_memory_mb" in draft["primary_metrics"]
    assert "configs/" in draft["forbidden_change_scope"]


def test_extract_intent_draft_empty_for_none():
    text = "hello world"
    draft = _extract_intent_draft(text)
    assert draft["research_goal"] == ""
    assert draft["primary_metrics"] == []


def test_extract_intent_draft_always_returns_all_keys():
    draft = _extract_intent_draft("")
    expected = {"research_goal", "primary_metrics", "guardrail_metrics",
                "allowed_change_scope", "forbidden_change_scope",
                "success_criteria", "constraints", "user_idea"}
    assert set(draft.keys()) == expected
