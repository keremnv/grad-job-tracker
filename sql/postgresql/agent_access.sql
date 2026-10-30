-- OPTIONAL, SEPARATELY APPROVED ADMIN PROVISIONING after migrations 0001–0005.
-- Then apply lead_deadline_access.sql for the narrowly scoped deadline recipe grants.
-- Provision agent_roles.sql first. No automatic deployment or grant reset. Run once with psql
-- as role administrator + schema grant administrator. Existing names fail atomically.
-- The broad Stage 4C jobwatch_chat_readonly role remains unchanged/test-only.
\set ON_ERROR_STOP on
BEGIN;
GRANT USAGE ON SCHEMA jobwatch TO jobwatch_agent_readonly,jobwatch_agent_owner,jobwatch_calendar_reader;
GRANT SELECT ON jobwatch.sources,jobwatch.collection_runs,jobwatch.postings,
  jobwatch.posting_versions,jobwatch.posting_events,
  jobwatch.schema_migrations,jobwatch.embedding_chunks,jobwatch.research_postings,
  jobwatch.source_health,jobwatch.opportunity_workflow,jobwatch.operational_postings
  TO jobwatch_agent_readonly,jobwatch_agent_owner,jobwatch_calendar_reader;
GRANT SELECT (id,posting_key,kind,checked_at,recorded_at,actor,checked_by,url,method,
  status,wording,value,version_id,description_sha256) ON jobwatch.verifications
  TO jobwatch_agent_readonly,jobwatch_agent_owner,jobwatch_calendar_reader;
GRANT SELECT (id,slug,name,careers_url,watch_status,watch_checked_at) ON jobwatch.employers
  TO jobwatch_agent_readonly,jobwatch_agent_owner,jobwatch_calendar_reader;
GRANT SELECT (job_key,posting_key,company,role,url,url_exact,url_loose,stage,date_applied,
  next_action,next_action_date) ON jobwatch.applications
  TO jobwatch_agent_readonly,jobwatch_agent_owner,jobwatch_calendar_reader;
GRANT SELECT (posting_key,decision,reason_code,revisit_on,decided_at,recorded_by) ON jobwatch.triage
  TO jobwatch_agent_readonly,jobwatch_agent_owner,jobwatch_calendar_reader;
GRANT SELECT (lead_key,kind,company,title,programme_key,cycle_year,cycle_wording,state,
  url,url_exact,official,wording,deadline_date,deadline_wording,posting_key,checked_at,checked_by)
  ON jobwatch.leads TO jobwatch_agent_readonly,jobwatch_agent_owner,jobwatch_calendar_reader;
GRANT EXECUTE ON FUNCTION jobwatch.url_identity(text,boolean)
  TO jobwatch_agent_readonly,jobwatch_agent_owner,jobwatch_calendar_reader;

SET LOCAL ROLE jobwatch_migrator;
CREATE VIEW jobwatch.agent_leads AS
SELECT lead_key,kind,company,title,programme_key,cycle_year,cycle_wording,state,
  url,url_exact,official,NULL::text AS source_note,wording,deadline_date,deadline_wording,
  posting_key,checked_at,checked_by,
  jsonb_strip_nulls(jsonb_build_object(
    'window',coalesce(details->>'window',(details->>'legacy_json')::jsonb->>'window'),
    'deadline_source_url',coalesce(details#>>'{deadline,url}',((details->>'legacy_json')::jsonb)#>>'{deadline,url}'),
    'uncertainty',coalesce(details->>'uncertainty',(details->>'legacy_json')::jsonb->>'uncertainty')))
    AS details,deadline_source_url,deadline_official,deadline_checked_at,deadline_checked_by
FROM jobwatch.leads;
CREATE VIEW jobwatch.agent_audit_summary AS
SELECT id,at,db_login,actor,table_name,op FROM jobwatch.owner_audit;
GRANT SELECT ON jobwatch.agent_leads,jobwatch.agent_audit_summary
  TO jobwatch_agent_readonly,jobwatch_agent_owner,jobwatch_calendar_reader;
RESET ROLE;

-- Fixed cookbook execution only in the write transport; no arbitrary SQL tool there.
GRANT SELECT (note) ON jobwatch.triage TO jobwatch_agent_owner;
GRANT SELECT (source_note) ON jobwatch.leads TO jobwatch_agent_owner;
GRANT INSERT (job_key,company,role,url,stage) ON jobwatch.applications TO jobwatch_agent_owner;
GRANT UPDATE (posting_key,stage,date_applied,next_action,next_action_date)
  ON jobwatch.applications TO jobwatch_agent_owner;
GRANT INSERT (posting_key,decision,reason_code,note,revisit_on,recorded_by),
  UPDATE (decision,reason_code,note,revisit_on,decided_at,recorded_by)
  ON jobwatch.triage TO jobwatch_agent_owner;
GRANT INSERT (lead_key,kind,company,title,programme_key,cycle_year,cycle_wording,state,
  url,official,source_note,wording,deadline_date,deadline_wording,checked_at,checked_by),
  UPDATE (posting_key) ON jobwatch.leads TO jobwatch_agent_owner;
-- No applications.notes/cv_version, leads.details, audit images/writes, DELETE,
-- collector tables/control updates, embedding writes, membership or schema creation.
COMMIT;
