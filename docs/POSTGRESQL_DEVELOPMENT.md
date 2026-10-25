# PostgreSQL development (Stages 1–4A)

PostgreSQL is a **development/test target** at this stage. The scheduled tracker still reads/writes its existing JSON/CSV/text files. Installing the optional dependency, importing the package or running the existing CLI does not connect to PostgreSQL or apply migrations. There is no synchronization between the two stores and no production cutover.

The implementation follows review commit [`2966967`](https://github.com/keremnv/grad-job-tracker/tree/2966967c3cf7431dc3b487a72d251e6034d209e9/reviews/postgresql-final). Use PostgreSQL 17; the initial tested baseline is 17.11. No extension is required: pgvector and embedding tables are deferred.

## Install

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[postgres]'
```

The base installation still has no runtime dependencies. The `postgres` extra installs psycopg 3 with its binary implementation; an ORM, pool and service are unnecessary. The working development collector, history and rescreen API/CLI are documented in [POSTGRESQL_COLLECTION.md](POSTGRESQL_COLLECTION.md).

The one-time frozen Git importer, reconciliation and disposable backup/restore rehearsal are documented in [POSTGRESQL_IMPORT.md](POSTGRESQL_IMPORT.md). They require explicit invocation and do not change the production store.

Operational SQL, reports, search, multi-source collection, endpoint checks and readiness are documented in [POSTGRESQL_OPERATIONS.md](POSTGRESQL_OPERATIONS.md). See the [functionality matrix](POSTGRESQL_FUNCTIONALITY.md) and the proposed, unexecuted [cutover runbook](POSTGRESQL_CUTOVER_RUNBOOK.md).

## Disposable integration tests

Docker must be running. From the repository root:

```bash
.venv/bin/python -m unittest discover -s tests -v
PYTHON=.venv/bin/python bash scripts/test_postgres.sh
```

The second command starts a fresh, uniquely named container using pinned official image `postgres@sha256:858bb5e98eed02eb8cafbcdc007f9a65d1b613972028944a8ec2133ccaf80d27` (17.11-bookworm), provisions roles, then tests the **actual packaged application migration**. It binds an ephemeral port exclusively to 127.0.0.1 and removes its container/volume on exit, including failures. It needs no hosted account, credentials or live `data/` files.

Integration tests derive their destination from a verified Docker container name, matching per-run token/labels and loopback binding. They do not consume an arbitrary database URL. Running `python -m unittest discover -s tests/postgres -v` without the launcher skips integration tests; setting a mismatched marker fails before a connection. The offline suite remains independent of Docker and psycopg. Actual incident.io HTML and Cambridge Consultants Job Summary pairs under `tests/postgres/fixtures/` exercise the production publisher and comparator.

## Start a persistent local development sandbox

This example uses a new disposable database with local trust authentication and no production directory mount:

```bash
docker run -d --name jobwatch-dev -p 127.0.0.1:55432:5432 \
  -e POSTGRES_DB=jobwatch_dev -e POSTGRES_HOST_AUTH_METHOD=trust \
  postgres@sha256:858bb5e98eed02eb8cafbcdc007f9a65d1b613972028944a8ec2133ccaf80d27
docker exec jobwatch-dev pg_isready -h 127.0.0.1 -U postgres
```

Wait for the final TCP server to become ready. The image's temporary initialization server listens only on its Unix socket. Trust authentication in this example is only for the disposable local database; a future hosted database uses separately provisioned authenticated TLS credentials.

### Provision roles once

```bash
docker exec -i jobwatch-dev psql -X -U postgres -d jobwatch_dev \
  -v ON_ERROR_STOP=1 -f - < sql/postgresql/bootstrap.sql
```

`bootstrap.sql` is administrative setup, **not an application migration**. It creates two runtime logins and a separate migration login, without passwords:

| Login | Purpose |
|---|---|
| `jobwatch_migrator` | Owns the application schema/objects, runs migrations; CREATE on this database, no superuser/CREATEDB/CREATEROLE. Never use it for ordinary agent/collector work. |
| `jobwatch_collector` | Reads public corpus data and writes collector-owned records; cannot read private owner records or mutate immutable versions. The PostgreSQL publisher uses it. |
| `jobwatch_owner` | Ordinary owner/agent SQL: corpus/history reads, owner workflow and research/control writes; no schema ownership or collector observation writes. |

Bootstrap requires role-administration privileges plus authority to grant database privileges, revoke PUBLIC CREATE in `public`, and set the newly created roles' database-local search paths. It operates on the connected database (`psql`'s quoted `DBNAME` variable). Roles are cluster-wide: existing names deliberately cause failure and rollback instead of silently overwriting their passwords/grants.

For managed PostgreSQL, create the equivalent non-superuser logins/privileges through the provider's administrative facilities if it does not allow this script. An existing dedicated deployment login can replace the example migration login: the runner does not require its name. It needs CREATE on the database initially, and ownership of the `jobwatch` schema/objects on subsequent runs. Runtime role names in the migration are fixed. Do not grant runtime logins membership in the migration/deployment role. Authentication/passwords are configured outside the repository (e.g. interactive `psql \password` or a provider secret manager).

No cluster role creation, ALTER ROLE or changes to `public` occur during ordinary schema migration. A managed provider need not give the migration login superuser or CREATEROLE. Use a dedicated application database: bootstrap intentionally changes its default schema-create privileges.

### Apply the migration explicitly

```bash
export JOBWATCH_MIGRATION_DATABASE_URL='host=127.0.0.1 hostaddr=127.0.0.1 port=55432 dbname=jobwatch_dev user=jobwatch_migrator sslmode=disable'
.venv/bin/python -m jobwatch.postgres.migrate
# Equivalent installed entry point:
.venv/bin/jobwatch-db-migrate
```

Plain SQL lives in `src/jobwatch/postgres/migrations/` and is included in built distributions. The runner creates `jobwatch` and its private technical `schema_migrations` ledger, then applies numbered SQL in order. `0001_initial.sql` is unchanged; `0002_collection_receipts.sql` adds a last-positive-run FK and validated-payload digest plus registration metadata privileges. `0003_operational_views.sql` extends public coverage/health and adds one private operational view, without changing tables or normalisation. The ledger is additional to the eleven domain tables; runtime roles cannot read or write it. Initial SQL assumes this runner-created namespace. Do not apply it directly to an unmanaged schema and expect the runner to adopt it.

Every invocation validates filenames and SHA-256 checksums against the complete applied prefix. Duplicate/gapped versions, renamed/changed applied migrations and unavailable recorded versions fail. Repeating an unchanged migration invocation is a no-op. Add a new numbered migration instead of editing an applied file. An existing `jobwatch` schema without a ledger is refused; unrelated database schemas are never reset or dropped.

Pending migrations and ledger changes execute in one transaction under a PostgreSQL transaction-scoped advisory lock. A SQL failure rolls back the invocation, including namespace creation on a first run. If the connection is lost during COMMIT, rerun the same unmodified migrations to verify the ledger; the client cannot assume rollback after a lost acknowledgement. Concurrent invocations serialize. Migration SQL must be transactional: no COMMIT/ROLLBACK or nontransactional commands such as CREATE INDEX CONCURRENTLY. The runner requires an idle autocommit connection and reports the failing filename/SQLSTATE without printing SQL text, driver DETAIL or connection strings. There is no automatic rollback/down-migration/reset command.

### Inspect with ordinary SQL

```bash
docker exec -it jobwatch-dev psql -X -U jobwatch_owner -d jobwatch_dev
```

```sql
\dt jobwatch.*
\dv jobwatch.*
\d+ jobwatch.posting_versions
SELECT * FROM jobwatch.source_health;
SELECT posting_key, title, verdict, status FROM jobwatch.research_postings;

BEGIN;
SET LOCAL jobwatch.actor = 'owner:local-dev';
INSERT INTO jobwatch.employers(slug,name) VALUES ('example','Example');
COMMIT;
```

`research_postings` includes excluded records; `opportunity_workflow` joins private workflow for owner connections. Normalized fields and FTS live on immutable versions, with `postings.current_version_id` selecting current data. FKs enforce same-posting versions and same-source runs. The current-version FK is deferred to transaction commit so the initial posting/version pair can be inserted together.

Native evidence uses `encode_evidence(record)` → deterministic ASCII JSON bytes stored in bytea. PostgreSQL generates the SHA-256. Decode with `json.loads(bytes(row['evidence']))`; NUL-bearing strings remain intact. These are parsed record values, not original HTTP-response bytes. The PostgreSQL collector safely replaces NUL in derived values and records `nul_replaced`, preserving native bytes. Existing file-backed adapters/screeners remain unchanged.

Owner/control writes require `SET LOCAL jobwatch.actor` inside a transaction. The narrow audit records both the authenticated database login and self-asserted actor label, with private before/after images. It covers employers, sources' mutable controls, leads, triage and applications. Collector pointer/processing-token updates do not duplicate domain history. Audit rows roll back with failed writes; audit is not an attempted-operation log or proof of owner authorization.

Remove only this sandbox when finished:

```bash
docker rm -fv jobwatch-dev
```

## Connection configuration

Use `JOBWATCH_DATABASE_URL` for runtime connections and **only** `JOBWATCH_MIGRATION_DATABASE_URL` for migrations. Migration credentials never fall back to runtime credentials. Both accept standard libpq keyword DSNs, PostgreSQL URIs or `service=...` entries. Future hosted settings should use authenticated TLS with certificate verification (`sslmode=verify-full`) and `.pgpass`/service files or approved secret management; no hosted endpoint or credential is configured in this stage.

```python
from jobwatch.postgres.connection import connect

with connect() as conn:                 # dict rows, READ COMMITTED, autocommit
    rows = conn.execute('SELECT * FROM jobwatch.source_health').fetchall()
    with conn.transaction():            # explicit atomic write boundary
        conn.execute("SELECT set_config('jobwatch.actor', %s, true)", ('agent:session-id',))
        # ordinary parameterized SQL for a permitted owner/control edit
```

For programmatic configuration use `DatabaseConfig(dsn, purpose='runtime')` and `connect(config)`. Connection contexts close on success/error; explicit transactions roll back on error. There is no pool or ORM. Configuration reprs and connection/CLI errors omit DSNs and credentials. Raw SQL exceptions in a caller's own code still require normal care when logging private row data.

| Environment variable | Default |
|---|---:|
| `JOBWATCH_DB_CONNECT_TIMEOUT` (seconds) | 10 |
| `JOBWATCH_DB_STATEMENT_TIMEOUT_MS` | 30000 |
| `JOBWATCH_DB_LOCK_TIMEOUT_MS` | 5000 |
| `JOBWATCH_DB_IDLE_TRANSACTION_TIMEOUT_MS` | 60000 |

Values are positive integers. The module explicitly sets these session defaults, the application search path and READ COMMITTED; DSN `options` do not override them. Raise the statement timeout explicitly for a later long migration. The same data remains accessible through psql or direct psycopg without using this convenience module.

## Adaptations and remaining stages

Schema semantics follow the approved review. Provisioning is separated; the runner owns namespace/journal setup; a dedicated migration login replaces review-time superuser execution. SQL URL identity now matches existing Python behavior for empty query/fragment delimiters, empty/default/zero-padded HTTPS ports, and validates IPv6 literals rather than accepting malformed bracketed addresses. These refinements have regression tests and change no existing Python normalizer or deployed data.

Stage 2 implements collection, publication, provider comparison, evidence/history, retries and source-locked rescreening with current v1 output. Stage 3 adds frozen Git import, historical recovery, full reconciliation and tested local pg_dump/pg_restore. Stage 4A adds SQL-backed research/reports, BM25 and FTS, multi-source collection, endpoint validation/activation, and explicit readiness checks. Hosted infrastructure, actual-host backups/PITR, real endpoint checks, the single authoritative production cutover, source discovery expansion, normalizer v2, pgvector and hybrid retrieval remain later work. Production remains file-backed.

## Optional Stage 4C read tools

The [read-tool contract](CHATGPT_READ_TOOLS.md) provides a transport-independent
SQL/schema/evidence/search/report core, JSON-lines harness and optional official
MCP SDK adapter. Install `.[chat-mcp]` only when using MCP; ordinary psycopg/psql
and `.[postgres]` remain independent. Proposed role provisioning is separate from
migrations and has run only against disposable databases. The existing production
CLI, AWS roles, workflows and authoritative storage remain unchanged.
