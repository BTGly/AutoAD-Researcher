from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from autoad_researcher.assistant.v2.event_service import load_events_since
from autoad_researcher.server.ws_manager import manager

router = APIRouter()


@router.websocket("/api/runs/{run_id}/ws")
async def websocket_endpoint(ws: WebSocket, run_id: str):
    await manager.connect(run_id, ws)
    last_event_id = 0

    # Replay un-sent events after connect
    events = load_events_since(run_id, last_event_id)
    for evt in events:
        try:
            await ws.send_json({"type": evt["type"], **(evt.get("payload", {}))})
            last_event_id = evt["event_id"]
        except Exception:
            break

    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "chat.send":
                await manager.broadcast(run_id, {
                    "type": "assistant.delta",
                    "content": "收到: " + data.get("user_input", ""),
                })

            elif msg_type == "ping":
                await ws.send_json({"type": "pong"})

            # Poll new events
            new_events = load_events_since(run_id, last_event_id)
            for evt in new_events:
                try:
                    await ws.send_json({"type": evt["type"], **(evt.get("payload", {}))})
                    last_event_id = evt["event_id"]
                except Exception:
                    pass

    except WebSocketDisconnect:
        manager.disconnect(run_id, ws)
    except Exception:
        manager.disconnect(run_id, ws)
