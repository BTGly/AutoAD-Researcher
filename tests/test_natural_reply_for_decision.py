"""Tests for _natural_reply_for_decision history/sanitizer/concise prompt."""
import json
from pathlib import Path

from autoad_researcher.ui.research_chat import (
    _natural_reply_for_decision,
    _sanitize_response_context_for_llm,
)
from autoad_researcher.assistant.intent_action import ActionDecision


class FakeResult:
    def __init__(self, reply="ok", error=None):
        self.reply = reply
        self.error = error

    def get(self, key):
        return getattr(self, key, None)


def _fake_call(api_key=None, provider_base_url=None, messages=None):
    return FakeResult()


def _make_decision() -> ActionDecision:
    return ActionDecision(
        selected_action="answer_directly",
        response_mode="parsing_failed_status",
        snapshot_sha256="abc123",
        reason="test",
    )


def test_sanitize_removes_unknown_legacy_parser():
    ctx = {
        "facts": {
            "parse_attempts": [
                {"parse_attempt_id": "pa_001", "parser": "unknown_legacy", "status": "ok"},
                {"parse_attempt_id": "pa_002", "parser": "mineru", "status": "failed"},
            ]
        }
    }
    clean = _sanitize_response_context_for_llm(ctx)
    attempts = clean["facts"]["parse_attempts"]
    assert "parser" not in attempts[0]
    assert "legacy_parse_attempt" not in attempts[0]
    assert attempts[1]["parser"] == "mineru"


def test_sanitize_preserves_non_legacy():
    ctx = {"facts": {"parse_attempts": [{"parser": "mineru", "status": "ok"}]}}
    clean = _sanitize_response_context_for_llm(ctx)
    assert clean["facts"]["parse_attempts"][0]["parser"] == "mineru"


def test_natural_reply_for_decision_injects_history(monkeypatch, tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "ui_chat").mkdir(parents=True)

    transcript = [
        {"role": "user", "content": "你能看论文了吗"},
        {"role": "assistant", "content": "上次解析失败"},
    ]
    transcript_path = run_dir / "ui_chat" / "chat_transcript.jsonl"
    with open(transcript_path, "w") as f:
        for entry in transcript:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    captured_messages: list = []

    def fake_call(api_key=None, provider_base_url=None, messages=None):
        captured_messages.extend(messages)
        return FakeResult()

    import autoad_researcher.ui.research_chat as mod
    original = getattr(mod, "call_research_chat", None)
    mod.call_research_chat = fake_call
    try:
        _natural_reply_for_decision(
            run_dir=run_dir,
            decision=_make_decision(),
            api_key="sk-test",
            provider_url="https://test",
            user_input="为什么失败了",
        )
    finally:
        if original:
            mod.call_research_chat = original

    assert any("你能看论文了吗" in json.dumps(m, ensure_ascii=False) for m in captured_messages), \
        "history not injected"


def test_natural_reply_for_decision_prompt_is_concise(monkeypatch, tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "ui_chat").mkdir(parents=True)
    transcript_path = run_dir / "ui_chat" / "chat_transcript.jsonl"
    transcript_path.write_text("")

    captured: list = []

    def fake_call(api_key=None, provider_base_url=None, messages=None):
        captured.extend(messages)
        return FakeResult()

    import autoad_researcher.ui.research_chat as mod
    original = getattr(mod, "call_research_chat", None)
    mod.call_research_chat = fake_call
    try:
        _natural_reply_for_decision(
            run_dir=run_dir,
            decision=_make_decision(),
            api_key="sk-test",
            provider_url="https://test",
            user_input="测试",
        )
    finally:
        if original:
            mod.call_research_chat = original

    system_text = " ".join(
        m["content"] for m in captured if m["role"] == "system"
    )
    assert "4 行" in system_text or "不超过" in system_text, \
        "no conciseness constraint in system prompt"
    assert "不重复" in system_text or "不要重复" in system_text, \
        "no anti-repetition instruction in system prompt"
    assert "不要以你好开头" in system_text or "不以你好" in system_text, \
        "missing instruction to avoid opening salutation"


def test_natural_reply_for_decision_prompt_mentions_github_source_registration(monkeypatch, tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "ui_chat").mkdir(parents=True)
    (run_dir / "ui_chat" / "chat_transcript.jsonl").write_text("")

    captured: list = []

    def fake_call(api_key=None, provider_base_url=None, messages=None):
        captured.extend(messages)
        return FakeResult()

    import autoad_researcher.ui.research_chat as mod
    original = getattr(mod, "call_research_chat", None)
    mod.call_research_chat = fake_call
    try:
        _natural_reply_for_decision(
            run_dir=run_dir,
            decision=_make_decision(),
            api_key="sk-test",
            provider_url="https://test",
            user_input="这个 GitHub 仓库能看吗",
        )
    finally:
        if original:
            mod.call_research_chat = original

    system_text = " ".join(m["content"] for m in captured if m["role"] == "system")
    assert "GitHub 仓库链接" in system_text
    assert "clone" in system_text
    assert "作为 source" in system_text
