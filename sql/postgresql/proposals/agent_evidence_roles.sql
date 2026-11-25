-- ISOLATED PROPOSAL (NOT DEPLOYED TO PRODUCTION).
-- Narrow least-privilege role for agent public evidence ingestion.
-- Enforces strict database separation:
--   - Append-only INSERT on `agent_evidence_observations` (excluding corroboration columns!)
--   - Append-only INSERT on `discovery_candidates` (excluding owner-promotion/link columns!)
--   - ZERO UPDATE/DELETE privileges anywhere
--   - ZERO write privileges on `applications`, `triage`, `calendar_selections`,
--     `leads`, `verifications`, `postings`, `posting_versions`, `sources`, `employers`, `owner_audit`.

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
GRANT EXECUTE ON FUNCTION jobwatch.url_identity(text, boolean) TO jobwatch_agent_evidence;

-- Narrow read visibility to resolve targets and check duplicates/rate budgets.
GRANT SELECT ON
  jobwatch.research_postings,
  jobwatch.research_discovered_boards,
  jobwatch.research_discovery_candidates,
  jobwatch.posting_deadline_claims,
  jobwatch.agent_evidence_observations,
  jobwatch.research_agent_evidence
TO jobwatch_agent_evidence;

-- Append-only INSERT on observations: corroboration columns intentionally OMITTED so
-- an agent submission can NEVER self-corroborate!
GRANT INSERT (
  observation_id,
  idempotency_key,
  kind,
  contributor,
  observed_at,
  source_url,
  posting_key,
  candidate_key,
  board_key,
  source_id,
  lead_key,
  claim_summary,
  observed_wording,
  candidate_deadline_date,
  payload,
  payload_sha256
) ON jobwatch.agent_evidence_observations TO jobwatch_agent_evidence;

-- Append-only INSERT on discovery_candidates for `new_vacancy` submissions:
-- excludes source_id, linked_posting_key, promoted_lead_key, and NO UPDATE/DELETE.
GRANT INSERT (
  candidate_key,
  provider,
  board,
  native_id,
  company,
  title,
  posting_url,
  location_text,
  country,
  countries,
  department,
  team,
  employment_type,
  workplace_type,
  published_at,
  source_dates,
  deadlines,
  description_excerpt,
  description_sha256,
  evidence_sha256,
  state,
  discovery_mechanisms,
  provenance,
  verdict,
  family,
  codes,
  rules_version,
  screening_input_sha256,
  screening_basis
) ON jobwatch.discovery_candidates TO jobwatch_agent_evidence;

-- Read-only agent visibility (only when read role exists)
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jobwatch_agent_readonly') THEN
    GRANT SELECT ON jobwatch.agent_evidence_observations, jobwatch.research_agent_evidence
      TO jobwatch_agent_readonly;
  END IF;
END $$;

COMMIT;
