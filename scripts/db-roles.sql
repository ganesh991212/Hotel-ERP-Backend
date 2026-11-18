-- Three roles (docs/08-security-tenancy.md):
--   hotel_owner    owns the schema, runs migrations
--   hotel_app      tenant traffic; NOT superuser, NO BYPASSRLS, so RLS always applies
--   hotel_platform super admin + pre-auth lookups; BYPASSRLS
-- Local throwaway passwords; production passwords come from the secret manager.
CREATE ROLE hotel_owner LOGIN PASSWORD 'hotel_owner';
CREATE ROLE hotel_app LOGIN PASSWORD 'hotel_app' NOBYPASSRLS;
CREATE ROLE hotel_platform LOGIN PASSWORD 'hotel_platform' BYPASSRLS;
SELECT 'CREATE DATABASE hotel_erp OWNER hotel_owner'
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'hotel_erp')\gexec
ALTER DATABASE hotel_erp OWNER TO hotel_owner;
