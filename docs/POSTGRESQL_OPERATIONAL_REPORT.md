# Stage 4A implementation report — 24 October 2026

Implemented on `feat/postgresql-operational-readiness`, starting from exact Stage 3 commit `783520b27a3e0fd47eb10ffaa1cce46678c98861`. PostgreSQL remains an explicitly invoked development target. There was no production switch, live ATS request or hosted database connection.

## Delivered functionality

The [functionality matrix](POSTGRESQL_FUNCTIONALITY.md) inventories the file-era commands and identifies direct SQL, useful conveniences, transactional/network operations and persistence machinery to retire at cutover. The [operational guide](POSTGRESQL_OPERATIONS.md) gives working invocations and SQL equivalents; [operational_queries.sql](../sql/postgresql/operational_queries.sql) is an inspectable cookbook, not a migration.

- Broad research includes excluded postings. The private attention view reuses the existing workflow join and application-over-triage precedence. Exact ambiguous URLs and possible loose matches remain warnings, never inferred links.
- Daily/weekly reports, independently attributed deadlines, applications, programme cycles, checked requirement mentions and private calendar export read SQL directly. They do not reconstruct the old file-backed state. Reports distinguish source facts, collector observations, judgments, independent checks and owner actions, and omit notes/CV labels. Calendar export reuses the existing date/escaping/rendering logic, refuses conflicting or timezone-uncertain dates and never parses prose into dates.
- Unchanged Python BM25 now reads SQL rows; indexed PostgreSQL FTS is a separate labelled ranking option. All-verdict listed-current research is the default. Historical evidence and not-seen inclusion are explicit. C++, C# and C tokenisation differences are tested, not treated as equivalent. Matched source text is marked untrusted.
- Multi-source scheduling enumerates monitored sources in a bounded deterministic order and calls the real Stage 2 collector. Each source commits independently. JSON and public Actions summaries distinguish published/failed/skipped/superseded/unknown, absence-safe and positive-only results. No monitored sources is a non-success, never a successful empty scan.
- Endpoint checks register before fetching, reuse the actual fail-closed provider clients, and retain dated validation attempts without publishing postings or activating sources. Activation separately requires employer mapping and the latest successful same-source, same-identity real check; fixture checks cannot activate. Equivalent ordinary owner SQL is supplied. Actor/audit and source-first locking remain in force.
- Readiness checks migration bytes, PG version, roles/column grants/private boundaries, immutable evidence, views, FTS, observation consistency, activation, import reconciliation and restore receipts. Baseline and post-activation operational phases are distinct. It always leaves hosting/authorization gates explicit and never claims deployment readiness from local tests.

The proposed [cutover runbook](POSTGRESQL_CUTOVER_RUNBOOK.md) covers final-current-commit selection, freeze, empty-target import/reconciliation, actual-host restore, activation, a single authoritative writer, controlled first scan and recovery before/after authoritative DB writes. The workflow proposal is inert at `docs/templates/postgres-scan.yml.disabled`, outside `.github/workflows`.

## Files and justified schema adaptations

New implementation modules: `postgres/research.py`, `reports.py`, `orchestrator.py`, `endpoints.py`, `readiness.py`, `operations.py`. `pyproject.toml` adds the optional `jobwatch-pg-ops` entry point. Normal reads and permitted owner writes remain ordinary SQL; no ORM, service, filtering language or backend abstraction was added.

`0003_operational_views.sql` extends the public health/research views and adds **one private operational view**. No new table, trigger, role or index is needed. In particular:

1. Coverage exposes genuine per-posting positive observations separately from source success, positive-only retention and uncertain imported absence. This prevents a successful paginated scan from appearing to refresh an unseen posting.
2. The original research view's `p.*` had been expanded when migration 0001 ran, so migration 0002's `last_seen_run_id` was absent from that view. Migration 0003 exposes it explicitly, without changing either existing migration.
3. The private view composes the existing workflow join rather than reimplementing owner precedence in every report. The collector cannot read it.

`collection.fetch_records()` extracts existing validation/coverage code so endpoint checks do not run the screener or store observations. Normalisation, screening, request contracts, Stage 2 publication and absence inference are unchanged. `importer.py` adds a privacy-safe imported-audit-image digest to reconciliation and column ACLs to restore schema comparison. This lets operational readiness verify immutable imported history after legitimate mutable writes and detects column-level privilege drift. Older schema-2 restore receipts do not prove schema-3 readiness; obtain a current rehearsal.

Existing schema tests assert the new view and all pending migrations. New coverage lives in `tests/postgres/test_operations.py` and `tests/test_postgres_operations.py`; the existing Stage 3 rehearsal now exercises actual operational reports, calendar, both searches and readiness on both independent imports and the restored database. Existing development guides link these additions.

## Tests actually executed

All commands were run from this implementation worktree. Python environments are disposable `/tmp` venvs with this checkout installed editable. No arbitrary caller-provided test DSN is accepted: the existing launcher verifies its owned, token-labelled, loopback-only Docker container.

| Python | Offline unittest suite | Complete PostgreSQL suite |
|---|---:|---:|
| 3.11.16 (supported baseline) | 195 passed, 4.830 s | 94 passed, 27.385 s |
| 3.12.14 (Actions baseline) | 195 passed, 4.360 s | 94 passed, 27.347 s |
| 3.13.12 | 195 passed, 2.691 s | 94 passed, 27.719 s, plus full frozen-data rehearsal |

The offline total is 188 existing + 7 new tests; PostgreSQL is 77 existing + 17 new tests. Every prior test continues passing. PostgreSQL **17.11**, psycopg **3.2.13**, binary libpq **170006**; Docker **29.4.1**. Pinned official image:

```text
postgres@sha256:858bb5e98eed02eb8cafbcdc007f9a65d1b613972028944a8ec2133ccaf80d27
```

Exact commands:

```bash
PYTHONPATH=src /tmp/jobwatch-ops-py311/bin/python -m unittest discover -s tests -v
PYTHONPATH=src /tmp/jobwatch-ops-py312/bin/python -m unittest discover -s tests -v
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHON=/tmp/jobwatch-ops-py311/bin/python bash scripts/test_postgres.sh
PYTHON=/tmp/jobwatch-ops-py312/bin/python bash scripts/test_postgres.sh
PYTHON=/tmp/jobwatch-ops-venv/bin/python bash scripts/test_postgres.sh --rehearse-import . \
  90434ea6d9a3457f9c8ba40f01ad25fa2ceffca3 /tmp/jobwatch-ops-rehearsal-final
```

Tests use actual operations on imported fixture data and mocked provider responses. They cover all-verdict reads, selective queues, owner precedence/conflicts/URL ambiguity, modern and legacy history, current/historical evidence, exact-current-description verification, read-only screening parity, date uncertainty and private-output safety, both retrieval methods, partial failures and valid empty boards, positive-only Lever retention, skipped/superseded/unknown outcomes, endpoint/mapping separation, actor attribution, identity races, source activation and privilege/refused-write checks. Stage 1–3 constraints, evidence, concurrency, import, owner fixtures and real backup/restore tests all remain included.

Additional checks passed: `git diff --check`, `python3 -m compileall -q src/jobwatch/postgres tests/postgres tests/test_postgres_operations.py`, `bash -n scripts/test_postgres.sh`, `pip check`, and a built-wheel verification. Build command: `/tmp/jobwatch-ops-venv/bin/python -m pip wheel --no-deps --wheel-dir /tmp/jobwatch-ops-wheel .`. Every packaged Python module and all three migration files match source byte-for-byte. A separate wheel-only venv without psycopg successfully imports the operations modules, loads all migration resources and runs installed `jobwatch-pg-ops --help` from `/tmp`, without connecting to a database. The first no-build-isolation build attempt lacked setuptools in its venv; the normal isolated build succeeded.

## Frozen production-snapshot rehearsal

Source: **`90434ea6d9a3457f9c8ba40f01ad25fa2ceffca3`**, read through Git objects only. This is a rehearsal snapshot, not the future final cutover commit.

| Dataset | Reconciled count |
|---|---:|
| Employers / ATS sources | 44 / 9 |
| Postings | 629 (627 listed, 2 not_seen) |
| Screening | 8 priority, 49 review, 572 excluded |
| Immutable versions / retained legacy events | 1008 / 107 |
| Imported health runs | 69 |
| Research leads | 34 (21 vacancies, 13 programme cycles) |
| Verification observations | 24 |
| Real applications / triage | 0 / 0; nonempty synthetic fixtures are tested |

All 629 current descriptions are retained. Recovery finds 537 distinct texts, including 2 historical-only, and 57 genuine historical native versions across 54 postings. **Zero recorded evidence references are missing.** Two independent empty-database imports reconcile exactly and have the same logical digest. Real `pg_dump`/`pg_restore` produces a separately reconciled third database with matching schema, ownership, column/table grants, sequences, migration ledger, evidence/history, runtime reads and denials.

The custom-format archive was 7,892,348 bytes, SHA-256 `dd02cc9eb59a7e0ae244d2d652073d4c910bc4dc9ed3441cf85729d7432514e2`. Local PG utilities were both 17.11. Raw archives/databases/containers were removed by the harness; this is local restore proof, not hosted backup/PITR proof.

The safe durable [machine-readable results](postgresql-operational-results.json) contain counts, digests, exact utility arguments, source gates and checks, without descriptions, notes, CV labels or credentials. Logical digest remains `24d9204464ad74213cd588fe3b4441bac3133204774b70aa3cf160796922fb61`; schema-3 digest is `7e69d4ecb3530b8e4eb647e8244dcf3b151c370c5fe8b441b15e45dd2c369e1a`. On each of the three databases operational smoke checks return 629 corpus rows, 57 attention rows, 107 legacy material events, 67 dated/undated deadline claims, 34 leads, 11 independently checked requirement samples and 2 calendar events with no warnings; both search methods execute successfully. These counts are observations of this frozen baseline, not current recruitment claims.

## Remaining external gates and deliberate omissions

All nine legacy-enabled sources remain candidates pending genuine endpoint evidence:

```text
ashby:hyperexponential
ashby:incident
ashby:tracebit
greenhouse:cambridgeconsultantslimited
greenhouse:monzo
greenhouse:mw-tech-grad
greenhouse:riverlane
lever:palantir
lever:scottlogic
```

No live requests were authorized or made. A scheduled scan of this imported baseline therefore selects zero monitored sources and returns exit 1 explicitly. Existing employer mapping timestamps are preserved, never reclassified as endpoint checks. Activation tests use injected transports only and do not activate real sources. Lever's multi-page collection remains positive-only. Sources outside the existing coverage are not added.

The owner must accept the two unchanged historical limits: current schema-2 native responses were not archived, and pre-schema-2 rejected/unindexed roles cannot be reconstructed. Hosted provisioning, TLS credentials, actual-host restore/backup policy, production secrets, final snapshot/freeze and the single-writer switch require separately authorized Stage 4B work. Readiness correctly reports these gates and `deployment_ready=false`.

No known unresolved Stage 4A implementation defects. Old file-specific validators, locking/durability, writers and Git observation commits are classified for retirement but deliberately **not deleted or switched** in this stage. No mandatory CRUD compatibility layer, generic audit/event system, vectors, normalizer v2 or source expansion was introduced.

Tracked production data/config/reports, the existing file-backed runtime, active scheduled workflow, `main`, and migration 0001/0002 bytes are unchanged. No hosted resources, cloud credentials, dual writes, live collection, deployment, merge or cutover occurred. Stage 4B has not started.
