-- PROPOSED OPTIONAL ADMIN PROVISIONING, not a migration. Never run automatically.
-- After migrations 0001–0006, as a role administrator/schema-grant administrator.
-- Existing role names fail atomically; no adoption/password reset. No credential here.
\set ON_ERROR_STOP on
BEGIN;
CREATE ROLE jobwatch_chat_readonly LOGIN PASSWORD NULL
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 3;
GRANT CONNECT ON DATABASE :"DBNAME" TO jobwatch_chat_readonly;
GRANT USAGE ON SCHEMA jobwatch TO jobwatch_chat_readonly;
GRANT SELECT ON jobwatch.employers,jobwatch.sources,jobwatch.collection_runs,
  jobwatch.postings,jobwatch.posting_versions,jobwatch.posting_events,
  jobwatch.verifications,jobwatch.leads,jobwatch.triage,jobwatch.applications,
  jobwatch.owner_audit,jobwatch.schema_migrations,jobwatch.embedding_chunks,
  jobwatch.research_postings,jobwatch.source_health,jobwatch.opportunity_workflow,
  jobwatch.operational_postings,jobwatch.posting_deadline_claims,jobwatch.posting_deadline_state TO jobwatch_chat_readonly;
GRANT EXECUTE ON FUNCTION jobwatch.url_identity(text,boolean) TO jobwatch_chat_readonly;
ALTER ROLE jobwatch_chat_readonly SET search_path=jobwatch,pg_catalog;
ALTER ROLE jobwatch_chat_readonly SET default_transaction_read_only=on;
ALTER ROLE jobwatch_chat_readonly SET statement_timeout='5s';
ALTER ROLE jobwatch_chat_readonly SET lock_timeout='1s';
ALTER ROLE jobwatch_chat_readonly SET idle_in_transaction_session_timeout='10s';
ALTER ROLE jobwatch_chat_readonly SET transaction_timeout='15s';
ALTER ROLE jobwatch_chat_readonly SET work_mem='4MB';
COMMIT;
-- No existing-role/PUBLIC grants, audit triggers, migrations or default privileges change.
-- USERSET defaults aren't irrevocable: keep credentials server-side. The tool core
-- always forces READ ONLY, permits one row-producing statement and rolls back/closes.
