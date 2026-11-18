-- Run in Supabase > SQL Editor AFTER the first successful deploy (migrations have run).
-- Every query below must return ZERO rows. Any row is a finding to fix before real data goes in.

-- 1. Tenant tables without Row-Level Security.
SELECT c.relname AS table_without_rls
FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relowner = 'hotel_owner'::regrole
  AND c.relname IN (SELECT table_name FROM information_schema.columns
                    WHERE table_schema = 'public' AND column_name = 'hotel_id')
  AND NOT c.relrowsecurity;

-- 2. Supabase's public Data API roles must not be able to touch our tables.
SELECT grantee, table_name, privilege_type
FROM information_schema.role_table_grants
WHERE table_schema = 'public' AND grantee IN ('anon', 'authenticated')
  AND table_name IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tableowner = 'hotel_owner');

-- 3. The tenant role must not bypass RLS.
SELECT rolname FROM pg_roles WHERE rolname = 'hotel_app' AND (rolbypassrls OR rolsuper);
