from __future__ import annotations

import asyncio
from unittest.mock import Mock

import pytest

from autoad_researcher.server import main


@pytest.mark.asyncio
async def test_lifespan_disabled_worker_still_resets_llm_broker(monkeypatch):
    monkeypatch.setenv("AUTOAD_EMBEDDED_WORKER", "0")
    reset_broker = Mock()
    monkeypatch.setattr("autoad_researcher.assistant.llm_runtime.reset_llm_call_broker", reset_broker)

    async with main.app.router.lifespan_context(main.app):
        assert main.app.state.embedded_worker_task is None

    reset_broker.assert_called_once_with()
    assert main.app.state.embedded_worker_task is None


@pytest.mark.asyncio
async def test_lifespan_cancels_worker_and_clears_app_state(monkeypatch):
    started = asyncio.Event()
    reset_broker = Mock()
    monkeypatch.setenv("AUTOAD_EMBEDDED_WORKER", "1")
    monkeypatch.setattr("autoad_researcher.server.worker_runtime.embedded_worker_enabled", lambda: True)

    async def fake_worker_loop():
        started.set()
        await asyncio.Future()

    monkeypatch.setattr("autoad_researcher.server.worker_runtime.embedded_worker_loop", fake_worker_loop)
    monkeypatch.setattr("autoad_researcher.assistant.llm_runtime.reset_llm_call_broker", reset_broker)

    async with main.app.router.lifespan_context(main.app):
        await started.wait()
        worker_task = main.app.state.embedded_worker_task
        assert worker_task is not None
        assert not worker_task.done()

    assert worker_task.cancelled()
    assert main.app.state.embedded_worker_task is None
    reset_broker.assert_called_once_with()
