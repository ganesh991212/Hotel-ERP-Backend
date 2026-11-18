"""Hotel ERP API entry point.

Run: ``uvicorn app.main:app --workers N`` (uvloop and httptools come with
``uvicorn[standard]``). Each worker holds its own connection pool; size
``DB_POOL_SIZE x workers`` below the database's connection limit, or put PgBouncer in
front (then set ``DB_STATEMENT_CACHE_SIZE=0``).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.api.routers import audit_log, auth, hotel, outlets, platform, roles, staff
from app.config import get_settings
from app.core.errors import AppError, install_error_handlers
from app.db.session import dispose_engine, get_engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    settings.require_production_secrets()

    app = FastAPI(
        title="Hotel ERP API",
        version="0.1.0",
        openapi_url=None if settings.is_production else "/openapi.json",
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    install_error_handlers(app)

    @app.exception_handler(IntegrityError)
    async def _integrity(_: Request, exc: IntegrityError) -> JSONResponse:
        # Fallback only: handlers map the constraint violations they expect to specific
        # codes. Reaching this means a write conflicted in a way nobody anticipated.
        return JSONResponse(
            {
                "code": "CONFLICT",
                "message": "The change conflicts with existing data",
                "details": None,
            },
            status_code=409,
        )

    @app.get("/health", tags=["ops"])
    async def health() -> dict[str, str]:
        """Liveness. Deliberately does not touch the database (a DB blip must not trigger
        restarts that make things worse)."""
        return {"status": "ok"}

    @app.get("/ready", tags=["ops"])
    async def ready() -> dict[str, str]:
        """Readiness: can this process reach the database right now?"""
        try:
            async with get_engine().connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:
            raise AppError(503, "NOT_READY", "Database unreachable") from exc
        return {"status": "ready"}

    # Responses over 1 KB are compressed: staff lists and audit pages on 4G.
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        # Must match what the Flutter client sends, or browsers fail the preflight
        # (a vethra_api production incident).
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "X-Client-Platform",
            "X-Client-Version",
        ],
    )

    for router in (
        auth.router,
        auth.me_router,
        platform.router,
        hotel.router,
        outlets.router,
        roles.router,
        staff.router,
        audit_log.router,
    ):
        app.include_router(router, prefix="/v1")
    return app


app = create_app()
