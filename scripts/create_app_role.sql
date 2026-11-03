-- Creates the non-superuser application role `hotel_app`.
-- Adapted from vethra_api scripts/create_app_role.sql.
--
-- RLS is bypassed by `rolsuper` and `rolbypassrls`; FORCE ROW LEVEL SECURITY only covers
-- the table owner. So: run MIGRATIONS as the owner, run the APPLICATION as hotel_app.
--
-- Run BEFORE `alembic upgrade head`: ALTER DEFAULT PRIVILEGES is what grants access to
-- tables the migrations have not created yet. Re-run AFTER it to apply the append-only
-- revokes. `scripts/dev_db.sh` does both.
--
-- The password comes from the session setting `hotel.app_password` (bound parameter in
-- scripts/dev_db.sh), never from this file.

DO $$
DECLARE
  app_password text := nullif(current_setting('hotel.app_password', true), '');
  is_super boolean;
  bypass_rls boolean;
BEGIN
  IF app_password IS NULL THEN
    RAISE EXCEPTION 'set hotel.app_password before running create_app_role.sql';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'hotel_app') THEN
    CREATE ROLE hotel_app LOGIN;  -- defaults: NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE
  END IF;
  EXECUTE format('ALTER ROLE hotel_app PASSWORD %L', app_password);

  -- Assert rather than set: if the role ever gains either attribute, fail loudly.
  SELECT rolsuper, rolbypassrls INTO is_super, bypass_rls FROM pg_roles WHERE rolname = 'hotel_app';
  IF is_super OR bypass_rls THEN
    RAISE EXCEPTION 'hotel_app has rolsuper=% rolbypassrls=%: RLS would not apply', is_super, bypass_rls;
  END IF;
END $$;

GRANT USAGE ON SCHEMA public TO hotel_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO hotel_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO hotel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO hotel_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO hotel_app;
REVOKE CREATE ON SCHEMA public FROM hotel_app;

-- Append-only audit (second layer; the migration also revokes).
DO $$ BEGIN
  IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'audit_log') THEN
    REVOKE UPDATE, DELETE ON audit_log, platform_audit_log FROM hotel_app;
  END IF;
END $$;
