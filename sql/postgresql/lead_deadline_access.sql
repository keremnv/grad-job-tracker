-- Additive administrator provisioning AFTER migration 0005 and agent_access.sql.
-- Apply this delta to existing installations; never rerun broad provisioning.
\set ON_ERROR_STOP on
BEGIN;
GRANT SELECT (deadline_source_url,deadline_official,deadline_checked_at,deadline_checked_by)
  ON jobwatch.leads TO jobwatch_agent_readonly,jobwatch_agent_owner,jobwatch_calendar_reader;
GRANT UPDATE (deadline_date,deadline_wording,deadline_source_url,deadline_official,
  deadline_checked_at,deadline_checked_by) ON jobwatch.leads TO jobwatch_agent_owner;
-- The ordinary owner already reads all lead evidence; allow the same public snapshot
-- used by the cookbook's concurrency guard, without exposing anything new to agents.
GRANT SELECT ON jobwatch.agent_leads TO jobwatch_owner;
COMMIT;
