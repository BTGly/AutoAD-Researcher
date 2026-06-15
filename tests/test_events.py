"""测试 EventStore。"""

import json

import pytest

from autoad_researcher.core import EventRecord, EventStore


class TestEventStore:
    def test_record_run_created(self, tmp_path):
        events = EventStore(runs_root=tmp_path)

        event = events.record_run_created(
            "run_demo",
            payload={"source": "test"},
        )

        assert event.event_type == "run_created"
        assert event.run_id == "run_demo"
        assert event.payload["source"] == "test"

        path = tmp_path / "run_demo" / "events.jsonl"
        assert path.exists()

        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 1

        data = json.loads(lines[0])
        assert data["event_type"] == "run_created"
        assert data["run_id"] == "run_demo"

    def test_read_events(self, tmp_path):
        events = EventStore(runs_root=tmp_path)

        events.record_run_created("run_demo")
        events.append("run_demo", "artifact_written", {"artifact": "x.json"})

        loaded = events.read_events("run_demo")

        assert len(loaded) == 2
        assert all(isinstance(event, EventRecord) for event in loaded)
        assert loaded[0].event_type == "run_created"
        assert loaded[1].event_type == "artifact_written"

    def test_read_missing_events_returns_empty_list(self, tmp_path):
        events = EventStore(runs_root=tmp_path)
        assert events.read_events("run_demo") == []

    def test_invalid_event_type_rejected(self, tmp_path):
        events = EventStore(runs_root=tmp_path)
        with pytest.raises(ValueError):
            events.append("run_demo", "unknown_event")

    @pytest.mark.parametrize(
        "bad_run_id",
        [
            "",
            ".",
            "..",
            "...",
            "../escape",
            "foo/bar",
            r"foo\bar",
        ],
    )
    def test_invalid_run_id_rejected(self, tmp_path, bad_run_id):
        events = EventStore(runs_root=tmp_path)
        with pytest.raises(ValueError):
            events.record_run_created(bad_run_id)

    def test_invalid_jsonl_raises(self, tmp_path):
        events = EventStore(runs_root=tmp_path)

        run_dir = tmp_path / "run_demo"
        run_dir.mkdir(parents=True)
        path = run_dir / "events.jsonl"
        path.write_text("{not valid json}\n", encoding="utf-8")

        with pytest.raises(ValueError):
            events.read_events("run_demo")
