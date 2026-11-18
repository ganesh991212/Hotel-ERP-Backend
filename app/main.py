from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.catalog.router import router as catalog
from app.core.config import get_settings
from app.core.errors import ApiError, api_error_handler
from app.dashboard.router import router as dashboard
from app.expenses.router import router as expenses
from app.floor.router import router as floor
from app.iam.router import router as auth
from app.iam.router import staff
from app.kitchen.router import router as kitchen
from app.ordering.diner import router as diner
from app.ordering.router import router as orders
from app.platform.router import billing
from app.platform.router import router as platform
from app.realtime.router import router as realtime

app = FastAPI(title="Hotel ERP API", version="0.1.0")
app.add_exception_handler(ApiError, api_error_handler)
origins = [o.strip() for o in get_settings().cors_origins.split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])

for r in (auth, staff, platform, billing, floor, catalog, orders, kitchen, diner, expenses, dashboard):
    app.include_router(r, prefix="/api/v1")
app.include_router(realtime)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
