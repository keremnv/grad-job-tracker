-- ISOLATED PROPOSAL (NOT DEPLOYED TO PRODUCTION).
-- Strictly controlled least-privilege role for agent public evidence ingestion.
--
-- Security invariant:
--   `jobwatch_agent_evidence` has ZERO raw INSERT/UPDATE/DELETE privileges on ANY
--   table! Every submission MUST pass through the two SECURITY DEFINER functions
--   (`submit_agent_observation`, `submit_agent_vacancy`) which enforce:
--     - Authenticated `session_user` attribution (cannot spoof `db_login`)
--     - Transactional advisory-locked hourly/daily budgets per `db_login`
--     - Strict semantic calendar date validation (`make_date`)
--     - Strict idempotency payload-collision refusal (`idempotency_key_conflict`)
--     - Atomic candidate + historical snapshot + observation persistence
--   ZERO access to corroboration writes (`agent_evidence_corroborations`),
--   ZERO access to owner decisions (`applications`, `triage`, `calendar_selections`,
--   `leads`, `verifications`, `owner_audit`) or collector tables (`sources`, `postings`).

\set ON_ERROR_STOP on
BEGIN;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jobwatch_agent_evidence') THEN
    CREATE ROLE jobwatch_agent_evidence LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION;
  END IF;
END $$;

REVOKE ALL ON SCHEMA jobwatch FROM jobwatch_agent_evidence;
GRANT USAGE ON SCHEMA jobwatch TO jobwatch_agent_evidence;

-- Narrow read-only visibility to resolve targets and inspect own/public observations.
GRANT SELECT ON
  jobwatch.research_postings,
  jobwatch.research_discovered_boards,
  jobwatch.research_discovery_candidates,
  jobwatch.research_discovery_evidence,
  jobwatch.posting_deadline_claims,
  jobwatch.research_agent_evidence,
  jobwatch.research_agent_corroborations
TO jobwatch_agent_evidence;

-- Controlled ingestion boundary ONLY (NO raw INSERT/UPDATE/DELETE on any table!).
GRANT EXECUTE ON FUNCTION jobwatch.submit_agent_observation(
  text,text,text,text,text,timestamptz,text,text,text,text,text,text,text,bigint,text,jsonb,text
) TO jobwatch_agent_evidence;

GRANT EXECUTE ON FUNCTION jobwatch.submit_agent_vacancy(
  text,text,text,text,timestamptz,text,text,text,text,text,text,text,text,text,text[],
  text,text,text,text,text,text,text,text,text[],text,text,text,text,jsonb,text
) TO jobwatch_agent_evidence;

-- Read-only visibility for Research role when provisioned.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jobwatch_agent_readonly') THEN
    GRANT SELECT ON
      jobwatch.research_discovery_evidence,
      jobwatch.discovery_candidate_snapshots,
      jobwatch.research_agent_evidence,
      jobwatch.research_agent_corroborations
    TO jobwatch_agent_readonly;
  END IF;
END $$;

COMMIT;
