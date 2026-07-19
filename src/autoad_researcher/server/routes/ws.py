import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from autoad_researcher.assistant.v2.event_service import load_events_since
from autoad_researcher.core.run_id import run_dir_path
from autoad_researcher.server.config import RUNS_ROOT
from autoad_researcher.server.ws_manager import manager

router = APIRouter()
TRANSIENT_EVENT_PREFIXES = ("toast.",)


@router.websocket("/api/runs/{run_id}/ws")
async def websocket_endpoint(ws: WebSocket, run_id: str):
    try:
        run_dir = run_dir_path(RUNS_ROOT, run_id)
    except ValueError:
        await ws.close(code=1008)
        return
    await manager.connect(run_id, ws)
    last_event_id = 0

    # Replay existing events on connect
    for evt in load_events_since(run_dir, last_event_id):
        try:
            if not _is_transient_event(evt):
                await ws.send_json({"type": evt["type"], **(evt.get("payload", {}))})
            last_event_id = evt["event_id"]
        except Exception:
            break

    # Background polling task — push new events without waiting for client messages
    async def poll_events():
        nonlocal last_event_id
        while True:
            try:
                for evt in load_events_since(run_dir, last_event_id):
                    await ws.send_json({"type": evt["type"], **(evt.get("payload", {}))})
                    last_event_id = evt["event_id"]
            except Exception:
                break
            await asyncio.sleep(0.8)

    poll_task = asyncio.create_task(poll_events())

    try:
        while True:
            data = await ws.receive_json()
            if data.get("type") == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        poll_task.cancel()
        manager.disconnect(run_id, ws)


def _is_transient_event(evt: dict) -> bool:
    event_type = str(evt.get("type") or "")
    return event_type.startswith(TRANSIENT_EVENT_PREFIXES)
