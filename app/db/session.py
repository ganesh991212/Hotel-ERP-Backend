"""Database engine, sessions and hotel (tenant) context.

**The most safety-critical file in the codebase.** Adapted from vethra_api, where each
rule below was learned from a real defect.

Why ``set_config(..., true)`` and not ``SET``
    RLS policies read ``current_setting('app.hotel_id')``. Connections are pooled. A plain
    ``SET`` persists on the connection after the request, so the next borrower (maybe a
    different hotel) would inherit it and RLS would authorise the wrong hotel's rows.
    ``set_config(key, value, is_local => true)`` is scoped to the transaction and discarded
    on commit or rollback, so context can never travel back into the pool.

Why the role matters
    Superusers and BYPASSRLS roles skip RLS entirely; the table owner skips it unless the
    table is FORCE ROW LEVEL SECURITY. The app connects as ``hotel_app``, which is none of
    those. Connecting as ``postgres`` makes every isolation test pass for the wrong reason.

Performance
    All context keys are set in **one** statement (one round trip), and the request's
    access check is the only other query before the handler runs. Handlers get a Core
    ``AsyncConnection``, not an ORM ``AsyncSession``: every query here is SQL text, and
    skipping the ORM unit-of-work layer measured about 12% more requests per second.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from app.config import get_settings

HOTEL_SETTING = "app.hotel_id"
USER_SETTING = "app.user_id"

_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            str(settings.database_url),
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_timeout=settings.db_pool_timeout_seconds,
            pool_pre_ping=settings.db_pool_pre_ping,
            pool_recycle=settings.db_pool_recycle_seconds,
            connect_args={
                "statement_cache_size": settings.db_statement_cache_size,
                "server_settings": {"application_name": "hotel-erp-api"},
            },
        )
    return _engine


async def set_context(
    session: AsyncConnection, *, hotel_id: UUID | None = None, user_id: UUID | None = None
) -> None:
    """Set transaction-local context keys in a single round trip. Must be inside a transaction."""
    await session.execute(
        text("SELECT set_config('app.hotel_id', :h, true), set_config('app.user_id', :u, true)"),
        {
            "h": str(hotel_id) if hotel_id is not None else "",
            "u": str(user_id) if user_id is not None else "",
        },
    )


@asynccontextmanager
async def hotel_session(
    hotel_id: UUID, user_id: UUID | None = None
) -> AsyncIterator[AsyncConnection]:
    """A session bound to exactly one hotel for the life of one transaction."""
    async with get_engine().connect() as conn, conn.begin():
        await set_context(conn, hotel_id=hotel_id, user_id=user_id)
        yield conn


@asynccontextmanager
async def system_session() -> AsyncIterator[AsyncConnection]:
    """A transaction with **no hotel context**.

    Legitimate uses are narrow: login (before the hotel is known), token refresh, and
    platform (super admin) work on global tables. RLS-protected tables return zero rows
    here: the failure mode is "sees nothing", never "sees everything".

    Platform code that must write a hotel's own rows sets that hotel's context explicitly
    with :func:`set_context` inside this same transaction, so onboarding stays atomic.
    """
    async with get_engine().connect() as conn, conn.begin():
        yield conn


async def current_hotel_id(session: AsyncConnection) -> UUID | None:
    raw = (await session.execute(text("SELECT current_setting('app.hotel_id', true)"))).scalar()
    return UUID(raw) if raw else None


async def dispose_engine() -> None:
    global _engine
    if _engine is not None:
        await _engine.dispose()
    _engine = None
