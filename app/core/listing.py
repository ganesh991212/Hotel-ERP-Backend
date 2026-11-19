"""Shared filters for long lists (orders, bills): hotel-local dates, a search box, pages (docs/06 "Lists by date")."""

from datetime import date, timedelta

from fastapi import Query
from sqlalchemy import Date, cast, func
from sqlalchemy.sql import ColumnElement

from app.core.errors import bad_request

MAX_RANGE_DAYS = 31


class ListParams:
    """`?from=YYYY-MM-DD&to=YYYY-MM-DD&q=...&limit=50&offset=0`. Dates are the hotel's local calendar days."""

    def __init__(
        self,
        date_from: date | None = Query(None, alias="from"),
        date_to: date | None = Query(None, alias="to"),
        q: str | None = Query(None, max_length=40),
        limit: int = Query(200, ge=1, le=200),
        offset: int = Query(0, ge=0, le=100_000),
    ):
        if date_from and date_to and date_to < date_from:
            raise bad_request("bad_range", "'to' is before 'from'")
        if date_from and date_to and (date_to - date_from) > timedelta(days=MAX_RANGE_DAYS - 1):
            raise bad_request("range_too_long", f"Pick at most {MAX_RANGE_DAYS} days")
        self.date_from, self.date_to = date_from, date_to or date_from
        self.q = (q or "").strip() or None
        self.limit, self.offset = limit, offset

    def dates(self, column, tz: str) -> list[ColumnElement]:
        if not self.date_from:
            return []
        local = cast(func.timezone(tz, column), Date)
        return [local >= self.date_from, local <= self.date_to]
