-- OWNER SQL COOKBOOK. Copy ONE marked recipe into psql, not this whole file.
-- All parameters are psql variables, quoted with :'name'; empty optional values mean NULL.
-- Use private stdin/interactive variable input for real notes, never shell arguments/Git.
-- ON_ERROR_STOP and disconnect roll back failed transactions. Exit 3 = stale/mismatched
-- state: reread; do not blindly overwrite. Psycopg clients use bound parameters and
-- check RETURNING/rowcount/postconditions in the same ordinary transaction.
-- No source observation, evidence, screening, infrastructure or MCP write authority.

-- BEGIN create_application
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL jobwatch.actor = :'actor';
INSERT INTO jobwatch.applications(job_key,company,role,url,stage)
VALUES (:'job_key',:'company',:'role',:'url','bookmarked')
ON CONFLICT(job_key) DO NOTHING;
-- An uncertain-commit retry verifies identity and preserves any later stage/follow-up.
SELECT count(*)=1 AS ok FROM jobwatch.applications
WHERE job_key=:'job_key' AND company=:'company' AND role=:'role'
  AND url_exact=jobwatch.url_identity(:'url',true) \gset
\if :ok
COMMIT;
\else
ROLLBACK;
DO $$ BEGIN RAISE EXCEPTION 'Owner write not confirmed: reread expected state'; END $$;
\endif
SELECT job_key,posting_key,stage FROM jobwatch.applications WHERE job_key=:'job_key';
-- END create_application

-- BEGIN link_application
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL jobwatch.actor = :'actor';
UPDATE jobwatch.applications SET posting_key=:'posting_key'
WHERE job_key=:'job_key' AND (posting_key IS NOT DISTINCT FROM nullif(:'expected_posting_key','')
                             OR posting_key=:'posting_key')
RETURNING job_key,posting_key;
SELECT count(*)=1 AS ok FROM jobwatch.applications
WHERE job_key=:'job_key' AND posting_key=:'posting_key' \gset
\if :ok
COMMIT;
\else
ROLLBACK;
DO $$ BEGIN RAISE EXCEPTION 'Owner write not confirmed: reread expected state'; END $$;
\endif
-- Explicit association only; never infer this write from a title/loose URL match.
-- END link_application

-- BEGIN link_lead
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL jobwatch.actor = :'actor';
UPDATE jobwatch.leads SET posting_key=:'posting_key'
WHERE lead_key=:'lead_key' AND kind='vacancy'
 AND (posting_key IS NOT DISTINCT FROM nullif(:'expected_posting_key','') OR posting_key=:'posting_key');
SELECT count(*)=1 AS ok FROM jobwatch.leads
WHERE lead_key=:'lead_key' AND posting_key=:'posting_key' AND kind='vacancy' \gset
\if :ok
COMMIT;
\else
ROLLBACK;
DO $$ BEGIN RAISE EXCEPTION 'Owner write not confirmed: reread expected state'; END $$;
\endif
SELECT lead_key,posting_key FROM jobwatch.leads WHERE lead_key=:'lead_key';
-- END link_lead

-- BEGIN update_stage
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL jobwatch.actor = :'actor';
UPDATE jobwatch.applications SET stage=:'stage',date_applied=nullif(:'date_applied','')::date
WHERE job_key=:'job_key' AND (
 (stage,date_applied) IS NOT DISTINCT FROM (:'expected_stage',nullif(:'expected_date_applied','')::date)
 OR (stage,date_applied) IS NOT DISTINCT FROM (:'stage',nullif(:'date_applied','')::date))
RETURNING job_key,stage,date_applied;
SELECT count(*)=1 AS ok FROM jobwatch.applications WHERE job_key=:'job_key'
  AND (stage,date_applied) IS NOT DISTINCT FROM (:'stage',nullif(:'date_applied','')::date) \gset
\if :ok
COMMIT;
\else
ROLLBACK;
DO $$ BEGIN RAISE EXCEPTION 'Owner write not confirmed: reread expected state'; END $$;
\endif
-- Application stage/date are owner facts, never inferred from a feed or today's clock.
-- END update_stage

-- BEGIN set_followup
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL jobwatch.actor = :'actor';
-- Set/reschedule: supply action/date. Clear a completed/cancelled pending action:
-- supply both empty. No application-stage change or employer contact is implied.
UPDATE jobwatch.applications SET next_action=nullif(:'action',''),next_action_date=nullif(:'action_date','')::date
WHERE job_key=:'job_key' AND (
 (nullif(next_action,''),next_action_date) IS NOT DISTINCT FROM
   (nullif(:'expected_action',''),nullif(:'expected_action_date','')::date)
 OR (nullif(next_action,''),next_action_date) IS NOT DISTINCT FROM
   (nullif(:'action',''),nullif(:'action_date','')::date))
RETURNING job_key,next_action_date;
SELECT count(*)=1 AS ok FROM jobwatch.applications WHERE job_key=:'job_key'
  AND (nullif(next_action,''),next_action_date) IS NOT DISTINCT FROM
    (nullif(:'action',''),nullif(:'action_date','')::date) \gset
\if :ok
COMMIT;
\else
ROLLBACK;
DO $$ BEGIN RAISE EXCEPTION 'Owner write not confirmed: reread expected state'; END $$;
\endif
-- END set_followup

-- BEGIN set_triage
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL jobwatch.actor = :'actor';
-- expected_decision='absent' only when the owner row was absent at the read.
INSERT INTO jobwatch.triage(posting_key,decision,reason_code,note,revisit_on,recorded_by)
SELECT :'posting_key',:'decision',nullif(:'reason',''),nullif(:'note',''),
       nullif(:'revisit_on','')::date,'agent_on_owner_instruction'
WHERE :'expected_decision'='absent'
ON CONFLICT(posting_key) DO NOTHING;
UPDATE jobwatch.triage SET decision=:'decision',reason_code=nullif(:'reason',''),note=nullif(:'note',''),
  revisit_on=nullif(:'revisit_on','')::date,decided_at=clock_timestamp(),recorded_by='agent_on_owner_instruction'
WHERE posting_key=:'posting_key'
  AND (decision,reason_code,nullif(note,''),revisit_on) IS NOT DISTINCT FROM
    (:'expected_decision',nullif(:'expected_reason',''),nullif(:'expected_note',''),nullif(:'expected_revisit_on','')::date)
  AND (decision,reason_code,nullif(note,''),revisit_on) IS DISTINCT FROM
    (:'decision',nullif(:'reason',''),nullif(:'note',''),nullif(:'revisit_on','')::date);
SELECT count(*)=1 AS ok FROM jobwatch.triage WHERE posting_key=:'posting_key'
  AND (decision,reason_code,nullif(note,''),revisit_on) IS NOT DISTINCT FROM
    (:'decision',nullif(:'reason',''),nullif(:'note',''),nullif(:'revisit_on','')::date) \gset
\if :ok
COMMIT;
\else
ROLLBACK;
DO $$ BEGIN RAISE EXCEPTION 'Owner write not confirmed: reread expected state'; END $$;
\endif
SELECT posting_key,decision,revisit_on,decided_at,recorded_by FROM jobwatch.triage WHERE posting_key=:'posting_key';
-- END set_triage

-- BEGIN record_lead
\set ON_ERROR_STOP on
BEGIN;
SET LOCAL jobwatch.actor = :'actor';
-- checked_at/observer/wording must describe an actual source observation.
-- official=false is an unverified source, not a fabricated official check.
INSERT INTO jobwatch.leads(lead_key,kind,company,title,programme_key,cycle_year,cycle_wording,
 state,url,official,source_note,wording,deadline_date,deadline_wording,checked_at,checked_by)
VALUES (:'lead_key',:'kind',:'company',:'title',nullif(:'programme_key',''),nullif(:'cycle_year','')::integer,
 nullif(:'cycle_wording',''),:'state',:'url',:'official'::boolean,:'source_note',nullif(:'wording',''),
 nullif(:'deadline_date','')::date,nullif(:'deadline_wording',''),:'checked_at'::timestamptz,:'actor')
ON CONFLICT(lead_key) DO NOTHING;
SELECT count(*)=1 AS ok FROM jobwatch.leads WHERE lead_key=:'lead_key'
 AND (kind,company,title,programme_key,cycle_year,cycle_wording,state,url_exact,official,source_note,
      wording,deadline_date,deadline_wording,checked_at,checked_by) IS NOT DISTINCT FROM
 (:'kind',:'company',:'title',nullif(:'programme_key',''),nullif(:'cycle_year','')::integer,
  nullif(:'cycle_wording',''),:'state',jobwatch.url_identity(:'url',true),:'official'::boolean,:'source_note',
  nullif(:'wording',''),nullif(:'deadline_date','')::date,nullif(:'deadline_wording',''),:'checked_at'::timestamptz,:'actor') \gset
\if :ok
COMMIT;
\else
ROLLBACK;
DO $$ BEGIN RAISE EXCEPTION 'Owner write not confirmed: reread expected state'; END $$;
\endif
SELECT lead_key,kind,cycle_year,state,checked_at FROM jobwatch.leads WHERE lead_key=:'lead_key';
-- END record_lead
