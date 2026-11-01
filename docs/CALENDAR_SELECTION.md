# Reviewed calendar selection

Ask ChatGPT to add a chosen set of vacancies to the existing deadlines feed, review
the exact outcome, and approve it once. The architecture is unchanged: PostgreSQL →
ICS snapshot → S3/CloudFront → the existing Proton subscription. There is no new
AWS resource, credential, scheduler, URL or subscription.

## Three different calendars

| Output | What it contains |
| --- | --- |
| `run_report(kind="calendar")` | Legacy stateless export. Ignores the file policy and selections, and has no stable-UID state. |
| Published ICS snapshot | Policy, then selections, then the projection, with retained UID/SEQUENCE. This is what Proton subscribes to. |
| `run_report(kind="calendar_selection")` | Diagnoses the published projection for selected records. |

## Why there is a table

`/etc/jobwatch/calendar-deadlines.sql` is root-owned, unaudited and unreachable from
Owner Actions. The deployment report records the Git-tracked default, which selects
every posting and lead. Under that policy any qualifying deadline is already
published, so adding needs no selection row, but removal cannot be expressed at all.

Migration 0007 adds `jobwatch.calendar_selections`: keys, `include`/`exclude`, the date
shown at approval, time and actor. It has no free text and cannot supply a calendar
date. Published dates always come from retained evidence.

    published = (policy claims ∪ included identities) − excluded identities

Rows act on the whole calendar identity. Removing a lead also removes its linked
posting's claim, and including one cannot hide a contradicting claim. With no rows
the output is byte-for-byte the policy's. The file policy itself is not changed.

## Adding 12 is not limiting to 12

Selection is **additive**. Listing 12 deadlines asks for those 12 to be in the feed; every other
event stays exactly as it is. Under the deployed all-evidence policy a qualifying deadline is
already published, so the 12 usually report `already_included`, write nothing, and the feed may
hold far more than 12 events. Each review's `feed_effect` states the resulting total, and a batch
that would change any unlisted event is refused (`unlisted_event_changed`).

There is deliberately no “exactly these” mode. To limit the calendar, either remove the others
with an explicit `remove` for each, shown item by item in a review, or have an operator narrow
the file policy under separate approval. A narrowed policy is the only way that later evidence
stays out by default. Without it, new qualifying deadlines keep appearing.

## Workflow

1. **Research:** `run_report(kind="calendar_selection", selection=[{company,title,deadline?}|{key}])`,
   up to 25 items. Candidate dates are compared, never trusted. Company/title matching
   only proposes candidates; ties stay `ambiguous`.
2. **Owner Actions:** `prepare_owner_change(recipe="select_calendar_deadlines",
   values={"items":[{key, action, evidence?, confirmed_distinct?}]})`. `evidence` reuses
   `record_lead`, `link_lead`, `correct_lead_deadline` and `verify_posting_deadline`.
   Preparation runs the real write path in a transaction that always rolls back.
3. **Show** `review` (`changes`, `unchanged`, `blocked`) and `details` (evidence and current state).
4. **Commit** with `commit_owner_change(change_id, review)` after client confirmation.
5. **Read back:** the receipt is committed database state, re-read after COMMIT. The refresh
   hint fires, with the hourly publisher as fallback. Repeat step 1 to see the published state.

Statuses: `qualifies`, `missing`, `insufficient`, `conflicting`, `excluded`, `not_selected`,
`outside_window`. An `include` that would not produce an event is `blocked` and writes
nothing, including its evidence. Other items in the batch stay committable, and the review
shows what was blocked. A likely second event for one vacancy is blocked until linked with
`link_lead` or marked `confirmed_distinct`.

**Stale state:** commit repeats the plan and compares it with the reviewed digest. Any change
to the evidence, selections or policy outcome for a listed item rolls back the whole batch.
**Replay:** a confirmed replay returns the original receipt. After a restart reviews are
gone, and preparing the same request writes nothing: evidence that already stands, for all four
evidence recipes, is skipped, and committed items report `already_included`. New wording, source
or check time is new evidence. Only a new event can be a possible duplicate, so one already in
the feed is never blocked for it.
**Removal** needs an explicit `remove`, writes an audited row and deletes no evidence.
Reselecting restores the same UID with a later SEQUENCE.

## Publisher status

After each run the publisher writes `/run/jobwatch-calendar-status/status.json` (directory
0750, file 0640, group `jobwatch-db`): times, outcome, counts and hashed aliases with sequence
numbers. It holds no event text, keys, URLs or tokens. The database stays read-only to the
publisher, and a failed status write cannot affect publication. `/run` clears at reboot, so
status reads `unavailable` until the next hourly run. **Publication status is not evidence
that Proton has fetched anything.**

## Reminders

Unchanged: all-day events for date-only deadlines, no invented expiry times, no `VALARM`.
Set calendar-wide notifications for two days and one day before in Proton. Proton applies
those defaults to subscribed calendars and refreshes roughly every 4–16 hours, so a change
close to a deadline may arrive after the reminder.

## Rollout (needs separate approval; not performed)

Production evidence, read-only on 1 November 2026: `8630f8b` is installed, `schema_migrations`
holds 1–6 and the 0005/0006 grant deltas are present. This rollout therefore applies **only**
0007 and `calendar_selection_access.sql`. Do not rerun earlier deltas or role provisioning.

**Coupling.** `check_migrations` demands exactly the packaged migrations and the exact table set,
and backups call it. The publisher reads the new table with the delta's grant. So every collector,
backup and calendar trigger stays disabled from before the code install until after the delta;
no scheduled run can see mixed versions. Choose a 20-minute window clear of 00:00, 06:15–06:25
and 12:00 UTC. Backup health reads no database and keeps running. Backups run as the migrator,
which owns the new table, so backup needs no grant change. PostgreSQL is not restarted.

No Terraform, AWS resource, URL, credential, Proton or `/etc/jobwatch` change is involved.

### 1. Build and validate both artifacts (operator machine)

```sh
git fetch origin && COMMIT=$(git rev-parse origin/main)   # the approved merge commit
OUT=$(mktemp -d)
python3 infra/aws/scripts/build_release.py --repo . --commit "$COMMIT" --output "$OUT/application.tgz"
python3 infra/aws/scripts/build_agent_release.py --repo . --commit "$COMMIT" --output "$OUT/agent.tgz"
APP_SHA=$(sha256sum "$OUT/application.tgz" | cut -d' ' -f1)
AGENT_SHA=$(sha256sum "$OUT/agent.tgz" | cut -d' ' -f1)
python3 infra/aws/scripts/calendar_selection_check.py artifacts "$OUT/application.tgz" "$OUT/agent.tgz" \
  --application-sha256 "$APP_SHA" --agent-sha256 "$AGENT_SHA" --commit "$COMMIT"
export AWS_PROFILE=grad-jobwatch AWS_REGION=eu-north-1
BUCKET=$(aws s3api list-buckets --query "Buckets[?starts_with(Name,'grad-jobwatch-backups-')].Name" --output text)
for name in application agent; do
  aws s3api put-object --bucket "$BUCKET" --key "artifacts/calendar-selection/$COMMIT/$name.tgz" \
    --body "$OUT/$name.tgz" --server-side-encryption AES256 --query VersionId --output text
done   # record both version IDs
aws cloudwatch describe-alarms --query 'MetricAlarms[].[AlarmName,StateValue]' --output text   # all five OK
```

### 2. Health, backup and baseline (host, as root)

Open a root shell through SSM and stay in one `bash` session for steps 2–6. Set `COMMIT`,
`APP_SHA`, `AGENT_SHA`, `APP_VERSION`, `AGENT_VERSION`. Because of `set -eu`, any failure ends the
shell: inspect it, and do not continue past a failure in step 3 or 4 without restoring step 5.

```sh
set -eu; cd /; umask 077
test "$(readlink -f /opt/jobwatch/current)" = /opt/jobwatch/releases/8630f8b80f0944d539e120e3dbb56e04891ab5cc
if systemctl list-units --failed --plain --no-legend | grep jobwatch; then echo 'Stop: failed unit'; exit 1; fi
BUCKET=$(python3.12 -c "import json;print(json.load(open('/etc/jobwatch/aws.json'))['bucket'])")
WORK=/var/tmp/jobwatch-calendar-$COMMIT; install -d -m 700 "$WORK"
# The instance role has no s3:GetObjectVersion, so check the version returned rather than requesting it.
got=$(aws s3api get-object --region eu-north-1 --bucket "$BUCKET" --key "artifacts/calendar-selection/$COMMIT/application.tgz" "$WORK/application.tgz" --query VersionId --output text)
test "$got" = "$APP_VERSION"
got=$(aws s3api get-object --region eu-north-1 --bucket "$BUCKET" --key "artifacts/calendar-selection/$COMMIT/agent.tgz" "$WORK/agent.tgz" --query VersionId --output text)
test "$got" = "$AGENT_VERSION"
tar -xOf "$WORK/application.tgz" infra/aws/scripts/calendar_selection_check.py > "$WORK/check.py"
systemctl start jobwatch-backup.service; systemctl start jobwatch-backup-health.service
systemctl show jobwatch-backup.service jobwatch-backup-health.service --property=Id,Result
cat /var/lib/jobwatch-backup/latest.json      # confirm its archive and manifest versions in S3
python3.12 "$WORK/check.py" snapshot "$WORK/before.json"
```

### 3. Pause and install

```sh
for unit in jobwatch-collect jobwatch-backup jobwatch-calendar; do
  if systemctl is-active --quiet "$unit.service"; then echo "Stop: $unit is running; wait for it"; exit 1; fi
done
systemctl disable --now jobwatch-collect.timer jobwatch-backup.timer jobwatch-calendar.timer jobwatch-calendar.path \
  jobwatch-tunnel@read.service jobwatch-tunnel@owner.service
systemctl disable --now jobwatch-agent-read.service jobwatch-agent-owner.service
python3.12 /opt/jobwatch-bootstrap/deploy_release.py "$WORK/application.tgz" --sha256 "$APP_SHA" --commit "$COMMIT"
python3.12 /opt/jobwatch/current/infra/aws/scripts/install_agent_runtime.py "$WORK/agent.tgz" --sha256 "$AGENT_SHA" --commit "$COMMIT"
```

### 4. Migration 0007 and its narrow grants

```sh
runuser -u jobwatch-migrator -- env JOBWATCH_MIGRATION_DATABASE_URL='host=/run/jobwatch-postgresql dbname=jobwatch user=jobwatch_migrator' \
  /opt/jobwatch/current/venv/bin/jobwatch-db-migrate
runuser -u jobwatch-migrator -- psql -X -h /run/jobwatch-postgresql -U jobwatch_migrator -d jobwatch -v ON_ERROR_STOP=1 \
  -f /opt/jobwatch/current/sql/postgresql/calendar_selection_access.sql
```

### 5. Restore services and schedules

Only the two MCP services, their tunnels and the publisher restart; PostgreSQL does not.

```sh
systemctl enable --now jobwatch-agent-read.service jobwatch-agent-owner.service
systemctl enable --now jobwatch-tunnel@read.service jobwatch-tunnel@owner.service
systemctl enable --now jobwatch-collect.timer jobwatch-backup.timer jobwatch-calendar.timer jobwatch-calendar.path
systemctl start jobwatch-calendar.service
systemctl show jobwatch-calendar.service --property=Result,ExecMainStatus
```

### 6. Verify, then take the post-upgrade backup

`probe` runs with the running tunnels' exact identity, including their supplementary groups, and
never commits. Do not substitute `runuser`, which omits them and reports a false socket denial.

```sh
CHECK=/opt/jobwatch/current/infra/aws/scripts/calendar_selection_check.py
python3.12 "$CHECK" snapshot "$WORK/after.json"
python3.12 "$CHECK" compare "$WORK/before.json" "$WORK/after.json"   # {"preserved":true,...}
python3.12 "$CHECK" probe                                            # publisher: "available"
stat -c '%U:%G %a' /run/jobwatch-calendar-status /run/jobwatch-calendar-status/status.json
systemctl start jobwatch-backup.service; systemctl start jobwatch-backup-health.service
systemctl show jobwatch-backup.service jobwatch-backup-health.service --property=Id,Result
systemctl list-timers 'jobwatch-*' --no-pager
```

`compare` proves unchanged rows and audit, the exact grant matrix, unchanged private files, the
same PostgreSQL process, and identical calendar UIDs, SEQUENCE, feed bytes and upload receipt.
On the operator machine, confirm all five alarms are OK and the new backup's S3 version, checksum
and manifest, then restore it into a disposable network-isolated PostgreSQL 17 target with the
guarded `database_backup.restore`. Never restore over production.

### 7. Refresh ChatGPT, then one approved change

Refresh both connections as described below, then test in order:

1. Ask for a deadline **already in the feed**. Expect `already_included`, no `change_id`, no write.
2. Prepare one real addition and press **Cancel**. Expect no new `owner_audit` row.
3. Prepare the owner's chosen real addition and approve it. Expect `feed_effect` with
   `added: 1, removed: 0, unlisted_events_changed: 0`, a receipt, and `calendar_refresh_queued`.
4. After the publisher run, `run_report(kind="calendar_selection")` shows `in_published_feed: true`.
5. Later, observe the event in Proton with the same URL and no duplicate. Use the owner's
   choice of test; this rollout creates no test record.

## Rollback

Before any owner writes, as root: pause exactly as in step 3, then reinstall the `8630f8b` artifacts
(both installers accept the existing verified release). As the migrator, in one transaction, run
`DROP TABLE jobwatch.calendar_selections; DELETE FROM jobwatch.schema_migrations WHERE version=7;`,
so the old code's exact check passes. Then restore services as in step 5. Do not restore a dump.

After selections exist, prefer a reviewed forward fix so audited decisions stay in force. To stop
new use without a schema change, stop only the Owner Actions tunnel. A code rollback would make
removed events return, because old code does not read exclusions. Calendar UIDs stay stable
throughout, as the transport state is untouched; never reinitialise it.

## ChatGPT refresh

`run_report` gains a `selection` argument and both tool descriptions change, so **both connections
need refreshing** after rollout: Settings → Apps/Connectors → *Graduate Job Watch Research* →
refresh (or disconnect and reconnect through the same Secure MCP Tunnel), then the same for
*Graduate Job Watch Owner Actions*. Keep names, tunnels and “no authentication behind the tunnel”;
do not add a new connection or choose “always allow”. Start a new conversation afterwards. Check
that `run_report` lists `selection` and `prepare_owner_change` mentions `select_calendar_deadlines`.
UI labels vary by account.

## Limitations

- Pending reviews are in memory. An uncertain commit needs a fresh read before retrying.
- The token binds the change. It does not prove a human approved; ChatGPT's confirmation does that.
- Company/title resolution and duplicate warnings are heuristics and never authorise a write.
- A batch evaluates the whole deadline set several times. The heaviest read takes about 120 ms on
  the production host. A 25-item batch at production row counts, under the Owner service's timeouts,
  `MemoryMax=192M` and an 8% CPU quota on x86, took 7.7 s to prepare and 6.0 s to commit, with a
  43 MiB peak. Timing on the ARM64 host itself is unmeasured until rollout. A response over the
  131,072-byte bound fails closed; split that batch.
