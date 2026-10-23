# Stage 2 implementation report

Completed 23 October 2026 on `feat/postgresql-collection-history`, based on the exact Stage 1 commit `57336f4c97725775612699dd6f2c9386012c0632`. Contract: approved review `2966967c3cf7431dc3b487a72d251e6034d209e9`, especially section E. No review branch merge.

The PostgreSQL collector is executable against a local database. **Production remains file-backed.** No ongoing synchronization, hosted resources, production credentials, importer or cutover was introduced.

## Delivered files and behavior

| File | Responsibility |
|---|---|
| `src/jobwatch/postgres/collection.py` | Frozen registration/observation values, existing-adapter collection, full parsed evidence, unchanged v1 representation/screening, validation and provider completeness certification. |
| `src/jobwatch/postgres/material.py` | Complete initial provider material-field policies, comparison-only HTML rendering, set/ordered collections, absence/null/empty distinctions, coverage baselines and JSONB-safe change details. |
| `src/jobwatch/postgres/publisher.py` | Registration, source/run locking, atomic publication, evidence tuple reuse, one material stream, receipt/order/replay guards, bounded transaction retry/outcome reconciliation and source-locked rescreening. |
| `src/jobwatch/postgres/migrations/0002_collection_receipts.sql` | Last-positive-run FK, payload digest and narrowly extended collector grants. |
| `src/jobwatch/postgres/scan.py`, `pyproject.toml` | Explicit `jobwatch-pg` development invocation, fixture collection and rescreening. Existing CLI defaults/dependencies are unchanged. |
| `tests/postgres/test_publisher.py` | Tests the actual implementation with real runtime logins, mocked providers and disposable PostgreSQL. |
| `tests/postgres/test_migrations.py` | Existing migration guarantees retained across two migrations, plus populated Stage 1 upgrade coverage. |
| `tests/test_postgres_material.py` | Offline comparison regressions and v1 adapter/state/screening parity. |
| `docs/POSTGRESQL_COLLECTION.md`, `docs/POSTGRESQL_DEVELOPMENT.md`, `docs/DEVELOPMENT.md` | Setup, explicit invocation, SQL access, recovery, completeness/transaction contracts, processing deployment and scope. |

Run registration commits before any fetch and obtains PostgreSQL's order/time. Publication uses READ COMMITTED, source `FOR NO KEY UPDATE`, run `FOR UPDATE`, then stable posting-key order. The entire corpus/evidence/history/judgment/source-pointer update commits together. Failed collection never publishes a subset. Failed publication rolls back the corpus transaction; a separate short transaction records or confirms the attempt outcome.

Every new fetch gets a new UUID. Retries retain the same validated immutable payload and original receipt; they never refetch. Replay validates the payload digest. Lost acknowledgments are tested both at registration and after real publication COMMIT, including all bounded publication acknowledgments being lost. If outcome cannot be confirmed because connectivity remains unavailable, the error says `unknown`; the retained payload can subsequently resume the running attempt.

All observed postings are retained, including exclusions. Only actual positive observations update last-seen data. Complete Greenhouse feeds require an explicit matching total; Ashby requires its explicit version-1 full-list contract. Every multi-request Lever scan is positive-only, even a full page followed by an empty page. Missing completeness metadata produces positive-only observations; explicit continuation/truncation or invalid records fail closed. A validated complete empty board can establish `not_seen`; a failed or positive-only board cannot. These are response contracts, not claims of upstream atomic snapshots or verified closure.

The same current comparison rules interpret both native records. Formatting and timestamp variants remain retained without false material events. First native evidence for a legacy-only row is labelled a baseline, without inventing old provider data. Normalizer changes derive the old native input first; simultaneous source changes then form a separate provider transition in the same stream/transaction. Rules/input evaluation metadata refreshes even without a changed judgment. Owner data is neither read nor modified by collection; existing narrow audit/grants remain in force.

## Justified adaptations

1. **New migration, unchanged `0001`.** A version's `first_run_id` cannot identify the latest request contract when a tuple is reused (A→B→A). `postings.last_seen_run_id` supplies that provenance, with a same-source composite FK. `collection_runs.payload_sha256` binds terminal replay to the exact validated result and receipt. Collector gains UPDATE only on these columns and INSERT on registration `details`; it cannot rewrite those details. No new tables, trigger, view, framework or extension.
2. **Normalized content hash excludes deadline `observed_at`.** This is observation provenance, not changing source content. Unchanged deadline evidence carries its first retained observation time; identical daily input reuses its immutable tuple. Wording/value/source/URL remain hashed. This avoids manufacturing a daily version solely from the receipt clock.
3. **NUL safety is derived-only.** Native evidence uses unchanged Stage 1 encoding. Searchable text/JSONB replaces NUL with U+FFFD and records replacement; screening fingerprints the stored safe values. IDs/URLs are never repaired. Ordinary output/rules are unchanged, verified against the original adapter/state/screener for all providers. No planned normalizer-v2 improvements were implemented.
4. **Explicit processing advancement can accompany publication.** `advance_from` is a source-locked compare-and-set deployment operation, also available through rescreening. It reprocesses retained rows and atomically advances the source token, enabling the approved combined normalizer/source transition. Ordinary daily scans never advance it; old workers cannot overwrite the newer results. No general release registry or additional state machine.

## Tests actually executed

Environment: Docker Engine **29.4.1**, Linux amd64; PostgreSQL **17.11** (`Debian 17.11-1.pgdg12+2`), Python **3.13.12**, psycopg/psycopg-binary **3.2.13**, libpq **17.6** (`170006`). Pinned image:

`postgres@sha256:858bb5e98eed02eb8cafbcdc007f9a65d1b613972028944a8ec2133ccaf80d27`

Commands, from this branch's worktree:

```bash
python3 -m venv /tmp/jobwatch-collection-venv
/tmp/jobwatch-collection-venv/bin/pip install -e '.[postgres]' 'psycopg[binary]==3.2.13'
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHON=/tmp/jobwatch-collection-venv/bin/python bash scripts/test_postgres.sh
/tmp/jobwatch-collection-venv/bin/python -m unittest discover -s tests/postgres -v
/tmp/jobwatch-collection-venv/bin/python -m pip wheel --no-deps . --wheel-dir /tmp/jobwatch-collection-wheels
/tmp/jobwatch-collection-venv/bin/python -m pip check
bash -n scripts/test_postgres.sh
python3 -m compileall -q src/jobwatch/postgres tests/postgres tests/test_postgres_material.py
git diff --check
```

| Verification | Executed result |
|---|---|
| Offline suite, interpreter without psycopg | **177 passed** (170 unchanged existing tests + 7 new tests), final run 1.284 seconds. |
| Disposable database suite | **63 passed**, final run 6.028 seconds excluding startup; 32 Stage 1 tests retained, one upgrade test and 30 publisher tests. |
| Integration discovery without launcher markers | **63 skipped**, deliberately, with no database connection. |
| Packaging/dependency/syntax/whitespace | Passed. Wheel includes both migration files and all new modules, byte-identical to source, plus the explicit console entry point. |
| Approved fixture integrity | `source_pairs.json` is byte-identical to the review fixture; SHA-256 `a15afc485f495875e522a0b749f1a725e95ce8d6b377a10d5b3583369153f534`. |
| Production-path checks | No changes to `data/`, `config/`, `reports/`, `.github/`, existing runtime modules, existing offline tests or `0001_initial.sql`. Other worktrees remain clean. |

Database coverage includes excluded/new/unchanged observations; three consecutive material changes; actual incident.io/Cambridge Consultants pairs; NUL/native retention; version immutability and normalizer reuse; request-contract/baseline handling; rules/input metadata refresh; complete empty/failed/partial boards; positive-only Lever; absence/reappearance; actual concurrent publishers and rescreening blocked on the source lock; ordered/replayed runs; processing downgrade prevention; transaction/rescreen rollback; bounded database outages and recovery; owner/audit attribution/privacy/rollback; new FK/grants; FTS/views; populated Stage 1 upgrade; and the fixture CLI using the actual publisher. Deadlock/serialization exceptions and lost network acknowledgments are injected at production-operation boundaries; source lock contention and transactional writes/rollbacks occur in real PostgreSQL. Tests do not claim a production network-fault or deployment rehearsal.

The launcher's own containers/volumes are removed on exit. No employer feed requests, live database connections or live data imports were made. Existing main, the scheduled workflow and file persistence were not modified; only this feature branch is published.

## Remaining work

No known unresolved Stage 2 defects. A worker that loses both its in-memory observation and connectivity cannot resume that fetched payload; it must begin a new UUID/fetch after recovery. Running attempts remain visible rather than being guessed dead. This is deliberate bounded-retry behavior, not a durable work queue.

The next stage should implement the **one-time file/Git importer, historical evidence reconciliation and cutover/restore validation**. Hosted provisioning, backup operations, production credentials/scheduling/cutover, source discovery expansion, deliberate normalizer improvements, BM25 adaptation, pgvector and hybrid search remain later authorized work. None was started here.
