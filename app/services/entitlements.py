"""Resolving what a hotel's plan allows, cached by version.

Precedence for each feature: hotel override, then the current plan, then the catalogue
default. For limits, ``None`` means unlimited.

Caching: the per-request access check already reads ``hotel.entitlements_version``, and
database triggers bump it on every change that affects entitlements. The cache is keyed by
``(hotel_id, version)``, so a hit costs no query and can never be stale, on any number of
API processes, with no cross-process invalidation needed.
"""

from __future__ import annotations

import uuid
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.core.errors import AppError

Source = Literal["override", "plan", "default"]


@dataclass(frozen=True)
class Entitlements:
    hotel_id: uuid.UUID
    version: int
    plan_code: str | None
    subscription_status: str | None
    flags: Mapping[str, bool]
    limits: Mapping[str, int | None]
    sources: Mapping[str, Source]

    def allows(self, key: str) -> bool:
        if key not in self.flags:
            raise KeyError(f"unknown feature flag {key!r}")
        return self.flags[key]

    def limit(self, key: str) -> int | None:
        if key not in self.limits:
            raise KeyError(f"unknown limit {key!r}")
        return self.limits[key]


_RESOLVE_SQL = text(
    """
    SELECT fc.key, fc.kind,
           CASE WHEN o.feature_key IS NOT NULL THEN o.flag_value
                WHEN pf.feature_key IS NOT NULL THEN pf.flag_value
                ELSE fc.default_flag END AS flag_value,
           CASE WHEN o.feature_key IS NOT NULL THEN o.limit_value
                WHEN pf.feature_key IS NOT NULL THEN pf.limit_value
                ELSE fc.default_limit END AS limit_value,
           CASE WHEN o.feature_key IS NOT NULL THEN 'override'
                WHEN pf.feature_key IS NOT NULL THEN 'plan'
                ELSE 'default' END AS source,
           p.code AS plan_code, s.status AS subscription_status
      FROM feature_catalog fc
      LEFT JOIN subscription s ON s.hotel_id = :h AND s.is_current AND s.status <> 'cancelled'
      LEFT JOIN plan p ON p.id = s.plan_id
      LEFT JOIN plan_feature pf ON pf.plan_id = s.plan_id AND pf.feature_key = fc.key
      LEFT JOIN hotel_entitlement_override o ON o.hotel_id = :h AND o.feature_key = fc.key
     ORDER BY fc.sort_order
    """
)

_CACHE_MAX = 4096
_cache: OrderedDict[tuple[uuid.UUID, int], Entitlements] = OrderedDict()


async def load(session: AsyncConnection, hotel_id: uuid.UUID, version: int) -> Entitlements:
    """Resolve from the database, bypassing the cache."""
    rows = (await session.execute(_RESOLVE_SQL, {"h": hotel_id})).mappings().all()
    flags: dict[str, bool] = {}
    limits: dict[str, int | None] = {}
    sources: dict[str, Source] = {}
    plan_code: str | None = None
    sub_status: str | None = None
    for row in rows:
        key = str(row["key"])
        if row["kind"] == "flag":
            flags[key] = bool(row["flag_value"])
        else:
            limits[key] = None if row["limit_value"] is None else int(row["limit_value"])
        sources[key] = row["source"]
        plan_code = row["plan_code"]
        sub_status = row["subscription_status"]
    return Entitlements(hotel_id, version, plan_code, sub_status, flags, limits, sources)


async def get(session: AsyncConnection, hotel_id: uuid.UUID, version: int) -> Entitlements:
    key = (hotel_id, version)
    cached = _cache.get(key)
    if cached is not None:
        _cache.move_to_end(key)
        return cached
    resolved = await load(session, hotel_id, version)
    _cache[key] = resolved
    if len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)
    return resolved


def clear_cache() -> None:
    _cache.clear()


def feature_denied(key: str) -> AppError:
    return AppError(
        403, "FEATURE_NOT_IN_PLAN", f"Your plan does not include {key}", {"feature": key}
    )


async def enforce_limit(
    session: AsyncConnection,
    ent: Entitlements,
    key: str,
    count_sql: str,
    params: Mapping[str, object],
) -> None:
    """Refuse when the hotel is at its limit. Safe under concurrency.

    Two concurrent creates must not both pass a "count < limit" check. A transaction-level
    advisory lock per (hotel, limit) serialises just those creates, and it does not touch
    the hotel row, so ordinary inserts (which take a key-share lock on it through their
    foreign key) are never blocked.
    """
    limit = ent.limit(key)
    if limit is None:
        return
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"limit:{key}:{ent.hotel_id}"},
    )
    current = int((await session.execute(text(count_sql), dict(params))).scalar_one())
    if current >= limit:
        raise AppError(
            403,
            "PLAN_LIMIT_REACHED",
            f"Your plan allows {limit} for {key}",
            {"feature": key, "limit": limit, "current": current},
        )
