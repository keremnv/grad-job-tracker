-- Stage B local-only least-privilege role & grant provisioning (run once as admin after 0012).
-- Provisions two strictly separated, non-privileged roles:
--   1. `jobwatch_agent_evidence`: submits public evidence ONLY via SECURITY DEFINER
--      `submit_agent_observation` and `submit_agent_vacancy`. ZERO raw table writes.
--   2. `jobwatch_corroborator`: runs in an nftables-jailed OS worker; reads observations &
--      target metadata and appends ONLY to `agent_evidence_corroborations`. ZERO write access
--      to postings, sources, candidates, observations, verifications, or owner decisions.
-- Also grants read-only Research/Agent SELECT visibility on `research_agent_evidence` and
-- `research_agent_corroborations`.

\set ON_ERROR_STOP on
BEGIN;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jobwatch_agent_evidence') THEN
    CREATE ROLE jobwatch_agent_evidence LOGIN PASSWORD NULL NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 5;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jobwatch_corroborator') THEN
    CREATE ROLE jobwatch_corroborator LOGIN PASSWORD NULL NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2;
  END IF;
END $$;

ALTER ROLE jobwatch_agent_evidence SET search_path = jobwatch, pg_catalog;
ALTER ROLE jobwatch_agent_evidence SET statement_timeout = '5s';
ALTER ROLE jobwatch_agent_evidence SET lock_timeout = '1s';
ALTER ROLE jobwatch_agent_evidence SET idle_in_transaction_session_timeout = '10s';
ALTER ROLE jobwatch_agent_evidence SET transaction_timeout = '15s';
ALTER ROLE jobwatch_agent_evidence SET work_mem = '4MB';

ALTER ROLE jobwatch_corroborator SET search_path = jobwatch, pg_catalog;
ALTER ROLE jobwatch_corroborator SET statement_timeout = '10s';
ALTER ROLE jobwatch_corroborator SET lock_timeout = '2s';
ALTER ROLE jobwatch_corroborator SET idle_in_transaction_session_timeout = '15s';
ALTER ROLE jobwatch_corroborator SET transaction_timeout = '30s';
ALTER ROLE jobwatch_corroborator SET work_mem = '4MB';

REVOKE ALL ON SCHEMA public FROM jobwatch_agent_evidence, jobwatch_corroborator;
REVOKE ALL ON SCHEMA jobwatch FROM jobwatch_agent_evidence, jobwatch_corroborator;
GRANT USAGE ON SCHEMA jobwatch TO jobwatch_agent_evidence, jobwatch_corroborator;

-- Narrow read-only visibility for `jobwatch_agent_evidence`.
GRANT SELECT ON
  jobwatch.research_postings,
  jobwatch.research_discovered_boards,
  jobwatch.research_discovery_candidates,
  jobwatch.research_discovery_evidence,
  jobwatch.discovery_candidate_snapshots,
  jobwatch.posting_deadline_claims,
  jobwatch.posting_deadline_claims_distinct,
  jobwatch.research_agent_evidence,
  jobwatch.research_agent_corroborations
TO jobwatch_agent_evidence;

GRANT EXECUTE ON FUNCTION jobwatch.normalize_claim_wording(text) TO jobwatch_agent_evidence, jobwatch_corroborator;

-- Controlled SECURITY DEFINER ingestion boundary ONLY (ZERO raw table writes!).
GRANT EXECUTE ON FUNCTION jobwatch.submit_agent_observation(
  text,text,text,text,timestamptz,text,text,text,text,text,text,text,bigint,text,jsonb
) TO jobwatch_agent_evidence;

GRANT EXECUTE ON FUNCTION jobwatch.submit_agent_vacancy(
  text,text,text,timestamptz,text,text,text,text,text,text,text,text,text[],text,text,text,text,text,jsonb
) TO jobwatch_agent_evidence;

-- Narrow read + append-only corroboration write for `jobwatch_corroborator`.
GRANT SELECT ON
  jobwatch.agent_evidence_observations,
  jobwatch.agent_evidence_corroborations,
  jobwatch.research_agent_evidence,
  jobwatch.research_agent_corroborations,
  jobwatch.postings,
  jobwatch.posting_versions,
  jobwatch.sources,
  jobwatch.discovery_candidates,
  jobwatch.discovery_candidate_snapshots,
  jobwatch.discovered_boards
TO jobwatch_corroborator;

GRANT SELECT (id, slug, name, careers_url, watch_status, watch_checked_at) ON jobwatch.employers
  TO jobwatch_corroborator;
GRANT SELECT (lead_key, kind, company, title, url, url_exact, state, posting_key) ON jobwatch.leads
  TO jobwatch_corroborator;
GRANT INSERT ON jobwatch.agent_evidence_corroborations TO jobwatch_corroborator;

-- Read-only visibility for existing Research / Agent / Calendar roles when provisioned.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jobwatch_agent_readonly') THEN
    GRANT SELECT ON
      jobwatch.posting_deadline_claims_distinct,
      jobwatch.research_agent_evidence,
      jobwatch.research_agent_corroborations
    TO jobwatch_agent_readonly;
    GRANT EXECUTE ON FUNCTION jobwatch.normalize_claim_wording(text) TO jobwatch_agent_readonly;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jobwatch_agent_owner') THEN
    GRANT SELECT ON
      jobwatch.posting_deadline_claims_distinct,
      jobwatch.research_agent_evidence,
      jobwatch.research_agent_corroborations
    TO jobwatch_agent_owner;
    GRANT EXECUTE ON FUNCTION jobwatch.normalize_claim_wording(text) TO jobwatch_agent_owner;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jobwatch_calendar_reader') THEN
    GRANT SELECT ON
      jobwatch.posting_deadline_claims_distinct,
      jobwatch.research_agent_evidence,
      jobwatch.research_agent_corroborations
    TO jobwatch_calendar_reader;
    GRANT EXECUTE ON FUNCTION jobwatch.normalize_claim_wording(text) TO jobwatch_calendar_reader;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jobwatch_chat_readonly') THEN
    GRANT SELECT ON
      jobwatch.discovery_candidate_snapshots,
      jobwatch.research_discovery_evidence,
      jobwatch.posting_deadline_claims_distinct,
      jobwatch.research_agent_evidence,
      jobwatch.research_agent_corroborations
    TO jobwatch_chat_readonly;
    GRANT EXECUTE ON FUNCTION jobwatch.normalize_claim_wording(text) TO jobwatch_chat_readonly;
  END IF;
END $$;

COMMIT;
