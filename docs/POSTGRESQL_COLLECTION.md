# PostgreSQL collection and history (Stage 2)

This is a working **development/test collection path**. The scheduled JSON-backed tracker, its commands, data, rules and GitHub Actions workflow are unchanged. There is no dual write, live-state importer, hosted database, cutover or vector job. Setup and role provisioning remain in [POSTGRESQL_DEVELOPMENT.md](POSTGRESQL_DEVELOPMENT.md).

## Run locally with a fixture

Install `.[postgres]`, create the disposable `jobwatch-dev` database described in the setup guide, bootstrap roles, then apply both packaged migrations:

```bash
.venv/bin/python -m jobwatch.postgres.migrate
```

On that **local sandbox only**, seed a fixture source with owner SQL:

```bash
docker exec -i jobwatch-dev psql -X -U jobwatch_owner -d jobwatch_dev <<'SQL'
BEGIN;
SET LOCAL jobwatch.actor='owner:local-fixture';
INSERT INTO jobwatch.employers(slug,name) VALUES ('monzo','Monzo');
INSERT INTO jobwatch.sources(provider,board,employer_id,status,validation_url,
  endpoint_checked_at,mapping_verified_at,mapping_verified_by,mapping_evidence)
SELECT 'greenhouse','monzo',id,'monitored','https://monzo.com/careers',
  now(),now(),'local-fixture','Local fixture configuration, not a live validation'
FROM jobwatch.employers WHERE slug='monzo' RETURNING id;
COMMIT;
SQL
```

Use the returned source ID, not an assumed employer/board ID:

```bash
export JOBWATCH_DATABASE_URL='host=127.0.0.1 hostaddr=127.0.0.1 port=55432 dbname=jobwatch_dev user=jobwatch_collector sslmode=disable'
.venv/bin/python -m jobwatch.postgres.scan --actor collector:local \
  collect 1 --fixture tests/fixtures/greenhouse.json
.venv/bin/python -m jobwatch.postgres.scan --actor operator:local rescreen --all
```

The installed equivalent is `jobwatch-pg`. The fixture option performs no employer requests; Lever list fixtures are sliced into the adapter's normal pages. Without `--fixture`, this explicit command uses the existing bounded public-feed adapters and really fetches the configured source. It never reads or writes `data/`, performs migrations, or changes the existing `jobwatch.cli scan` default.

## Python boundary and recovery

```python
import json
from pathlib import Path
from jobwatch.postgres.publisher import Collector
from jobwatch.postgres.collection import fetch

collector = Collector(actor='collector:development')  # JOBWATCH_DATABASE_URL, collector login
registration = collector.register(source_id=1)       # committed before fetching
response = json.loads(Path('tests/fixtures/greenhouse.json').read_text())
observation = fetch(registration, collector.processing, get_json=lambda url: response)
status = collector.publish(observation)
assert collector.publish(observation) == status      # terminal replay, no duplicate history
```

`Collector.collect_source(id, get_json=..., pause=...)` combines those operations and returns `(run_uuid,status)`. It always allocates a new UUID for a new fetch. The separate operations are useful for deterministic tests and recovery. `Run`, `Record` and `Observation` are frozen values; records contain immutable serialized evidence/output/judgment bytes. Modifying a response dictionary after fetching cannot change the validated publication payload.

Registration obtains `run_order` and `started_at` from PostgreSQL. Its metadata freezes the source identity/endpoint/company/association and processing/request identifiers. To recover an uncertain registration acknowledgment, call `register` with the **same UUID and metadata**; an incompatible UUID reuse is rejected. Network collection starts only after registration commits and closes its connection.

Call `fetch` once per attempt. Never refetch an existing UUID: a genuine new fetch needs a new registration. A worker interrupted after fetching must retain its `Observation` to retry publication; if it loses that payload, start a new collection rather than inventing a receipt/order for the old one. A still-running abandoned attempt is visible in SQL; this stage does not guess worker death from its age or implement a durable payload queue.

The original receipt is captured when the last response returns, before record preparation. Counts account for every returned vacancy or intentionally skipped record. The existing adapters perform their usual identity, URL, shape, duplicate, transport and count validation. All records are validated/prepared before publication; one malformed record invalidates the board, never a subset publication.

`publish` retries the whole transaction for deadlocks, serialization errors, lock/connection interruptions, using the **same observation, UUID and receipt**. The default is three attempts, with short bounded backoff; `Collector(..., attempts=1..5)` controls the bound, while Stage 1 connection/statement/lock timeouts apply. It never fetches from inside retry logic.

`CollectionError` exposes `run_id`, `outcome` and a safe SQLSTATE where available. Messages/health do not copy provider exception text, SQL DETAIL, credentials or payload snippets. When retries fail, a separate source-locked health transaction either confirms an already committed terminal result or marks the running attempt failed. If connectivity prevents confirmation/finalization, outcome is `unknown`; retain and retry the same observation when connectivity returns. A known terminal failed attempt is not republished; a new fetch gets a new run. Lost COMMIT acknowledgements are resolved by reading the run and its payload digest, not by assuming rollback.

## Exact transaction and absence contract

Publication uses READ COMMITTED and locks the source `FOR NO KEY UPDATE`, then its run `FOR UPDATE`. It checks the registered source, monitoring/association, pinned processing metadata, payload binding, terminal status and latest successful `run_order`. Source/run/evidence/history/screening changes commit together. All posting changes, including absence and retained-record reprocessing, follow stable posting-key order. No HTTP call or embedding inference occurs under these locks.

Only actual positive records advance `postings.last_seen_at` and `last_seen_run_id`. Successful older runs become `superseded` if a newer run already published; a newer failed run does not block an older valid one. Paused/remapped sources are skipped. Screening exclusions never remove a posting. Collection never accesses owner applications, triage, leads or verifications; grants remain the standard boundary.

| Provider contract | Absence inference |
|---|---|
| Greenhouse list with one response, valid jobs and explicit integer `meta.total` matching all source records | Safe; prospects are counted as intentional skips. Without a total, publish positives only. |
| Ashby single full job-board response with explicit `apiVersion='1'` and validated jobs | Safe under that API contract. A legacy response missing apiVersion remains readable by the existing adapter but is positive-only here. |
| Lever offset/limit=100 | Safe only when the first and only response is shorter than 100 (including a valid empty first response). Every multi-request run is positive-only, including 100 records followed by an empty page. |
| Explicit continuation markers (`hasMore`, nonempty next/nextPage/nextCursor), malformed/truncated/failed responses | Fail the collection and publish no subset. |

For positive-only runs, unreturned records keep their feed state and last positive time. For absence-safe runs, previously listed unreturned keys become `not_seen`; descriptions/versions/owner links remain. Reappearance uses the same key and history. `not_seen` never verifies closure. An empty validated complete board can establish absence; a failed board cannot.

These are provider response contracts, not proof of an atomic upstream snapshot or employer edit time. See [Greenhouse list jobs](https://docs.greenhouse.io/job-board.html#list-jobs), [Ashby public job board](https://developers.ashbyhq.com/docs/public-job-posting-api), and [Lever's offset pagination](https://github.com/lever/postings-api). Unknown future API shapes/contracts require deliberate code/fixture changes before certifying absence.

## Evidence, comparison and v1 parity

Complete parsed records use Stage 1 `encode_evidence`; PostgreSQL computes the stored bytea hash. This preserves strings, HTML, entities, newlines and NUL, not original HTTP/JSON syntax. Derived values alone replace NUL with U+FFFD and set `nul_replaced`; identity/URLs are never repaired. Invalid Unicode or replacement-key collisions fail closed. Screening uses the safe stored inputs so its fingerprint can be reproduced; NUL handling is the deliberate safety exception to ordinary v1 parity.

The default `Processing()` is `pipeline-v1`, `normalizer-v1` and the unchanged current screening rules. It uses the existing `Posting`, adapter output, `deadline_evidence` and `screen`; no HTML cleanup, list-title enrichment, new location/country interpretation, Lever salary normalization or fit scoring was introduced. Tests compare both prepared and DB-stored fields/judgments against the existing file-era functions for all three providers.

Normalized hashes exclude deadline **observation times**, which are provenance, while retaining their wording/value/URL/source. Unchanged deadline content carries its first retained observation time into a new version. Reused evidence/normalizer/content tuples keep their original immutable capture; posting receipts and events record later observations. Nothing overwrites old derived wording or its normalizer label.

`material.py` compares both full native records under the same current `material-v1` rules. Greenhouse descriptions use a comparison-only HTML renderer; metadata, offices/departments, title, URLs, location and deadline are material. Lever compares classifications/locations, body/opening/additional text, ordered labelled lists, workplace/country, URLs and native compensation. Ashby compares plain wording, primary/secondary locations/addresses, classifications, flags, URLs and compensation tiers/components. Set-like arrays ignore order; section/list order is retained. Plain-text code such as `<vector>` is not mistaken for HTML.

Formatting/provider timestamp changes retain native variants without attention events. Absent→present fields establish baselines, including null; null/empty/removal remain distinct. A removal across different request contracts is a coverage baseline, not an employer removal claim. Unknown native fields remain preserved without automatically becoming material. A substantive change plus newly collected evidence produces one provider event with separate `changed`/`baseline` details. The actual incident.io style oscillation emits no material event; Cambridge Consultants emits a metadata change, not a description edit.

The one `posting_events` stream records observation/backfill, provider change/baseline, normalizer and screening transitions. Events reference retained same-posting versions. Large or NUL-bearing change values are represented by hashes rather than copying them into JSONB; the full wording remains in evidence. Event observation times/intervals are ours, not fabricated employer edit timestamps.

Screening always refreshes evaluation metadata when reevaluated, including unchanged verdicts. History records changed verdict/family/codes with old/new rules, input hashes and source/normalizer/rules cause flags. Rule/input metadata changes without changed judgment do not create attention history.

Legacy-only snapshots are not interpreted as native provider JSON. They can be screened from retained normalized fields, keep their normalizer label, and establish a native evidence baseline on first collection. No importer or Git-history recovery is included here.

## Processing revisions and rescreening

`rescreen_source(id)` locks that source before reading any posting input. `rescreen_all()` enumerates monitored sources and commits **one source at a time**, using the same lock order as publication. It includes retained/not_seen/excluded postings without refreshing their positive observation times.

A release pins processing, normalizer, comparison and rules identifiers. Bump the processing token when deploying changed code/rules; version labels are code provenance, not an automatic binary registry. An old worker cannot overwrite a source after its token advances. Ordinary daily scans never change the token.

Use `rescreen_source(id, advance_from='pipeline-v1')` on a collector pinned to the new processing release to explicitly compare-and-set the source. It reprocesses old native evidence when the normalizer changes, writes immutable derivations/events/judgments and advances the token in the same transaction. A mismatched expected/current token returns `superseded`; retrying an already completed advancement is safe.

An explicit registration `register(id, advance_from='pipeline-v1')` also permits advancement **inside publication**. This supports simultaneous new code/new source information: derive the previous native record using the new normalizer first, record that representation transition, then compare old/new native evidence and record the provider transition. Retained/unreturned rows are also reprocessed before advancing the source token. The intermediate representation is referenced by the same history stream. A failed transaction rolls all of this back.

The CLI's `--processing-revision`/`--advance-from` expose this explicit deployment operation. Changing a flag does not implement new parser/rules code. The shipped normalizer remains v1; tests use clearly labelled synthetic callbacks to establish future revision behavior without shipping those improvements now.

## Inspect history and test

```sql
SELECT * FROM jobwatch.source_health;
SELECT posting_key, verdict, status, last_seen_at FROM jobwatch.research_postings;
SELECT id, category, kind, observed_at, before_version_id, after_version_id, changes
FROM jobwatch.posting_events WHERE posting_key='greenhouse:monzo:8143930' ORDER BY id;
SELECT id, normalizer_version, description_text, evidence_sha256 FROM jobwatch.posting_versions
WHERE posting_key='greenhouse:monzo:8143930';
```

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHON=.venv/bin/python bash scripts/test_postgres.sh
```

The launcher still creates/verifies/removes its own pinned local PostgreSQL container; tests accept no production DSN. Both migrations are tested fresh and as a Stage 1 upgrade. Next work is the one-time importer, historical reconciliation and restore rehearsal before any hosted deployment/cutover. Deliberate normalizer improvements and embeddings remain separate later changes.
