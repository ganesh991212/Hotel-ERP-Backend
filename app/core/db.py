from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings

_s = get_settings()
tenant_engine = create_async_engine(_s.database_url, pool_pre_ping=True)
platform_engine = create_async_engine(_s.platform_database_url, pool_pre_ping=True)
TenantSession = async_sessionmaker(tenant_engine, expire_on_commit=False)
PlatformSession = async_sessionmaker(platform_engine, expire_on_commit=False)


@asynccontextmanager
async def tenant_session(hotel_id: UUID) -> AsyncIterator[AsyncSession]:
    """One transaction with app.hotel_id set LOCAL from the verified token. RLS does the rest."""
    async with TenantSession() as session, session.begin():
        await session.execute(text("select set_config('app.hotel_id', :h, true)"), {"h": str(hotel_id)})
        yield session


@asynccontextmanager
async def platform_session() -> AsyncIterator[AsyncSession]:
    """BYPASSRLS role. Only for super admin routes and narrow pre-auth lookups (login, QR resolve)."""
    async with PlatformSession() as session, session.begin():
        yield session
