#!/usr/bin/env bash
# Provision a local or CI database from nothing: roles, database, migrations, grants.
#
#   scripts/dev_db.sh                 # creates/updates database hotel_erp
#   scripts/dev_db.sh hotel_erp_test --reset
#
# Needs a Postgres where we can act as superuser: either SUPER_DATABASE_URL (CI), or the
# local `postgres` OS account (peer auth). Passwords default to dev values; never use
# these outside a laptop or CI.
#
# ORDER IS LOAD-BEARING:
#   1. hotel_owner + database (as superuser)
#   2. create_app_role.sql AS THE OWNER: default privileges apply only to tables created
#      by the role that set them, and the owner is who creates the tables
#   3. alembic upgrade head AS THE OWNER
#   4. create_app_role.sql again: append-only revokes on tables that now exist
set -euo pipefail
cd "$(dirname "$0")/.."

DB="${1:-hotel_erp}"
RESET="${2:-}"
OWNER_PW="${HOTEL_OWNER_PASSWORD:-owner-dev}"
APP_PW="${HOTEL_APP_PASSWORD:-app-dev}"
HOST="${PGHOST_TCP:-127.0.0.1}"
PORT="${PGPORT_TCP:-5432}"
PY="${PYTHON:-.venv/bin/python}"

super_sql() {
  if [ -n "${SUPER_DATABASE_URL:-}" ]; then
    psql "$SUPER_DATABASE_URL" -v ON_ERROR_STOP=1 -qAt -c "$1"
  else
    su postgres -c "psql -v ON_ERROR_STOP=1 -qAt -c \"$1\""
  fi
}

echo "==> 1/4 owner role and database $DB"
if [ -z "$(super_sql "SELECT 1 FROM pg_roles WHERE rolname='hotel_owner'")" ]; then
  super_sql "CREATE ROLE hotel_owner LOGIN CREATEDB CREATEROLE"
fi
super_sql "ALTER ROLE hotel_owner PASSWORD '$OWNER_PW'"
if [ "$RESET" = "--reset" ]; then
  super_sql "DROP DATABASE IF EXISTS $DB WITH (FORCE)"
fi
if [ -z "$(super_sql "SELECT 1 FROM pg_database WHERE datname='$DB'")" ]; then
  super_sql "CREATE DATABASE $DB OWNER hotel_owner"
fi

export OWNER_DATABASE_URL="postgresql+psycopg://hotel_owner:$OWNER_PW@$HOST:$PORT/$DB"
export HOTEL_APP_PASSWORD="$APP_PW"

run_owner_sql() {
  SQL_FILE="$1" "$PY" - <<'PY'
import os
import psycopg

url = os.environ["OWNER_DATABASE_URL"].replace("+psycopg", "")
with psycopg.connect(url, autocommit=True) as conn:
    conn.execute("SELECT set_config('hotel.app_password', %s, false)", [os.environ["HOTEL_APP_PASSWORD"]])
    conn.execute(open(os.environ["SQL_FILE"]).read())
PY
}

echo "==> 2/4 application role (as owner)"
run_owner_sql scripts/create_app_role.sql
echo "==> 3/4 migrations (as owner)"
"$PY" -m alembic upgrade head
echo "==> 4/4 append-only grants"
run_owner_sql scripts/create_app_role.sql

echo
echo "DATABASE_URL=postgresql+asyncpg://hotel_app:$APP_PW@$HOST:$PORT/$DB"
echo "OWNER_DATABASE_URL=postgresql+asyncpg://hotel_owner:$OWNER_PW@$HOST:$PORT/$DB"
