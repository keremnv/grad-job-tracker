-- OPTIONAL, SEPARATELY APPROVED ADMIN PROVISIONING after migration 0010.
-- Grants strictly read-only Research/Agent access to discovered ATS boards.
-- Never run automatically. Does not grant write access to discovered_boards.
\set ON_ERROR_STOP on
BEGIN;
GRANT SELECT ON jobwatch.discovered_boards, jobwatch.research_discovered_boards
  TO jobwatch_agent_readonly, jobwatch_agent_owner, jobwatch_calendar_reader;
COMMIT;
