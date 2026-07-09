from autoad_researcher.server.routes.ws import _is_transient_event


def test_ws_replay_treats_toast_events_as_transient():
    assert _is_transient_event({"type": "toast.success"}) is True
    assert _is_transient_event({"type": "toast.error"}) is True
    assert _is_transient_event({"type": "job.completed"}) is False
