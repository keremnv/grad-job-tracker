-- OPTIONAL, SEPARATELY APPROVED ADMIN PROVISIONING after migration 0008.
-- Grants read-only Research/Agent access to public discovered vacancies.
-- Never run automatically. Does not grant write access to discovery_candidates.
\set ON_ERROR_STOP on
BEGIN;
GRANT SELECT ON jobwatch.discovery_candidates, jobwatch.research_discovery_candidates
  TO jobwatch_agent_readonly, jobwatch_agent_owner, jobwatch_calendar_reader;
COMMIT;
