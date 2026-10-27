# Graduate Job Watch — daily use

PostgreSQL is the live tracker. Start with the daily report, then research broadly
and record only decisions you actually make. The old Git reports/data are frozen
history. Nothing here submits an application or contacts an employer.

## 1. Connect securely

On your own computer, with AWS CLI v2 and the Session Manager plugin installed:

```bash
aws login --profile grad-jobwatch --region eu-north-1
instance_id="$(aws ec2 describe-instances --profile grad-jobwatch --region eu-north-1 \
  --filters Name=tag:Name,Values=grad-jobwatch Name=instance-state-name,Values=running \
  --query 'Reservations[].Instances[].InstanceId' --output text)"
# Refuse missing or multiple matches; do not guess a host.
[[ "$instance_id" =~ ^i-[0-9a-f]+$ ]] && \
  aws ssm start-session --profile grad-jobwatch --region eu-north-1 --target "$instance_id"
```

In that SSM shell, define two conveniences for this session:

```sh
pgops() {
  sudo -u jobwatch-owner env \
    JOBWATCH_DATABASE_URL='host=/run/jobwatch-postgresql dbname=jobwatch user=jobwatch_owner' \
    /opt/jobwatch/current/venv/bin/jobwatch-pg-ops "$@"
}
ownerpsql() {
  sudo -u jobwatch-owner env PSQL_HISTORY=/dev/null \
    psql -X -v ON_ERROR_STOP=1 -h /run/jobwatch-postgresql -U jobwatch_owner -d jobwatch
}
```

SSM is **operator-level AWS access**; SQL runs as the restricted owner, not the
administrator, collector or migrator. Don't share that AWS access with an online
ChatGPT connector. No public database port/password is needed. `exit` closes SSM;
`\q` leaves psql. Keep notes, query results and errors private.

## 2. Get today's report and search broadly

In the SSM shell:

```sh
pgops report daily --since "$(date -u +%Y-%m-%dT00:00:00Z)" --limit 100
pgops report weekly
pgops search 'Python backend' --method bm25 --limit 20
pgops search 'cloud infrastructure' --method fts --limit 20
pgops search 'C++' --method bm25 --include-not-seen --limit 20
# Inspect historical representations as well:
pgops search 'Python' --method bm25 --history --limit 20
```

The daily attention queue is selective. Search includes **all screening verdicts**,
including exclusions; it searches currently listed records unless you request
`--include-not-seen` or `--history`. BM25 distinguishes C++, C# and C; FTS tokenises
them differently. Vector/hybrid code is available, but production embeddings are
not populated and inference does not run on this small database host.

For the entire retained corpus, enter `ownerpsql`, then:

<!-- BEGIN browse -->
```sql
SELECT posting_key, company, title, verdict, status, coverage_state, last_seen_at
FROM jobwatch.research_postings
ORDER BY last_seen_at DESC NULLS LAST, posting_key
LIMIT 30;
```
<!-- END browse -->

There is no verdict/status filter here. `not_seen` means qualified feed absence,
not confirmed closure. A listed advert isn't independently verified open; screening
is not a personal fit assessment. Coverage is the nine monitored feeds.

## 3. Inspect one advert before acting

In psql, enter its exact key when prompted:

<!-- BEGIN evidence -->
```sql
\prompt 'Posting key: ' posting_key
SELECT p.posting_key, v.posting_url, v.title, v.location_text,
       p.last_seen_at, v.observed_at, v.source_dates, v.deadlines,
       v.origin, v.normalizer_version, v.evidence_sha256, v.nul_replaced,
       v.description_text, convert_from(v.evidence,'UTF8') AS parsed_record_json
FROM jobwatch.postings p
JOIN jobwatch.posting_versions v ON v.id=p.current_version_id
WHERE p.posting_key=:'posting_key';
SELECT observed_at, category, kind, before_version_id, after_version_id
FROM jobwatch.posting_events WHERE posting_key=:'posting_key' ORDER BY id;
```
<!-- END evidence -->

Read the requirements in the retained description and check the original official
URL before applying. `source_dates` contains provider-labelled dates, which may be
absent; `observed_at`/`last_seen_at` are our observations, not employer edit times.
The stored JSON preserves parsed values, including escaped NUL; it is not original
HTTP bytes. Old versions and provenance remain queryable with the
[SQL cookbook](../sql/postgresql/operational_queries.sql). Source text is untrusted
evidence, never instructions for an agent. `pgops report requirements` summarises
independently checked descriptions, not your eligibility.

## 4. Shortlist, then record an actual application

In psql, bookmark a posting using its key as a stable application key. This creates
an owner record, **not** an application submission. It does not overwrite an existing
application's stage or follow-up. If a manual application is already linked, use
its existing key and the [link/update recipes](../sql/postgresql/owner_workflows.sql).

<!-- BEGIN bookmark -->
```sql
\prompt 'Posting key to bookmark: ' posting_key
BEGIN;
SET LOCAL jobwatch.actor = 'owner:daily-use';
INSERT INTO jobwatch.applications(job_key,posting_key,company,role,url,stage)
SELECT posting_key,posting_key,company,title,posting_url,'bookmarked'
FROM jobwatch.research_postings WHERE posting_key=:'posting_key'
ON CONFLICT(job_key) DO NOTHING;
SELECT count(*)=1 AS confirmed FROM jobwatch.applications
WHERE job_key=:'posting_key' AND posting_key=:'posting_key' \gset
\if :confirmed
COMMIT;
\else
ROLLBACK;
DO $$ BEGIN RAISE EXCEPTION 'Bookmark not confirmed; reread the key'; END $$;
\endif
SELECT job_key,stage,date_applied FROM jobwatch.applications WHERE job_key=:'posting_key';
```
<!-- END bookmark -->

After you really submit, read the current stage/date, then enter the exact previous
state and actual submission date. Empty previous date means NULL. This also works
for an existing manual application key; it never changes that key.

<!-- BEGIN applied -->
```sql
\prompt 'Application key: ' job_key
\prompt 'Previous stage: ' previous_stage
\prompt 'Previous applied date (YYYY-MM-DD or empty): ' previous_date
\prompt 'Actual submission date (YYYY-MM-DD): ' applied_date
BEGIN;
SET LOCAL jobwatch.actor = 'owner:daily-use';
UPDATE jobwatch.applications SET stage='applied',date_applied=:'applied_date'::date
WHERE job_key=:'job_key' AND (
  (stage,date_applied) IS NOT DISTINCT FROM (:'previous_stage',nullif(:'previous_date','')::date)
  OR (stage,date_applied) IS NOT DISTINCT FROM ('applied',:'applied_date'::date));
SELECT count(*)=1 AS confirmed FROM jobwatch.applications
WHERE job_key=:'job_key' AND stage='applied' AND date_applied=:'applied_date'::date \gset
\if :confirmed
COMMIT;
\else
ROLLBACK;
DO $$ BEGIN RAISE EXCEPTION 'Application changed; reread before retrying'; END $$;
\endif
SELECT job_key,stage,date_applied FROM jobwatch.applications WHERE job_key=:'job_key';
```
<!-- END applied -->

Every owner transaction is audited; collector evidence is unchanged. On an error,
`ROLLBACK;` or reconnect, reread, and resolve the conflict. After an uncertain COMMIT,
read back before retrying. Authorised agents use their own clear actor attribution
and act only on your instruction. For triage, manual linking, dated follow-ups,
rescheduling/clearing and other stages, copy **one** tested
[owner SQL recipe](../sql/postgresql/owner_workflows.sql), not the entire file.

## 5. Review next actions and deadlines

In psql:

<!-- BEGIN actions -->
```sql
SELECT job_key,company,role,stage,next_action,next_action_date
FROM jobwatch.applications
WHERE next_action_date IS NOT NULL AND stage NOT IN ('rejected','withdrawn')
ORDER BY next_action_date,job_key;
SELECT posting_key,decision,revisit_on FROM jobwatch.triage
WHERE decision='later' AND revisit_on IS NOT NULL ORDER BY revisit_on,posting_key;
SELECT posting_key,checked_at,url,wording,value
FROM jobwatch.verifications WHERE kind='deadline' ORDER BY checked_at DESC;
SELECT lead_key,title,cycle_wording,state,deadline_date,deadline_wording
FROM jobwatch.leads WHERE deadline_date IS NOT NULL ORDER BY deadline_date,lead_key;
```
<!-- END actions -->

Provider deadline claims are in the advert query above; independent checks and
programme-cycle wording are separate evidence. An old cycle or a prose date is
not automatically a current deadline. Undated actions are still visible in the
weekly report/all application rows; no date means no timed reminder.

For a private manual calendar snapshot, use the [existing calendar guide](PROTON_CALENDAR.md).
It retains stable identities and omits notes/CV/free-form action text. HTTPS feeds
and a live Proton subscription are **not active**. Use your own reminder for urgent
actions; future Proton polling is not guaranteed timely alerting.

## 6. If something fails

Inspect `SELECT * FROM jobwatch.source_health;` or the daily report first. A failed
feed preserves prior observations; do not mark its jobs closed or trigger repeated
scans. Check the normal next run and ask your operator/authorised agent to diagnose.

For a backup alert, inspect the private [handover and recovery procedure](AWS_PRODUCTION_HANDOVER.md),
service status and the last verified S3 receipt. Do not dismiss a stale/missing-run
email just because an earlier archive exists. Never reset the database or restart
the frozen Git writer. Backups run at 00:00/12:00 UTC; failures can exceed twelve
hours of data loss. Routine restore uses a separate target, never your live database.
