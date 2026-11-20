import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.billing.day_close import router as day_close
from app.billing.router import router as bills
from app.branding.router import router as branding
from app.catalog.options import router as menu_options
from app.catalog.price_rules import router as price_rules
from app.catalog.router import router as catalog
from app.core.config import get_settings
from app.core.errors import ApiError, ServerErrorsAsJson, api_error_handler
from app.dashboard.router import router as dashboard
from app.expenses.router import platform as expense_platform
from app.expenses.router import router as expenses
from app.floor.bookings import router as bookings
from app.floor.router import router as floor
from app.iam.router import router as auth
from app.iam.router import staff
from app.kitchen import overdue
from app.kitchen.router import router as kitchen
from app.notify.router import router as devices
from app.ordering.diner import router as diner
from app.ordering.router import router as orders
from app.platform.router import billing
from app.platform.router import router as platform
from app.printing.router import router as printing
from app.realtime.router import router as realtime
from app.reports.router import router as reports


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Background jobs that live with the (single) API process: the no-pickup alert (docs/04)."""
    task = None if get_settings().app_env == "test" else asyncio.create_task(overdue.run_forever())
    yield
    if task:
        task.cancel()


app = FastAPI(title="Hotel ERP API", version="0.1.0", lifespan=lifespan)
app.add_exception_handler(ApiError, api_error_handler)
# Order matters: the middleware added last runs first. CORS must wrap the crash handler so a 500 still
# carries the CORS headers the browser needs to show the error.
app.add_middleware(ServerErrorsAsJson)
origins = [o.strip() for o in get_settings().cors_origins.split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])

for r in (
    auth,
    staff,
    platform,
    billing,
    floor,
    catalog,
    menu_options,
    price_rules,
    orders,
    kitchen,
    diner,
    expenses,
    expense_platform,
    dashboard,
    bills,
    printing,
    devices,
    branding,
    reports,
    day_close,
    bookings,
):
    app.include_router(r, prefix="/api/v1")
app.include_router(realtime)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


if get_settings().web_dir:
    # Mounted last so every /api and /ws route wins. Dev/preview only; production serves the web build from a CDN.
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=get_settings().web_dir, html=True), name="web")
