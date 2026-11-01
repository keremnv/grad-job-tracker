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
gone, and preparing the same request reports committed items as `already_included`.
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

**First check the host.** `INTEGRATION_RELEASE.md` records `41f640b`, and no document records
0005/0006 being deployed. If absent, apply them and their deltas in step 4, in order.

Code and schema are coupled: `check_migrations` demands exactly the packaged migrations, and
backups call it. Mixed versions make backups fail, so finish between the 00:00 and 12:00 UTC runs.

1. Build both artifacts from one full commit. Take and verify the normal pre-maintenance backup.
2. Record unit states. Discard pending Owner reviews. Pause only what the installers require.
3. Install with `deploy_release.py` and `install_agent_runtime.py` (updates three units).
4. `jobwatch-db-migrate` as the migrator, then only `sql/postgresql/calendar_selection_access.sql`
   as the grant administrator. Do not rerun role provisioning. PostgreSQL needs no restart.
5. Start both MCP services and restore tunnels. Run `jobwatch-calendar.service` once and restore
   all timers and paths exactly. No Terraform, URL, credential or `/etc/jobwatch` change.
6. Confirm the next normal backup receipt and restore proof.

## Rollback

Before any owner writes, restore the previous release, then as the migrator in one transaction
drop `jobwatch.calendar_selections` and delete the 0007 row from `schema_migrations`; old code's
exact check then passes again. Migration 0007 changes nothing else. Do not restore a database dump.

After selections exist, prefer a reviewed forward fix so audited decisions survive. To stop new
use without a schema change, stop only the Owner Actions tunnel. With old code, exclusions stop
applying and removed events return. Reselected UIDs stay stable throughout, as the transport
state is untouched; never reinitialise it.

## ChatGPT refresh

`run_report` gains a `selection` argument and both tool descriptions change, so **both connections
need refreshing** after rollout: Settings → Apps/Connectors → *Graduate Job Watch Research* →
refresh (or disconnect and reconnect through the same Secure MCP Tunnel), then the same for
*Graduate Job Watch Owner Actions*. Keep names, tunnels and “no authentication behind the tunnel”;
do not add a new connection or choose “always allow”. Start a new conversation afterwards. Check
that `run_report` lists `selection` and `prepare_owner_change` mentions `select_calendar_deadlines`.
Then confirm with a small real change that Cancel writes nothing. UI labels vary by account.

## Production verification

- [ ] `schema_migrations` ends at 0007; the delta grants match the tested matrix.
- [ ] Next backup and its restore proof succeed; collectors, health and alarms are unchanged.
- [ ] `jobwatch-calendar.service` healthy; `events` count matches the pre-rollout count with no rows.
- [ ] Existing UIDs and SEQUENCE unchanged by rollout (compare manifest before and after).
- [ ] `run_report(kind="calendar_selection")` shows `publisher.state=available` and `last_success_at`.
- [ ] Output contains no feed URL, source note, application note or audit image.
- [ ] After the first approved batch: receipt, private audit, refresh hint, later `in_published_feed`.
- [ ] Eventually observed in the Proton subscription itself, with the same URL and no duplicates.

## Limitations

- Pending reviews are in memory. An uncertain commit needs a fresh read before retrying.
- The token binds the change. It does not prove a human approved; ChatGPT's confirmation does that.
- Company/title resolution and duplicate warnings are heuristics and never authorise a write.
- A batch evaluates the whole deadline set several times, within the 5-second statement limit.
  Production timing is unmeasured on the 2 GiB host.
