-- Explicit administrator prerequisite, never executed by application startup.
\set ON_ERROR_STOP on
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public VERSION '0.8.7';
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace
                 WHERE e.extname='vector' AND e.extversion='0.8.7' AND n.nspname='public') THEN
    RAISE EXCEPTION 'Expected public.vector 0.8.7; do not silently upgrade/adopt another extension';
  END IF;
END $$;
