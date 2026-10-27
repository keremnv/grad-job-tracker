# Subscription-ready calendars (Stage 4E)

Implemented: a local PostgreSQL-to-ICS snapshot producer and synthetic integration
tests. The [last-mile integration](AGENT_CALENDAR_INTEGRATION_REPORT.md) now prepares
hourly/owner-triggered publication and revocable HTTPS feeds locally.
**Not deployed:** real HTTPS feeds, publication scheduling or a live Proton subscription.
PostgreSQL remains authoritative; ICS is a disposable
projection plus small transport revision metadata, never another owner ledger.

## Local use and manual fallback

Install the `postgres` extra and use a read-capable, owner-approved SQL connection
as described in [PostgreSQL development](POSTGRESQL_DEVELOPMENT.md). The
[user guide](USER_GUIDE.md) describes the existing authenticated owner SQL connection for private manual use. Test changes on disposable data; external
HTTPS publication/Proton subscription still requires separate approval.

```sh
# New PRIVATE output directory only; explicit initialization is required once.
jobwatch-pg-ops calendar snapshot --output-dir .local-reports/calendar-feed --initialize
# Subsequent refreshes reuse UID/revision metadata; no network or database writes.
jobwatch-pg-ops calendar snapshot --output-dir .local-reports/calendar-feed
jobwatch-pg-ops calendar status --output-dir .local-reports/calendar-feed --max-age-hours 2
```

`--feeds deadlines actions` is the default; either feed can be selected alone.
Disabled feeds become valid empty calendars so an eventual subscriber can remove
old events. `combined.ics` contains exactly the enabled events, generated once by
the shared projection. Subscribe to combined **or** the separate calendars, not
both. Separate calendars allow different Proton calendar-wide notification defaults.

`current/` atomically points at a complete generation containing `deadlines.ics`,
`actions.ics`, `combined.ics`, `manifest.json` and `manifest.sha256`. Retain the
whole private publication directory, including the pointer and metadata, for
recovery. For a manual fallback import/export the **current published ICS** to
retain its exact UIDs/revisions. Repeated manual imports may duplicate events in
some clients; do not combine imports with the same subscribed calendar.

The old `jobwatch-pg-ops report calendar` remains a private stateless export and
the file-backed exporter is unchanged. Those compatibility exports can include
free-form action/wording text and cannot preserve a subscriber's previous
lead-to-posting UID mapping. Use the new minimal snapshots for external calendars.

## Projection and privacy contract

| Feed | Included | Omitted / meaning |
|---|---|---|
| Deadlines | Explicit independently verified dates, structured provider application-deadline fields, checked official vacancy/programme dates | Unknown/prose-inferred dates, ambiguous identities, conflicting explicit dates, rolling contradictions, unknown timezones, unverified leads and previous/closed/interest-only programme cycles |
| Actions | Dated pending application actions; dated `later` triage revisits without an associated application | Blank/undated/completed actions; rejected/withdrawn applications; revisits suppressed by application precedence |

Programme cycle year is a label, not a rule to move a deadline into that year.
An open 2027 intake can have an explicitly observed 2026 application deadline.
No unknown date or old cycle is promoted to a new opportunity. A date marker is
not proof the form is open, nor eligibility/closure evidence. No screening verdict
filter is applied to owner actions or recorded date evidence.

Default snapshots contain company/title, a generic action/deadline label, date,
safe source URL and fixed uncertainty/provenance categories. **No notes, CV labels,
free-form next-action text, source quotations, descriptions, contact details or
observer identifiers.** Company/title/URL and the existence of a follow-up are
still private information: minimisation is not anonymisation. Untrusted source
text is escaped/folded as RFC 5545 data; URLs bearing recognised credentials are
omitted. Unsafe text fails publication instead of injecting calendar properties.
`CLASS:PRIVATE` is client metadata, **not access control**.

The snapshot reads a single repeatable-read, read-only database snapshot. Bounds:
10,000 records per input table, 5,000 projected events, 20,000 retained UID metadata
entries, 900,000 bytes per ICS and 32 MiB manifest. The explicit rolling window
defaults to 30 past/366 future days in Europe/London (`--past-days`, `--future-days`,
maximum 3660 each). Outside-window entries are deliberately omitted; limits fail
before replacing anything, never silently truncate. Increase/narrow scope only
after inspecting warnings. Proton documents a 1 MB subscription limit.

## Identity, revisions and atomic publication

Existing hash-based `jobwatch-calendar-v1` IDs remain the base scheme: posting key
for linked vacancies, exact URL for an unlinked vacancy lead, lead/cycle key for
programmes, manual application key for follow-ups, posting key for revisits.
This fixes the former PostgreSQL unlinked-lead-key mismatch with legacy export.

Publication metadata stores hashed strong aliases, content fingerprints and
UID/sequence/timestamps, **no event bodies or owner history**. Explicit or
unambiguous exact-URL linkage lets a lead keep its previously published UID; when
two published claims join, one old UID survives and the other is removed. Splits
retain the anchored component's UID; related programmes with different cycles
stay distinct. No fuzzy title or loose-URL association is introduced. Manual
application keys survive ATS linkage and keep their action UID.

Metadata must survive refresh/recovery. If state is missing/corrupt, publication
fails closed; restore the private directory rather than silently issue new client
identities. A genuinely new subscription can be explicitly initialised in an empty
directory. Links never observed by the publisher cannot reconstruct lost past
identities; do not delete/rename domain keys and expect inference to repair them.
Back up publication metadata separately from the PostgreSQL dump when hosting is
approved. Recovering an **older** manifest can rewind client sequence numbers;
do not blindly resume it as current. Recover the latest transport state or arrange
an explicit new subscription/client reset. This stage tests a complete directory
copy/restore, not lossless recovery from an unavailable newer manifest.

An unchanged event retains UID, `DTSTAMP`, `LAST-MODIFIED` and `SEQUENCE`, even when
the SQL/report clock or evidence-check time advances. Significant projected content
changes increment sequence from zero and update both UTC timestamps. Removed or
completed events are omitted from the full calendar; small inactive UID metadata
allows stable reappearance with a later sequence. This is a subscription snapshot,
not emailed scheduling/iTIP invitations; no `METHOD:CANCEL`, attendee or appointment
is invented. Actual client deletion behaviour remains a live acceptance test.

Each feed has a SHA-256/size/count and stable last-modified time. A refresh changes
manifest `generated_at` for freshness without changing identical feed bytes/mtime.
Status validates manifest and all file checksums; fresh defaults to at most two
hours old (configurable 1–168 hours). This is publisher freshness, **not** source
freshness or proof Proton fetched it. Snapshot/status return 0 for clean/fresh,
1 for published omissions/stale status, 2 for execution/configuration failure.

Private directories/files use 0700/0600. An OS advisory lock serialises publishers;
symlink/hardlink/unsafe-path checks protect artifacts. Files and directories are
fsynced, then one atomic relative pointer replaces the generation. Current and
previous generation are retained; failed pre-commit writes preserve the old feed.
An uncertain filesystem acknowledgement requires reading the current checksums
before retrying. Publication is not evidence of live subscription success.

## Making it practical in Proton — later, with approval

Proton needs a stable HTTPS ICS URL reachable by its servers; localhost or an
SSM-only tunnel is not an online subscription. Separately approve a minimal
authenticated/revocable hosting arrangement, private data scope and credential
custody. Do not expose PostgreSQL, the S3 backup bucket, manifest or directory
listing. Serve only the selected fixed ICS files, `text/calendar; charset=utf-8`,
using the recorded hash as ETag and stable feed mtime as Last-Modified. Resolve the
current generation once per request; preserve conditional GET behaviour. Keep
capability URLs out of logs and give each feed an independently revocable token if
that is the chosen compatible authentication scheme. An expiring presigned backup
URL is not a durable calendar endpoint. No endpoint or credential is created here.

An approved scheduled refresh must call this producer after relevant owner writes
and periodically, monitor nonzero status and `generated_at`, and preserve the last
good generation on failure. No systemd timer or GitHub workflow is changed now.

Add external subscription(s) in Proton and test synthetic creation, rescheduling,
completion/removal, linkage, timezone/display and chosen notification defaults.
Proton documents **approximately 4–16 hours between refreshes**; polling does not
guarantee prompt updates or deadline alerts. Subscribed calendars support
calendar-wide default notifications, **not event-specific notifications**. A late
reschedule may arrive after a desired reminder. Use an explicitly reviewed direct
reminder for urgent actions; no message/reminder service is implemented here.
Manual import/export remains fallback, not the intended ongoing V1 experience.
The actual plan's calendar limits and the live connection must be checked with
the owner; no Proton plan or subscription was accessed during development.

Official references: [external subscriptions](https://proton.me/support/subscribe-to-external-calendar),
[notifications](https://proton.me/support/calendar-notifications-overview),
[calendar limits](https://proton.me/support/calendar),
[RFC 5545 revision properties](https://www.rfc-editor.org/rfc/rfc5545.html#section-3.8.7.2).
