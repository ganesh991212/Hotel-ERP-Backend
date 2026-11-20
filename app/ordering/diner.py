"""Diner QR flow (docs/06 Diner, docs/08 QR abuse controls). No staff token; a short-lived session token."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Header
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.catalog.options import for_diner, options_for, with_options
from app.core.config import get_settings
from app.core.db import platform_session, tenant_session
from app.core.deps import load_entitlements
from app.core.errors import ApiError, forbidden, not_found
from app.core.security import new_opaque_token, now, sha256
from app.kitchen import service as kitchen
from app.models import DiningTable, Hotel, MenuCategory, MenuItem, Order, Outlet, TableSession
from app.ordering.router import LineModel, _lines, ensure_order_quota
from app.ordering.service import OPEN_STATES, Actor, create_order, emit_order, order_json, quote, totals_json
from app.realtime.publish import publish_all

router = APIRouter(prefix="/d", tags=["diner"])
MAX_OPEN_ORDERS_PER_SESSION = 5


@dataclass
class TableRef:
    hotel_id: UUID
    outlet_id: UUID
    table_id: UUID


async def resolve_qr(qr_token: str) -> TableRef:
    # Pre-auth lookup: the opaque token maps to a tenant before any tenant context exists.
    async with platform_session() as db:
        t = (await db.execute(select(DiningTable).where(DiningTable.qr_token == qr_token))).scalar_one_or_none()
        if not t or t.status != "ACTIVE":
            raise not_found("Table")
        return TableRef(t.hotel_id, t.outlet_id, t.id)


@asynccontextmanager
async def diner_tx(hotel_id: UUID) -> AsyncIterator[tuple]:
    """Tenant transaction for diner requests; events are published only after commit."""
    events: list[tuple[str, dict]] = []
    async with tenant_session(hotel_id) as db:
        yield db, (lambda ch, ev: events.append((ch, ev)))
    publish_all(events)


async def _qr_enabled(db, hotel_id: UUID) -> dict:
    ent = await load_entitlements(db, hotel_id)
    if not ent["features"].get("QR_ORDERING"):
        raise forbidden("qr_disabled", "Ordering from the table is not available here. Please call a server.")
    return ent


@router.get("/{qr_token}")
async def menu(qr_token: str) -> dict:
    ref = await resolve_qr(qr_token)
    async with tenant_session(ref.hotel_id) as db:
        ent = await load_entitlements(db, ref.hotel_id)
        hotel = await db.get(Hotel, ref.hotel_id)
        outlet = await db.get(Outlet, ref.outlet_id)
        table = await db.get(DiningTable, ref.table_id)
        cats = (await db.execute(select(MenuCategory).order_by(MenuCategory.sort, MenuCategory.name))).scalars().all()
        items = (await db.execute(select(MenuItem).order_by(MenuItem.sort, MenuItem.name))).scalars().all()
        opts = await options_for(db)
        return {
            "hotel": {"name": hotel.name},
            # serve_mode tells the diner page whether to say "on its way" or "collect at the counter".
            "outlet": {"name": outlet.name, "serve_mode": outlet.serve_mode},
            "table": {"label": table.label, "area": table.area},
            "ordering_enabled": bool(ent["features"].get("QR_ORDERING")),
            "categories": [
                {
                    "id": str(c.id),
                    "name": c.name,
                    "items": [
                        for_diner(
                            with_options(
                                {
                                    "id": str(i.id),
                                    "name": i.name,
                                    "description": i.description,
                                    "is_veg": i.is_veg,
                                    "price_minor": i.price_minor,
                                    "is_available": i.is_available,
                                    "est_prep_minutes": i.est_prep_minutes,
                                },
                                opts,
                                i.id,
                            )
                        )
                        for i in items
                        if i.category_id == c.id
                    ],
                }
                for c in cats
            ],
        }


@router.post("/{qr_token}/session", status_code=201)
async def open_session(qr_token: str) -> dict:
    ref = await resolve_qr(qr_token)
    raw = new_opaque_token()
    async with tenant_session(ref.hotel_id) as db:
        await _qr_enabled(db, ref.hotel_id)
        s = TableSession(
            hotel_id=ref.hotel_id,
            table_id=ref.table_id,
            token_hash=sha256(raw),
            expires_at=now() + timedelta(hours=get_settings().diner_session_hours),
        )
        db.add(s)
        await db.flush()
        return {"session_token": raw, "expires_at": s.expires_at.isoformat()}


class DinerQuoteIn(BaseModel):
    items: list[LineModel] = Field(min_length=1, max_length=50)


@router.post("/{qr_token}/quote")
async def diner_quote(qr_token: str, body: DinerQuoteIn) -> dict:
    ref = await resolve_qr(qr_token)
    async with tenant_session(ref.hotel_id) as db:
        return totals_json(await quote(db, _lines(body.items)))


async def _session(token: str | None) -> TableSession:
    if not token:
        raise ApiError(401, "no_session", "Scan the table QR again")
    async with platform_session() as db:
        s = (
            await db.execute(select(TableSession).where(TableSession.token_hash == sha256(token)))
        ).scalar_one_or_none()
        if not s or s.closed_at or s.expires_at < now():
            raise ApiError(401, "session_expired", "Your table session ended. Scan the QR again.")
        return s


class DinerOrderIn(DinerQuoteIn):
    note: str | None = Field(default=None, max_length=300)
    client_ref: str | None = Field(default=None, max_length=64)


def _qr_client_ref(session_id: UUID, client_ref: str) -> str:
    """Idempotency key for a diner order, scoped to the table session (two phones may pick the same ref).
    A fixed-length fingerprint, because session id + the app's ref is longer than the 64-char column."""
    return "qr:" + sha256(f"{session_id}:{client_ref}")[:48]


@router.post("/orders", status_code=201)
async def diner_order(body: DinerOrderIn, x_diner_token: str | None = Header(default=None)) -> dict:
    s = await _session(x_diner_token)
    async with diner_tx(s.hotel_id) as (db, emit):
        ent = await _qr_enabled(db, s.hotel_id)
        await ensure_order_quota(db, ent)
        open_n = (
            await db.execute(
                select(func.count())
                .select_from(Order)
                .where(Order.table_session_id == s.id, Order.status.in_(OPEN_STATES))
            )
        ).scalar()
        if open_n >= MAX_OPEN_ORDERS_PER_SESSION:
            raise forbidden("too_many_open_orders", "Please wait for your earlier orders or call a server")
        table = await db.get(DiningTable, s.table_id)
        outlet = await db.get(Outlet, table.outlet_id)
        o, created = await create_order(
            db,
            hotel_id=s.hotel_id,
            outlet=outlet,
            table=table,
            session_id=s.id,
            channel="QR",
            actor=Actor("DINER"),
            lines=_lines(body.items),
            note=body.note,
            client_ref=_qr_client_ref(s.id, body.client_ref) if body.client_ref else None,
        )
        if created:
            await emit_order(db, emit, o, "order.created")
            await kitchen.route_pending(db, outlet, emit)
        return await order_json(db, o)


@router.get("/orders/mine")
async def my_orders(x_diner_token: str | None = Header(default=None)) -> list[dict]:
    s = await _session(x_diner_token)
    async with tenant_session(s.hotel_id) as db:
        rows = (
            await db.execute(select(Order).where(Order.table_session_id == s.id).order_by(Order.created_at.desc()))
        ).scalars()
        return [await order_json(db, o) for o in rows]
