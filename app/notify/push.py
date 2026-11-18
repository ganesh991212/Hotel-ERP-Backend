"""Push notifications via Firebase Cloud Messaging HTTP v1.

Ported from GigBridge (gigbridge-api/app/services/push_service.py) and adapted:
- async httpx instead of blocking `requests` inside async handlers (the service-account token refresh,
  which is sync in google-auth, runs in a worker thread and is cached until shortly before expiry);
- sends are queued by the request and fired only after the DB transaction commits;
- a LOUD custom sound: Android channel `orders_loud` (created by the app with alarm-volume audio
  attributes) + `kitchen_alert` raw sound; iOS `kitchen_alert.wav` with time-sensitive interruption;
  web stays data-only (as in GigBridge) so our service worker renders exactly one notification.
Push is best-effort and never breaks the action that triggered it.
"""

import asyncio
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models import ChefProfile, DeviceToken, RolePermission, User

logger = logging.getLogger(__name__)
_SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
SOUNDS = {"new_order": "kitchen_alert", "ready": "ready_alert"}
# On Android 8+ the channel (created by the app with alarm-volume audio) decides the sound.
ANDROID_CHANNELS = {"kitchen_alert": "orders_loud", "ready_alert": "orders_ready"}

_token: tuple[str, float] | None = None
_tasks: set[asyncio.Task] = set()  # keep references so fire-and-forget tasks are not garbage collected


def configured() -> bool:
    s = get_settings()
    return bool(s.fcm_project_id and s.fcm_service_account_json)


def _mint_token() -> tuple[str, float]:
    from google.auth.transport.requests import Request
    from google.oauth2 import service_account

    raw = get_settings().fcm_service_account_json
    info = json.load(open(raw)) if os.path.exists(raw) else json.loads(raw)
    creds = service_account.Credentials.from_service_account_info(info, scopes=[_SCOPE])
    creds.refresh(Request())
    return creds.token, time.time() + 50 * 60


async def _access_token() -> str:
    global _token
    if _token is None or _token[1] < time.time():
        _token = await asyncio.to_thread(_mint_token)
    return _token[0]


def payload_for(platform: str, title: str, body: str, data: dict[str, str], sound: str) -> dict:
    if platform == "web":
        # Data-only on purpose (GigBridge lesson): a `notification` block would be auto-shown by the Firebase
        # SW *and* by our handler, giving two banners. Browsers do not allow custom notification sounds; the
        # open app plays the loud sound itself when the realtime event arrives.
        return {
            "data": {**data, "title": title, "body": body, "sound": sound},
            "webpush": {"headers": {"Urgency": "high", "TTL": "600"}},
        }
    return {
        "notification": {"title": title, "body": body},
        "data": data,
        "android": {
            "priority": "high",
            "ttl": "600s",
            "notification": {
                "channel_id": ANDROID_CHANNELS.get(sound, "orders_loud"),
                "sound": sound,
                "default_vibrate_timings": False,
                "vibrate_timings": ["0.6s", "0.3s", "0.6s", "0.3s", "0.6s"],
                "notification_priority": "PRIORITY_MAX",
                "visibility": "PUBLIC",
            },
        },
        "apns": {
            "headers": {"apns-priority": "10", "apns-push-type": "alert"},
            "payload": {"aps": {"sound": f"{sound}.wav", "interruption-level": "time-sensitive"}},
        },
    }


Transport = Callable[[str, dict], Awaitable[int]]


async def _http_send(token: str, message: dict) -> int:
    access = await _access_token()
    url = f"https://fcm.googleapis.com/v1/projects/{get_settings().fcm_project_id}/messages:send"
    async with httpx.AsyncClient(timeout=10) as c:
        for attempt in range(2):  # one retry on transient errors, as GigBridge did
            r = await c.post(
                url, json={"message": {"token": token, **message}}, headers={"Authorization": f"Bearer {access}"}
            )
            if r.status_code not in (429, 500, 502, 503, 504) or attempt:
                return r.status_code
            await asyncio.sleep(0.5)
    return 0


transport: Transport = _http_send  # tests replace this


async def send_many(targets: list[tuple[str, str]], title: str, body: str, data: dict, sound: str) -> None:
    if not targets or (transport is _http_send and not configured()):
        return
    payload_data = {k: str(v) for k, v in data.items()}
    stale: list[str] = []
    for token, platform in targets:
        try:
            status = await transport(token, payload_for(platform, title, body, payload_data, sound))
            if status == 404:
                stale.append(token)  # unregistered device: prune
            elif status >= 400:
                logger.info("FCM send returned %s", status)
        except Exception as e:  # noqa: BLE001
            logger.warning("push send failed: %s", str(e)[:120])
    if stale:
        from app.core.db import platform_session

        async with platform_session() as db:
            await db.execute(delete(DeviceToken).where(DeviceToken.token.in_(stale)))


def fire(intent: dict) -> None:
    """Called after commit. Never raises, never blocks the response."""
    task = asyncio.get_running_loop().create_task(
        send_many(intent["targets"], intent["title"], intent["body"], intent.get("data", {}), intent["sound"])
    )
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def targets_for(
    db: AsyncSession, *, permission: str | None = None, chefs_on_shift: bool = False, user_ids: list | None = None
) -> list[tuple[str, str]]:
    """Device tokens of the right people, read inside the tenant transaction (RLS-scoped)."""
    q = (
        select(DeviceToken.token, DeviceToken.platform)
        .join(User, User.id == DeviceToken.user_id)
        .where(User.status == "ACTIVE")
    )
    if permission:
        q = q.join(RolePermission, RolePermission.role_id == User.role_id).where(
            RolePermission.permission == permission
        )
    if chefs_on_shift:
        q = q.join(ChefProfile, ChefProfile.user_id == User.id).where(ChefProfile.on_shift)
    if user_ids is not None:
        q = q.where(User.id.in_(user_ids))
    return [(t, p) for t, p in (await db.execute(q.distinct())).all()]
