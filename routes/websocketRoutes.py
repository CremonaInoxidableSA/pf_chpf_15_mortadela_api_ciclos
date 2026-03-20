from fastapi import APIRouter, WebSocket

from services.ciclosWS import ws_ciclos_endpoint

RouterWebSocket = APIRouter(tags=["WebSocket"])


@RouterWebSocket.websocket("/ws/ciclos")
async def websocket_ciclos(ws: WebSocket):
    await ws_ciclos_endpoint(ws)
