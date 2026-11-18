"""The database-level safety net, checked against the live catalogue.

These fail the build if a future migration adds a hotel-scoped table without RLS, if the
app connects as a role that bypasses RLS, or if pooled connections leak hotel context.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.core.permissions import SYSTEM_ROLES
from app.db.session import current_hotel_id, get_engine, hotel_session, system_session

pytestmark = pytest.mark.isolation

# Tables that carry hotel_id but are deliberately not RLS-protected. Adding to this list
# is a security decision and needs the reasoning in the migration that creates the table.
UNSCOPED_WITH_HOTEL_ID = {
    "subscription",
    "hotel_entitlement_override",
    "auth_session",
    "invite_token",
    "platform_audit_log",
}


async def test_app_role_cannot_bypass_rls() -> None:
    async with system_session() as db:
        row = (
            await db.execute(
                text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
            )
        ).one()
        owner = (
            await db.execute(
                text(
                    "SELECT count(*) FROM pg_tables WHERE schemaname = 'public' "
                    "AND tableowner = current_user"
                )
            )
        ).scalar_one()
    assert row == (False, False), "app role bypasses RLS: isolation tests would prove nothing"
    assert owner == 0, "app role owns tables: FORCE would be its only protection"


async def test_every_hotel_table_is_forced_rls() -> None:
    async with system_session() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                    "FROM pg_class c JOIN pg_attribute a ON a.attrelid = c.oid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relkind = 'r' AND a.attname = 'hotel_id' "
                    "AND NOT a.attisdropped"
                )
            )
        ).all()
    tables = {name: (enabled, forced) for name, enabled, forced in rows}
    assert tables, "no hotel_id tables found"
    for name, (enabled, forced) in tables.items():
        if name in UNSCOPED_WITH_HOTEL_ID:
            continue
        assert enabled and forced, f"{name} has hotel_id but RLS is not enabled and forced"


async def test_policies_check_writes_and_use_the_null_safe_context() -> None:
    async with system_session() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT tablename, qual, with_check FROM pg_policies "
                    "WHERE schemaname = 'public' AND policyname = 'hotel_isolation'"
                )
            )
        ).all()
    assert {r[0] for r in rows} == {
        "hotel_tax_profile",
        "outlet",
        "role",
        "membership",
        "audit_log",
    }
    for table, qual, with_check in rows:
        assert "app_hotel_id()" in qual, table
        assert with_check is not None and "app_hotel_id()" in with_check, (
            f"{table}: without WITH CHECK a hotel could write rows into another hotel"
        )


async def test_system_roles_match_code() -> None:
    async with system_session() as db:
        rows = (
            await db.execute(text("SELECT id, code, permissions FROM role WHERE hotel_id IS NULL"))
        ).all()
    in_db = {code: (rid, frozenset(perms)) for rid, code, perms in rows}
    in_code = {r.code: (r.id, r.permissions) for r in SYSTEM_ROLES}
    assert in_db == in_code


async def test_empty_context_sees_nothing_and_does_not_crash() -> None:
    # On a pooled connection an expired setting reads as '' and ''::uuid would raise.
    async with system_session() as db:
        await db.execute(text("SELECT set_config('app.hotel_id', '', true)"))
        assert (await db.execute(text("SELECT count(*) FROM outlet"))).scalar_one() == 0
        assert (await db.execute(text("SELECT count(*) FROM membership"))).scalar_one() == 0


async def test_pooled_connection_does_not_leak_hotel_context() -> None:
    hotel = uuid.uuid4()
    async with get_engine().connect() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.hotel_id', :h, true)"), {"h": str(hotel)}
            )
            assert await current_hotel_id(session) == hotel
        # Same session and connection, new transaction: the setting must be gone.
        async with session.begin():
            assert await current_hotel_id(session) is None


async def test_cannot_write_into_another_hotel() -> None:
    other_hotel = uuid.uuid4()
    with pytest.raises(ProgrammingError, match="row-level security"):
        async with hotel_session(uuid.uuid4()) as db:
            await db.execute(
                text("INSERT INTO outlet (hotel_id, name, code) VALUES (:h, 'X', 'X')"),
                {"h": other_hotel},
            )


async def test_audit_log_is_append_only() -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        async with system_session() as db:
            await db.execute(text("UPDATE audit_log SET note = 'tampered'"))
    with pytest.raises(DBAPIError, match="permission denied"):
        async with system_session() as db:
            await db.execute(text("DELETE FROM platform_audit_log"))
