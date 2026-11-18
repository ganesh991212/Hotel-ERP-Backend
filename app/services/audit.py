"""Audit writers. Adapted from vethra_api ``app/services/audit.py``.

Hotel actions go to ``audit_log`` (RLS, inside the hotel's transaction, so the entry
commits or rolls back with the change it describes). Platform actions on global tables go
to ``platform_audit_log``. Both are append-only for the application role.

Personal data stays out of payloads: store ids, codes, statuses and amounts.
"""

from __future__ import annotations

import re
import uuid
from typing import Literal

import orjson
from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_PII_KEYS = re.compile(
    r"^(full_name|first_name|last_name|name|email|phone|mobile|address|password|"
    r"password_hash|secret|token|refresh_token|invite_token|aadhaar|pan)$",
    re.IGNORECASE,
)


def sanitize(payload: dict[str, object] | None) -> dict[str, object] | None:
    if payload is None:
        return None
    clean: dict[str, object] = {}
    for key, value in payload.items():
        if _PII_KEYS.match(key):
            clean[key] = "[REDACTED]"
        elif isinstance(value, dict):
            clean[key] = sanitize(value)
        elif isinstance(value, list):
            clean[key] = [sanitize(v) if isinstance(v, dict) else v for v in value]
        else:
            clean[key] = value
    return clean


def _json(payload: dict[str, object] | None) -> str | None:
    cleaned = sanitize(payload)
    # default=str turns UUIDs, datetimes and times into strings.
    return orjson.dumps(cleaned, default=str).decode() if cleaned is not None else None


def client_ip(request: Request | None) -> str | None:
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else None


async def write_hotel_audit(
    session: AsyncConnection,
    *,
    hotel_id: uuid.UUID,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    actor_kind: Literal["staff", "platform", "system"] = "staff",
    before: dict[str, object] | None = None,
    after: dict[str, object] | None = None,
    ip: str | None = None,
    note: str | None = None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO audit_log (hotel_id, actor_user_id, actor_kind, action, entity_type, "
            "entity_id, before_json, after_json, ip, note) "
            "VALUES (:h, :a, :k, :act, :et, :eid, CAST(:b AS jsonb), CAST(:af AS jsonb), :ip, :n)"
        ),
        {
            "h": hotel_id,
            "a": actor_user_id,
            "k": actor_kind,
            "act": action,
            "et": entity_type,
            "eid": entity_id,
            "b": _json(before),
            "af": _json(after),
            "ip": ip,
            "n": note,
        },
    )


async def write_platform_audit(
    session: AsyncConnection,
    *,
    action: str,
    entity_type: str,
    entity_id: uuid.UUID | None = None,
    hotel_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    before: dict[str, object] | None = None,
    after: dict[str, object] | None = None,
    ip: str | None = None,
    note: str | None = None,
) -> None:
    await session.execute(
        text(
            "INSERT INTO platform_audit_log (actor_user_id, action, entity_type, entity_id, "
            "hotel_id, before_json, after_json, ip, note) "
            "VALUES (:a, :act, :et, :eid, :h, CAST(:b AS jsonb), CAST(:af AS jsonb), :ip, :n)"
        ),
        {
            "a": actor_user_id,
            "act": action,
            "et": entity_type,
            "eid": entity_id,
            "h": hotel_id,
            "b": _json(before),
            "af": _json(after),
            "ip": ip,
            "n": note,
        },
    )
