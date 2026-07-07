"""WebSocket connection pool. One run can have multiple connected clients."""

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self._connections: dict[str, list[WebSocket]] = {}

    async def connect(self, run_id: str, ws: WebSocket):
        await ws.accept()
        self._connections.setdefault(run_id, []).append(ws)

    def disconnect(self, run_id: str, ws: WebSocket):
        conns = self._connections.get(run_id)
        if conns and ws in conns:
            conns.remove(ws)

    async def broadcast(self, run_id: str, message: dict):
        for ws in list(self._connections.get(run_id, [])):
            try:
                await ws.send_json(message)
            except Exception:
                self.disconnect(run_id, ws)


manager = ConnectionManager()
