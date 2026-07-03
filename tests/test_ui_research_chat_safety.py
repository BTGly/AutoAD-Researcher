"""Tests for research_chat.py safety boundaries."""

from pathlib import Path

from autoad_researcher.ui.research_chat import _SAFETY_WARNING, _MODE_LABELS


def test_safety_warning_not_empty():
    assert len(_SAFETY_WARNING) > 10
    assert "不会修改" in _SAFETY_WARNING or "不" in _SAFETY_WARNING


def test_mode_labels_have_all_modes():
    assert set(_MODE_LABELS.keys()) == {
        "intent_clarification", "run_explanation", "next_experiment",
    }


def test_mode_labels_are_chinese():
    for label in _MODE_LABELS.values():
        # At least some Chinese characters
        assert any('\u4e00' <= c <= '\u9fff' for c in label)
