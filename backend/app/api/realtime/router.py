"""WebSocket endpoint for realtime data (REQ-008)."""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.realtime.hub import RealtimeHub

router = APIRouter()


@router.websocket("/ws")
async def realtime_ws(websocket: WebSocket) -> None:
    """Multiplexed realtime stream.

    Clients send ``{"action":"subscribe","channels":["position","equity"],
    "run_id":"..."}`` and then receive matching events as JSON text frames.
    """
    hub: RealtimeHub = websocket.app.state.hub
    client = await hub.connect(websocket)
    channels = [
        "order",
        "fill",
        "position",
        "signal",
        "equity",
        "error",
        "market",
        "candle",
        "trade_level",
        "watchlist",
        "symbol_summary",
        "run",
    ]
    try:
        await websocket.send_json({"type": "hello", "channels": channels})
        while True:
            message = await websocket.receive_json()
            hub.configure(client, message)
    except WebSocketDisconnect:
        pass
    finally:
        await hub.disconnect(client)
