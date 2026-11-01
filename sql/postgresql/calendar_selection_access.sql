-- Additive administrator delta AFTER migration 0007 and existing agent provisioning.
-- Apply this delta only; never rerun broad role provisioning. No DELETE is granted:
-- removal is an audited 'exclude' row, and evidence is never removed.
\set ON_ERROR_STOP on
BEGIN;
GRANT SELECT ON jobwatch.calendar_selections
  TO jobwatch_agent_readonly,jobwatch_agent_owner,jobwatch_calendar_reader;
GRANT INSERT (posting_key,lead_key,mode,approved_deadline,recorded_by),
  UPDATE (mode,approved_deadline,decided_at,recorded_by)
  ON jobwatch.calendar_selections TO jobwatch_agent_owner;
COMMIT;
