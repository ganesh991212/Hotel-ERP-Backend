"""The hotel's audit log: who changed what, and when."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text

from app.api.deps import HotelContext, require_permission
from app.api.schemas import Page
from app.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, decode_cursor, encode_cursor

router = APIRouter(tags=["audit"])
ReadAudit = Annotated[HotelContext, Depends(require_permission("audit.read"))]


class AuditOut(BaseModel):
    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    actor_kind: str
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    before_json: dict[str, object] | None
    after_json: dict[str, object] | None
    note: str | None
    occurred_at: datetime


@router.get("/audit-log", response_model=Page[AuditOut])
async def list_audit(
    ctx: ReadAudit,
    entity_type: Annotated[str | None, Query(max_length=48)] = None,
    entity_id: uuid.UUID | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> Page[AuditOut]:
    sql = (
        "SELECT id, actor_user_id, actor_kind, action, entity_type, entity_id, before_json, "
        "after_json, note, occurred_at FROM audit_log WHERE hotel_id = :h"
    )
    params: dict[str, object] = {"h": ctx.hotel_id, "lim": limit + 1}
    if entity_type:
        sql += " AND entity_type = :et"
        params["et"] = entity_type
    if entity_id:
        sql += " AND entity_id = :eid"
        params["eid"] = entity_id
    if cursor:
        stamp, row_id = decode_cursor(cursor)
        sql += " AND (occurred_at, id) < (:cs, :ci)"
        params.update(cs=stamp, ci=row_id)
    rows = (
        (await ctx.db.execute(text(sql + " ORDER BY occurred_at DESC, id DESC LIMIT :lim"), params))
        .mappings()
        .all()
    )
    items = [AuditOut(**r) for r in rows[:limit]]
    nxt = encode_cursor(items[-1].occurred_at, items[-1].id) if len(rows) > limit else None
    return Page[AuditOut](items=items, next_cursor=nxt)
