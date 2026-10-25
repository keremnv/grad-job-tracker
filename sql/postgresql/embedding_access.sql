-- OPTIONAL ADMIN provisioning AFTER migrations, never automatic. No password here.
-- Existing cluster role deliberately fails; no adoption, reset or privilege escalation.
\set ON_ERROR_STOP on
BEGIN;
CREATE ROLE jobwatch_embedding_writer LOGIN PASSWORD NULL NOSUPERUSER NOCREATEDB
  NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2;
GRANT CONNECT ON DATABASE :"DBNAME" TO jobwatch_embedding_writer;
-- For restore, create the role before restoring its archived object ACLs.
DO $$ BEGIN
  IF to_regclass('jobwatch.embedding_chunks') IS NOT NULL THEN
    GRANT USAGE ON SCHEMA jobwatch TO jobwatch_embedding_writer;
    GRANT SELECT ON jobwatch.postings,jobwatch.posting_versions TO jobwatch_embedding_writer;
    GRANT SELECT,INSERT,DELETE ON jobwatch.embedding_chunks TO jobwatch_embedding_writer;
  END IF;
END $$;
ALTER ROLE jobwatch_embedding_writer SET search_path=jobwatch,pg_catalog;
ALTER ROLE jobwatch_embedding_writer SET statement_timeout='30s';
ALTER ROLE jobwatch_embedding_writer SET idle_in_transaction_session_timeout='30s';
COMMIT;
