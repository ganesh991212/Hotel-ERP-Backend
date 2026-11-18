import asyncio
from uuid import UUID

import jwt
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.db import platform_session, tenant_session
from app.core.security import decode_access_token, now, sha256
from app.models import Outlet, TableSession
from app.realtime.hub import diner_channel, hub, outlet_channel

router = APIRouter()


async def _pump(ws: WebSocket, channel: str) -> None:
    q = hub.subscribe(channel)
    try:
        await ws.send_json({"type": "hello", "channel": channel})
        while True:
            try:
                msg = await asyncio.wait_for(q.get(), timeout=25)
                await ws.send_json(msg)
            except TimeoutError:
                await ws.send_json({"type": "ping"})
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        hub.unsubscribe(channel, q)


@router.websocket("/ws/outlets/{outlet_id}")
async def outlet_ws(ws: WebSocket, outlet_id: UUID, token: str) -> None:
    """Staff channel. Browsers cannot set headers on WebSockets, so the access token rides in the query."""
    try:
        claims = decode_access_token(token)
        async with tenant_session(UUID(claims["hid"])) as db:
            ok = (await db.execute(select(Outlet.id).where(Outlet.id == outlet_id))).first()  # RLS-scoped
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
        ok = None
    if not ok:
        await ws.close(code=4401)
        return
    await ws.accept()
    await _pump(ws, outlet_channel(outlet_id))


@router.websocket("/ws/d")
async def diner_ws(ws: WebSocket, token: str) -> None:
    async with platform_session() as db:
        s = (
            await db.execute(select(TableSession).where(TableSession.token_hash == sha256(token)))
        ).scalar_one_or_none()
    if not s or s.closed_at or s.expires_at < now():
        await ws.close(code=4401)
        return
    await ws.accept()
    await _pump(ws, diner_channel(s.id))
