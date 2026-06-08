"""WebSocket route — clients subscribe to a sweepstake room for live events."""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.websocket.manager import manager

router = APIRouter()


@router.websocket("/ws/sweepstakes/{sid}")
async def sweepstake_ws(websocket: WebSocket, sid: str):
    await manager.connect(sid, websocket)
    try:
        # Send an immediate hello so the client knows the socket is live.
        await websocket.send_json({"event": "connected", "data": {"room": sid}})
        while True:
            # We don't require client messages; keep the socket open. Any inbound
            # text acts as a ping/keepalive.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(sid, websocket)
    except Exception:
        manager.disconnect(sid, websocket)
