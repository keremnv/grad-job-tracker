-- OPTIONAL, SEPARATELY APPROVED ADMIN PROVISIONING after migration 0011.
-- Grants strictly read-only Research/Agent access to discovery evidence snapshots
-- and the single-version-consistent `research_discovery_evidence` view.
-- Never run automatically. Does not grant any write access.
\set ON_ERROR_STOP on
BEGIN;
GRANT SELECT ON jobwatch.discovery_candidate_snapshots, jobwatch.research_discovery_evidence
  TO jobwatch_agent_readonly, jobwatch_agent_owner, jobwatch_calendar_reader;
COMMIT;
