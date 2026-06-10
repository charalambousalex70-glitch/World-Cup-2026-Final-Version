"""WebSocket connection manager.

Clients connect to a room identified by sweepstake_id. The backend broadcasts
JSON events (leaderboard updates, score changes, eliminations) to every socket
in that room so the UI updates without polling.
"""
import json
from collections import defaultdict

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        # sweepstake_id (str) -> set of sockets
        self._rooms: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, room: str, ws: WebSocket) -> None:
        await ws.accept()
        self._rooms[room].add(ws)

    def disconnect(self, room: str, ws: WebSocket) -> None:
        self._rooms[room].discard(ws)
        if not self._rooms[room]:
            self._rooms.pop(room, None)

    async def broadcast(self, room: str, event: str, payload: dict) -> None:
        message = json.dumps({"event": event, "data": payload}, default=str)
        dead: list[WebSocket] = []
        for ws in self._rooms.get(room, set()):
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(room, ws)


manager = ConnectionManager()
