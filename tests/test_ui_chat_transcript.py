"""Tests for chat_transcript.py."""

import json
import tempfile
from pathlib import Path

from autoad_researcher.ui.chat_transcript import load_transcript, save_transcript


class TestChatTranscript:
    def test_save_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "run_t"
            save_transcript(run_dir, "run_explanation", "user", "hello")
            save_transcript(run_dir, "run_explanation", "assistant", "hi back")
            entries = load_transcript(run_dir)
            assert len(entries) == 2
            assert entries[0]["role"] == "user"
            assert entries[1]["role"] == "assistant"

    def test_empty_transcript_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = load_transcript(Path(tmp) / "runs" / "no_run")
            assert entries == []

    def test_context_refs_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "run_t"
            refs = ["final_facts.json", "execution_manifest.json"]
            save_transcript(run_dir, "next_experiment", "assistant", "try harder", refs)
            entries = load_transcript(run_dir)
            assert entries[0]["context_refs"] == refs

    def test_timestamp_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "run_t"
            save_transcript(run_dir, "intent_clarification", "user", "hello")
            entries = load_transcript(run_dir)
            assert "timestamp" in entries[0]
            assert "mode" in entries[0]

    def test_corrupted_lines_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "run_t"
            run_dir.joinpath("ui_chat").mkdir(parents=True)
            path = run_dir / "ui_chat" / "chat_transcript.jsonl"
            path.write_text('{"bad":\n', encoding="utf-8")  # invalid JSON
            entries = load_transcript(run_dir)
            assert entries == []
