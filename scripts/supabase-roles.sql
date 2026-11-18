-- One-time setup for a Supabase project (run in Supabase > SQL Editor, which runs as `postgres`).
-- Run it once per environment: staging and production are separate Supabase projects with different passwords.
--
-- Before running: replace the three REPLACE_ placeholders with passwords from a password manager
-- (32+ random letters and digits; avoid @ : / ? # % so they are safe inside a connection URL).
-- Do NOT save the edited copy into this repository.
--
-- Roles (docs/08-security-tenancy.md):
--   hotel_owner     owns the schema, runs migrations. Never used by the running API for requests.
--   hotel_app       tenant traffic. NOBYPASSRLS, so Row-Level Security always applies.
--   hotel_platform  super admin and pre-auth lookups. BYPASSRLS.

CREATE ROLE hotel_owner    LOGIN PASSWORD 'REPLACE_OWNER_PASSWORD';
CREATE ROLE hotel_app      LOGIN PASSWORD 'REPLACE_APP_PASSWORD' NOBYPASSRLS;
CREATE ROLE hotel_platform LOGIN PASSWORD 'REPLACE_PLATFORM_PASSWORD' BYPASSRLS;

-- Tables live in `public`. The owner creates them; the other two only use them (grants come from migrations).
GRANT USAGE, CREATE ON SCHEMA public TO hotel_owner;
GRANT USAGE ON SCHEMA public TO hotel_app, hotel_platform;

-- Guard rails: a runaway query or an abandoned transaction cannot hold locks for long.
ALTER ROLE hotel_app      SET statement_timeout = '15s';
ALTER ROLE hotel_platform SET statement_timeout = '30s';
ALTER ROLE hotel_app      SET idle_in_transaction_session_timeout = '30s';
ALTER ROLE hotel_platform SET idle_in_transaction_session_timeout = '30s';

-- Check: expect three rows; only hotel_platform has rolbypassrls = true.
SELECT rolname, rolcanlogin, rolbypassrls FROM pg_roles
WHERE rolname IN ('hotel_owner', 'hotel_app', 'hotel_platform') ORDER BY rolname;
