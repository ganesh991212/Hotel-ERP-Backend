"""Device token registration (same contract as GigBridge's /notifications/register-token)."""

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.db import platform_session
from app.core.deps import Ctx, HotelCtx
from app.models import DeviceToken
from app.notify import push

router = APIRouter(tags=["notifications"])


@router.get("/push/config")
async def push_config() -> dict:
    """Public Firebase client config from .env, so apps need no per-build Firebase files. `enabled` is true only
    when the server can actually send (FCM project + service account) and the client side is configured."""
    s = get_settings()
    base = {
        "projectId": s.fcm_project_id,
        "messagingSenderId": s.firebase_messaging_sender_id,
        "storageBucket": s.firebase_storage_bucket,
    }

    def platform(api_key: str, app_id: str, **extra) -> dict | None:
        key = api_key or s.firebase_api_key
        return {**base, "apiKey": key, "appId": app_id, **extra} if key and app_id else None

    out = {
        "web": platform(
            s.firebase_api_key, s.firebase_web_app_id, authDomain=s.firebase_auth_domain, vapidKey=s.firebase_vapid_key
        )
        if s.firebase_vapid_key
        else None,
        "android": platform(s.firebase_android_api_key, s.firebase_android_app_id),
        "ios": platform(s.firebase_ios_api_key, s.firebase_ios_app_id, iosBundleId=s.firebase_ios_bundle_id),
    }
    return {"enabled": push.configured() and any(out.values()), **out}


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
