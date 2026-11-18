"""Device token registration (same contract as GigBridge's /notifications/register-token)."""

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.core.db import platform_session
from app.core.deps import Ctx, HotelCtx
from app.models import DeviceToken
from app.notify import push

router = APIRouter(tags=["notifications"])


class TokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=400)
    platform: str = Field(default="web", pattern="^(android|ios|web)$")


@router.post("/me/devices")
async def register(body: TokenIn, ctx: Ctx = HotelCtx()) -> dict:
    """Upsert. A token moving to a new user (shared device) is re-bound so the previous user's
    notifications stop arriving here (GigBridge shared-browser fix)."""
    async with platform_session() as pdb:  # token is globally unique; may belong to another hotel's row
        await pdb.execute(delete(DeviceToken).where(DeviceToken.token == body.token))
    ctx.db.add(DeviceToken(hotel_id=ctx.hotel_id, user_id=ctx.user_id, token=body.token, platform=body.platform))
    return {"registered": True, "push_configured": push.configured()}


@router.delete("/me/devices", status_code=204)
async def unregister(body: TokenIn, ctx: Ctx = HotelCtx()) -> None:
    row = (
        await ctx.db.execute(
            select(DeviceToken).where(DeviceToken.token == body.token, DeviceToken.user_id == ctx.user_id)
        )
    ).scalar_one_or_none()
    if row:
        await ctx.db.delete(row)
