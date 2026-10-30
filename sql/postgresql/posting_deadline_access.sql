-- Additive administrator delta AFTER migration 0006 and existing agent provisioning.
\set ON_ERROR_STOP on
BEGIN;
GRANT SELECT (deadline_supersedes,deadline_reason) ON jobwatch.verifications
  TO jobwatch_agent_readonly,jobwatch_agent_owner,jobwatch_calendar_reader;
GRANT SELECT ON jobwatch.posting_deadline_claims,jobwatch.posting_deadline_state
  TO jobwatch_agent_readonly,jobwatch_agent_owner,jobwatch_calendar_reader;
-- No status/version/hash columns: description and application-status checks cannot
-- satisfy their constraints with these INSERT privileges. No UPDATE or DELETE.
GRANT INSERT (id,posting_key,kind,checked_at,checked_by,url,wording,value,
  deadline_supersedes,deadline_reason) ON jobwatch.verifications TO jobwatch_agent_owner;
GRANT INSERT (deadline_supersedes,deadline_reason) ON jobwatch.verifications TO jobwatch_owner;
COMMIT;
