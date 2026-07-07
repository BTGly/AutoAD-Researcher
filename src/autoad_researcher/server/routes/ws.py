import asyncio
import os
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from autoad_researcher.assistant.v2.event_service import load_events_since
from autoad_researcher.server.ws_manager import manager

RUNS_ROOT = os.environ.get("AUTOAD_RUNS_ROOT", "runs")
router = APIRouter()


@router.websocket("/api/runs/{run_id}/ws")
async def websocket_endpoint(ws: WebSocket, run_id: str):
    run_dir = Path(RUNS_ROOT) / run_id
    await manager.connect(run_id, ws)
    last_event_id = 0

    # Replay existing events on connect
    for evt in load_events_since(run_dir, last_event_id):
        try:
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
            msg_type = data.get("type", "")
            if msg_type == "ping":
                await ws.send_json({"type": "pong"})
            elif msg_type == "chat.send":
                await manager.broadcast(run_id, {
                    "type": "assistant.delta",
                    "content": "收到: " + data.get("user_input", ""),
                })
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        poll_task.cancel()
        manager.disconnect(run_id, ws)
