from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from autoad_researcher.server.ws_manager import manager

router = APIRouter()


@router.websocket("/api/runs/{run_id}/ws")
async def websocket_endpoint(ws: WebSocket, run_id: str):
    await manager.connect(run_id, ws)
    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type", "")

            if msg_type == "chat.send":
                user_input = data.get("user_input", "")
                await manager.broadcast(run_id, {
                    "type": "assistant.delta",
                    "content": f"收到: {user_input}",
                })

            elif msg_type == "ping":
                await ws.send_json({"type": "pong"})

    except WebSocketDisconnect:
        manager.disconnect(run_id, ws)
    except Exception:
        manager.disconnect(run_id, ws)
