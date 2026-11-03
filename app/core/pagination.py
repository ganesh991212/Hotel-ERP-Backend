"""Keyset (cursor) pagination on ``(created_at DESC, id DESC)``.

Offset pagination re-scans every skipped row and returns duplicates when rows are inserted
between pages. Keyset reads only the page, using the ``(hotel_id, created_at, id)`` index.
"""

from __future__ import annotations

import base64
import uuid
from datetime import datetime

import orjson

from app.core.errors import unprocessable

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    raw = orjson.dumps([created_at.isoformat(), str(row_id)])
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        stamp, row_id = orjson.loads(base64.urlsafe_b64decode(padded))
        return datetime.fromisoformat(stamp), uuid.UUID(row_id)
    except (ValueError, TypeError, orjson.JSONDecodeError) as exc:
        raise unprocessable("INVALID_CURSOR", "Cursor is invalid") from exc
