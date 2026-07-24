from autoad_researcher.server.routes.ws import _event_message


def test_websocket_event_keeps_cursor_metadata():
    message = _event_message({
        "event_id": 7,
        "type": "job.queued",
        "created_at": "2026-07-24T00:00:00+00:00",
        "payload": {"job_id": "job_1", "source_id": "src_1"},
    })

    assert message == {
        "type": "job.queued",
        "event_id": 7,
        "created_at": "2026-07-24T00:00:00+00:00",
        "job_id": "job_1",
        "source_id": "src_1",
    }
