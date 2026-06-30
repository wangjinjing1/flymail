import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from config import SESSION_COOKIE_NAME
from services.security import parse_session_cookie
from services.sync import sync_service
from utils.logger import get_logger

logger = get_logger("websocket")
router = APIRouter(tags=["WebSocket"])


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    payload = parse_session_cookie(websocket.cookies.get(SESSION_COOKIE_NAME))
    if not payload or not payload.get("uid"):
        await websocket.close(code=4401)
        return

    uid = payload["uid"]
    await websocket.accept()
    await sync_service.add_client(websocket, uid)
    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=60)
                if data:
                    continue
            except asyncio.TimeoutError:
                try:
                    await websocket.send_json({"type": "ping"})
                    await asyncio.wait_for(websocket.receive_text(), timeout=10)
                except Exception:
                    break
    except WebSocketDisconnect:
        logger.info("websocket disconnected uid=%s", uid)
    finally:
        await sync_service.remove_client(websocket)
