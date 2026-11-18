"""Alembic runs as the OWNER role, synchronously, on psycopg. Never as hotel_app."""

from __future__ import annotations

import os

from sqlalchemy import create_engine, pool

from alembic import context
from app.config import get_settings


def _url() -> str:
    raw = os.environ.get("OWNER_DATABASE_URL")
    if not raw:
        owner = get_settings().owner_database_url
        if owner is None:
            raise RuntimeError("OWNER_DATABASE_URL is not set; migrations run as the owner role.")
        raw = str(owner)
    # Normalise any driver suffix to psycopg (sync).
    for prefix in ("postgresql+asyncpg://", "postgresql+psycopg://", "postgresql://"):
        if raw.startswith(prefix):
            return "postgresql+psycopg://" + raw[len(prefix) :]
    return raw


def run_migrations_online() -> None:
    engine = create_engine(_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection, target_metadata=None, transaction_per_migration=True
        )
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
