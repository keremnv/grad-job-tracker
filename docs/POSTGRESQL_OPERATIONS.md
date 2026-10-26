# PostgreSQL operational tools (Stage 4A)

PostgreSQL is the authoritative production store after the 26 October cutover.
Use [the handover](AWS_PRODUCTION_HANDOVER.md) for authenticated SSM/owner-peer
access and schedules. The file-backed CLI is retained for frozen-source rehearsal,
not live writes; its GitHub workflow is retired. Importing modules never connects,
migrates, activates a source or changes a backend automatically.

Use PostgreSQL 17 and `pip install -e '.[postgres]'`; setup/roles are in [POSTGRESQL_DEVELOPMENT.md](POSTGRESQL_DEVELOPMENT.md). Apply packaged migrations explicitly. `0003_operational_views.sql` appends coverage/health columns without changing existing columns, fixes the missing `last_seen_run_id` exposure caused by PostgreSQL expanding `SELECT *` before migration 0002, and adds one private convenience view. Initial migrations remain byte-identical. No new domain table, trigger, runtime role or index is introduced.

See the complete [functionality matrix](POSTGRESQL_FUNCTIONALITY.md), [ordinary SQL cookbook](../sql/postgresql/operational_queries.sql) and [executed AWS cutover and recovery runbook](AWS_CUTOVER.md).

## SQL remains the primary read/write interface

- `research_postings`: **all retained postings and all verdicts**, including exclusions and `not_seen`. Current source-derived wording comes from the immutable current version. Query `posting_versions` for previous wording/native evidence.
- `source_health`: actual latest publish attempt, last successful publication or explicitly imported baseline, coverage, activation/processing metadata. Validation runs are separate from publication. The report helper additionally labels original imported attempts when no modern attempt exists; an old failure/success is not a newly performed collection.
- `opportunity_workflow`: existing application/triage precedence and explicit/unambiguous exact URL matching.
- `operational_postings`: owner-only research plus that workflow, conflict/possible-match flags and the most recent independently checked application status **with its check time**. No notes/CV labels. This is a shared join, not another current authority.

`coverage_state` distinguishes `seen_in_latest_published_run`, `retained_after_positive_only_run`, `not_seen_absence_safe`, `legacy_not_seen_unproven`, `legacy_snapshot`, `observation_unknown` and `inconsistent_presence`. A recent source success does not refresh an unseen posting. An absence-safe scan can confirm an already-absent legacy record without manufacturing a second transition event. Positive-only scans never invent absence. `not_seen` never verifies closure.

Owner attention is a WHERE clause over `operational_postings`: listed, priority/review, and unreviewed/revisit_due/interested/preparing. Applications outrank triage; dismissed + preparing/applied is a visible conflict. Exact-URL ambiguity prevents fallback association; loose/fragment-different URLs only produce a possible-match flag. All affected records remain in research. Ordering is owner state, stored verdict, descending first observation, company/title/key; no second screener, prestige or fit ranking.

Direct permitted SQL writes still use `SET LOCAL jobwatch.actor` for owner/control audit. Applications keep their PK when deliberately linked later. Independent claims require real checked-at/source wording/observer; unknown status alone is not a check. No mandatory CRUD API, MCP server or compatibility envelope is needed.

## Useful report/search transformations

Set `JOBWATCH_DATABASE_URL` to a **local owner login** for private reports, or the collector login for public research/search. No credentials are printed. Examples assume the existing local sandbox, not a hosted production connection:

```bash
jobwatch-pg-ops report daily
jobwatch-pg-ops report weekly
jobwatch-pg-ops report requirements
jobwatch-pg-ops report weekly --json
jobwatch-pg-ops report daily --since 2026-10-23T00:00:00Z --limit 100

# Private derived output, never a writable database or tracked observation file:
umask 077
mkdir -p .local-reports
jobwatch-pg-ops report calendar > .local-reports/postgres-calendar.ics

jobwatch-pg-ops search 'C++ distributed systems' --method bm25
jobwatch-pg-ops search 'software' --method fts
jobwatch-pg-ops search 'Python' --include-not-seen
jobwatch-pg-ops search 'old wording' --history
```

Daily/weekly reports read one repeatable-read, read-only SQL snapshot. They include corpus/attention, health/coverage/failures, material history, first observations excluding backfill, independently checked versus feed-only dates, owner follow-ups, application stages and leads/cycles. Weekly also attributes requirement mentions to independently checked exact current descriptions. An old hash inside a legacy evidence map does not verify newer current wording. All listed verdicts can contribute to that sample. Mentions are not applicant requirements or hiring-market evidence. Programme windows/uncertainty and previous-cycle years remain explicit. Reports do not show private notes or CV labels; owner actions make these reports private and unsuitable for automatic Actions summaries.

Missing dates remain unknown. Deadlines include provenance/actual check time, state and uncertainty. Calendar exports only explicit admissible values, omits conflicting dates/ambiguous identities, rejects timezone-free/`-00:00` timestamps, and never parses prose into a date. All-day markers do not assert midnight expiry. It reuses the existing RFC escaping, stable UIDs and UTF-8 folding. Nonzero calendar exit 1 means warnings/omissions; valid RFC output may still have been produced.

Search defaults to **current listed records, every screening verdict, descriptions included**. `--include-not-seen` adds current absent records. `--history` explicitly searches all retained versions (including current and absent), with version ID, capture/reference time, origin, hash and `is_current`. Posting verdict/status/last-positive/health are current context, not historical judgments of that version. Ranking is labelled `python_bm25` or `postgresql_fts_ts_rank_cd`. The unchanged Python BM25/tokenizer distinguishes C++, C# and C; English PostgreSQL FTS folds these to C and has different weights/stemming. Neither ranking is claimed equivalent. Snippets are explicitly untrusted data; JSON output escapes control characters, and Markdown reports escape source wording. Unsafe credential-bearing URLs are omitted at presentation boundaries; full evidence remains in SQL.

Reports bound lists explicitly (default 50, maximum 1000). Search bounds results (default 20, maximum 500). These limits do not cap storage or ordinary SQL queries. There is no custom filter language. `research.evidence(conn,key,version_id)` optionally decodes one exact retained evidence object, with trust labels. `research.screening_preview(conn)` calls the existing screener over current SQL rows without writing. Actual source-locked application remains `jobwatch-pg rescreen`, with its explicit processing-revision contract.

## Scheduled-style collection

Use collector credentials only:

```bash
jobwatch-pg-ops scan --actor collector:local --format json
jobwatch-pg-ops scan --actor collector:local --fixture /tmp/mock-feeds.json
```

The fixture input can be one provider response or `{"sources":{"greenhouse:board":{...},"lever:board":[...],"ashby:board":{...}}}`. Lever pages are sliced by the real adapter's offsets. `fixture_error:true` deliberately simulates a failed source. This invokes the actual Stage 2 publisher; no separate reference implementation exists.

The orchestrator snapshots all monitored sources in stable ID order, then collects them sequentially. Registration commits before each fetch, network access holds no DB locks, each validated publication commits independently and retries retain the original payload/receipt. One provider failure cannot roll back another source. The existing source→run→posting lock order, run ordering, idempotency and processing guards remain in Stage 2. Publication and validation do not access private owner records.

The roster has a bound (default 1000; explicit `--max-sources` up to 10000). Exceeding it fails before fetching; it never silently collects a subset. Summaries include source/run IDs, terminal/unknown outcome, validated counts, `absence_safe` versus positive-only and safe error classifications. Skipped/superseded attempts are not successes. A failed DB outcome is not guessed committed. GitHub summaries use `orchestrator.actions_summary(result)` on the **already produced JSON**, not a second scan.

| Exit | Meaning |
|---|---|
| 0 | Every selected source has a confirmed published result. Positive-only publications are valid and explicitly counted. |
| 1 | Partial publication failure, skip/supersession, or no monitored sources. Previously committed source work remains valid. |
| 2 | All selected sources failed, an outcome is unconfirmed, or command/configuration failed. Earlier source commits may exist; this does **not** mean nothing was written. |

All imported nine sources are candidates until real checks/activation. An empty roster returns 1 with `no_monitored_sources`, never a fake successful scan. For publishing recovery use Stage 2 run/payload rules; no durable queue or cross-source transaction was added.

The proposed [workflow template](templates/postgres-scan.yml.disabled) is inert outside `.github/workflows`. It uses Python 3.12, collector-only credentials, contents-read permission and a public source summary; no owner credential, Git commits, file writes, importer or automatic migrations. It must replace the old writer only during authorized cutover. The active workflow is unchanged.

## Endpoint checks and activation

An endpoint check is not collection, employer verification or activation. Inspect without requests/writes:

```bash
jobwatch-pg-ops source inspect SOURCE_ID
# Local/test-only response: records a clearly tagged fixture validation run.
jobwatch-pg-ops source check SOURCE_ID --actor operator:fixture --fixture /tmp/response.json
```

For a **future authorized real check**, omit `--fixture` with collector credentials. The tool registers `mode='validate'`, commits, uses the existing safe provider clients/completeness validation, captures the receipt time, then finalizes that validation run. It does not normalize/screen/store postings, refresh presence, change the publication pointer or set owner-controlled endpoint/mapping facts. Empty valid feeds can validate; malformed/partial responses fail. Fixture runs are never eligible as real activation evidence.

With owner credentials after a real successful check:

```bash
jobwatch-pg-ops source activate SOURCE_ID --check-run RUN_UUID --actor operator:activation
```

The source-first transaction checks the latest same-source validation, live provenance, provider/board/region identity, actual receipt, no endpoint timestamp downgrade, and separately dated employer association evidence/URL/observer. It then copies the **real receipt time** into `endpoint_checked_at` and sets monitored. It does not replace the mapping timestamp with that check time. Owner needs no run-UPDATE permission; source locking already serializes validation finalizers. The equivalent ordinary SQL is in the cookbook. A real independently observed endpoint fact may also be recorded through permitted audited SQL; the tool is a convenience, not an agent permission service. Claims/actors remain self-assertions subject to normal authorization, not cryptographic proof of a real HTTP visit.

A retry of a completed validation UUID reads its terminal result. Registration/finalization use bounded DB retries; new requests get new UUIDs. After an interrupted/uncertain running check inspect that run before starting a new UUID; do not refetch a completed attempt to invent a newer receipt. An unconfirmed operation does not authorize activation. If an endpoint identity changes while fetching, validation is superseded. Mapping can remain incomplete even with a valid endpoint.

**No live checks were performed in Stage 4A.** All nine still need them: `ashby:hyperexponential`, `ashby:incident`, `ashby:tracebit`, `greenhouse:cambridgeconsultantslimited`, `greenhouse:monzo`, `greenhouse:mw-tech-grad`, `greenhouse:riverlane`, `lever:palantir`, `lever:scottlogic`.

## Readiness diagnostics

Use separate migration-owner credentials for the private checksum ledger and integrity checks:

```bash
jobwatch-pg-ops readiness --phase baseline \
  --reconciliation /tmp/rehearsal/import-a.json --restore /tmp/rehearsal/rehearsal.json
# After deliberate validation/activation/collection/owner writes:
jobwatch-pg-ops readiness --phase operational \
  --reconciliation /tmp/rehearsal/import-a.json --restore /tmp/rehearsal/rehearsal.json
```

Baseline mode requires current full logical equivalence to the source-bound import receipt and a matching schema/data restore proof. Operational mode permits current mutable data to evolve, but verifies every imported immutable version/event/run/verification row against Stage 3 row hashes, retains original posting identities, and checks original import audit images. Stage 3 reconciliation now also emits their hash; old proof files lacking it must be regenerated. No replay/reverse-sync engine is involved. Ordinary current-source/owner writes are not falsely described as an unchanged import.

Checks include PostgreSQL 17, full applied migration checksums, runtime flags/membership/grants, immutable/private boundaries, all reporting views covering the corpus, FTS, presence anomalies, intended source activation, unresolved endpoint checks, source health and restore evidence bound to this frozen source/schema. Old Stage 3 two-migration restore proof is not accepted for the new schema. Current stale/failed/positive-only health is shown explicitly; freshness is not guessed from importer success.

Exit 0 means **local structural/evidence checks passed**; 1 means unresolved checks; 2 means diagnostic execution/configuration failed. `deployment_ready` remains false: actual hosted TLS/roles/restore, owner acceptance, secrets, freeze and single-writer promotion cannot be proved by local diagnostics. They remain explicit external gates for Stage 4B, not flags an agent can silently approve. See the runbook.

## Optional read-tool transport

[Stage 4C](CHATGPT_READ_TOOLS.md) exposes the same research/report functions and
composable SQL through five read-only tools. This adds no write workflow or
mandatory API. Local JSON-lines, MCP stdio and loopback Streamable HTTP share one
core and a dedicated proposed read login; hosted provisioning/online connection
remain separate approvals.

## Owner SQL and subscription snapshots (Stage 4E)

[Owner workflows](POSTGRESQL_OWNER_WORKFLOWS.md) document/test audited ordinary
SQL for applications, explicit links, triage, evidenced leads and pending actions,
including stale-state guards and uncertain-commit read-back. There are no new
write tools/grants; the ChatGPT transport remains read-only.

[Calendar publication](PROTON_CALENDAR.md) adds private, minimal, atomic
`jobwatch-pg-ops calendar snapshot` / `calendar status` conveniences. They reuse
the existing date/ICS rendering and SQL workflow precedence. Dated triage revisits
and checked current programme deadlines are included; unlinked lead IDs now match
legacy exact-URL IDs, and retained publication metadata preserves IDs on linkage.
Split deadline/action feeds and a combined fallback share one projection.
Publication writes only the specified local private directory, never SQL or a
server. No live Proton feed, public endpoint or schedule is activated.
