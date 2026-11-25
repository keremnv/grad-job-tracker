-- ISOLATED PROPOSAL (NOT DEPLOYED TO PRODUCTION).
-- Strictly controlled least-privilege role for local-only agent public evidence ingestion.
--
-- Security invariants:
--   1. `jobwatch_agent_evidence` is LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT
--      NOREPLICATION NOBYPASSRLS, with zero role memberships.
--   2. ZERO raw INSERT/UPDATE/DELETE/TRUNCATE privileges on ANY table!
--   3. Every submission passes through SECURITY DEFINER `submit_agent_observation` or
--      `submit_agent_vacancy` (owned by non-superuser `jobwatch_migrator`, pinned to
--      `search_path = pg_catalog, jobwatch, pg_temp`), which compute all hashes and
--      candidate keys inside SQL, enforce (db_login, idempotency_key) isolation,
--      transactional per-login budgets, anti-contamination checks, and unscreened markers.
--   4. ZERO write access to corroborations, owner decisions, or monitored sources.
--   5. Defense-in-depth: revokes CREATE on schema public from jobwatch_agent_evidence.

\set ON_ERROR_STOP on
BEGIN;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jobwatch_agent_evidence') THEN
    CREATE ROLE jobwatch_agent_evidence LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 5;
  END IF;
END $$;

REVOKE ALL ON SCHEMA public FROM jobwatch_agent_evidence;
REVOKE ALL ON SCHEMA jobwatch FROM jobwatch_agent_evidence;
GRANT USAGE ON SCHEMA jobwatch TO jobwatch_agent_evidence;

-- Narrow read-only visibility to resolve targets and inspect public observations.
GRANT SELECT ON
  jobwatch.research_postings,
  jobwatch.research_discovered_boards,
  jobwatch.research_discovery_candidates,
  jobwatch.research_discovery_evidence,
  jobwatch.discovery_candidate_snapshots,
  jobwatch.posting_deadline_claims,
  jobwatch.research_agent_evidence,
  jobwatch.research_agent_corroborations
TO jobwatch_agent_evidence;

-- Controlled SECURITY DEFINER ingestion boundary ONLY (NO raw table writes!).
GRANT EXECUTE ON FUNCTION jobwatch.submit_agent_observation(
  text,text,text,text,timestamptz,text,text,text,text,text,text,text,bigint,text,jsonb
) TO jobwatch_agent_evidence;

GRANT EXECUTE ON FUNCTION jobwatch.submit_agent_vacancy(
  text,text,text,timestamptz,text,text,text,text,text,text,text,text,text[],text,text,text,text,text,jsonb
) TO jobwatch_agent_evidence;

-- Read-only visibility for Research role when provisioned.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jobwatch_agent_readonly') THEN
    GRANT SELECT ON
      jobwatch.research_agent_evidence,
      jobwatch.research_agent_corroborations
    TO jobwatch_agent_readonly;
  END IF;
END $$;

COMMIT;
