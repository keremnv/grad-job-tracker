# Stage 1 implementation report

**Completed:** 23 October 2026. **Scope:** local database foundation; file-backed production remains authoritative.

Branch: `feat/postgresql-foundation`, based directly on main `90434ea6d9a3457f9c8ba40f01ad25fa2ceffca3`.
Implementation commit: `855c903a8c06ff609bf85cb0c3a7121b060c2e5c`.
Approved contract: review commit `2966967c3cf7431dc3b487a72d251e6034d209e9`; no design/review branch merge.

## Delivered

- `src/jobwatch/postgres/migrations/0001_initial.sql`: executable PostgreSQL 17 application schema, eleven domain tables, three views, generated evidence hashes/FTS, composite/deferred FKs, immutable evidence grants and narrow owner audit.
- `sql/postgresql/bootstrap.sql`: separate one-time administrative role/database setup. Two runtime logins plus a dedicated non-superuser migration login; no passwords or automatic provisioning.
- `src/jobwatch/postgres/connection.py`: optional psycopg 3 connections, separate runtime/migration environment variables, explicit timeouts/READ COMMITTED, context-manager cleanup and redacted connection errors.
- `src/jobwatch/postgres/migrate.py`: packaged ordered SQL, private checksum ledger, concurrent-invocation lock, atomic application, repeat invocation, refusal of changed/unknown/unmanaged history. Explicit module/console entry point only.
- `src/jobwatch/postgres/evidence.py`: deterministic parsed-record serialization preserving NUL; no normalization or material comparison.
- `scripts/test_postgres.sh`, `tests/postgres/`: isolated Docker tests of the actual migration, using reviewed source fixtures and small SQL setup helpers instead of the review's prototype publisher.
- `docs/POSTGRESQL_DEVELOPMENT.md`: dependency installation, local sandbox, bootstrap permissions, migration commands, psql usage, credentials/transactions and later-stage boundaries.
- `pyproject.toml`, `docs/DEVELOPMENT.md`: optional dependency/entry point/package-data configuration and a link to the new setup documentation. Existing package dependencies remain empty without the extra.

## Adaptations from the reviewed SQL

1. Role creation, database/public-schema grants and role search paths moved to administrative bootstrap. Ordinary migrations executed successfully as `jobwatch_migrator`, with superuser, CREATEDB and CREATEROLE all false. A provider can supply an equivalent deployment login; none is assumed provisioned.
2. The runner creates the application namespace and a private technical `jobwatch.schema_migrations` table before applying numbered SQL. This additional ledger is not a domain table and is inaccessible to both runtime logins. The initial migration uses SET LOCAL, so its search path does not escape its transaction.
3. URL identity now removes bare `?`/`#`, accepts Python's empty/default/zero-padded HTTPS port forms, and rejects malformed IPv6 literals. These fix demonstrated differences in duplicate/link identity, with parity tests against unchanged `jobwatch.identity` functions. No existing adapter/screening behavior changes.

All other domain DDL/grants follow the approved corrected schema. The defective archived scratch schema and optional embedding migration were not used.

## Tests actually executed

Environment: Docker Engine 29.4.1, Linux amd64; PostgreSQL **17.11** (`Debian 17.11-1.pgdg12+2`), Python **3.13.12**, psycopg/psycopg-binary **3.2.13**, libpq **17.6** (`170006`).
Pinned official image: `postgres@sha256:858bb5e98eed02eb8cafbcdc007f9a65d1b613972028944a8ec2133ccaf80d27` (17.11-bookworm). No extensions were installed; the test verifies `vector` is absent.

Commands run from the implementation worktree:

```bash
python3 -m venv /tmp/jobwatch-foundation-venv
/tmp/jobwatch-foundation-venv/bin/pip install -e '.[postgres]' 'psycopg[binary]==3.2.13'
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHON=/tmp/jobwatch-foundation-venv/bin/python bash scripts/test_postgres.sh
/tmp/jobwatch-foundation-venv/bin/python -m unittest discover -s tests/postgres -v
/tmp/jobwatch-foundation-venv/bin/python -m pip wheel --no-deps . --wheel-dir /tmp/jobwatch-foundation-wheels
/tmp/jobwatch-foundation-venv/bin/python -m pip check
bash -n scripts/test_postgres.sh
python3 -m compileall -q src/jobwatch/postgres tests/postgres
git diff --check
```

| Verification | Result |
|---|---|
| Existing offline unittest suite | **170 passed**, including a run with psycopg absent. Also passed with the optional dependency installed. Mocked scan/report output used temporary files, not production paths/endpoints. |
| Disposable PostgreSQL integration suite | **32 passed** (final run: 1.779 seconds excluding container startup). Covers empty/repeat/concurrent migration, checksums/history, failed initialization/upgrade rollback, identities/FKs, immutable/NUL evidence and distinct normalizers, material references/retention, runtime grants, actor/login audit and rollback, applications, source health, views/FTS and source-row lock behavior. |
| Integration discovery without launcher markers | **32 skipped**, as intended; no database connection. |
| Imports without psycopg, with an intentionally invalid migration URL | Successful and silent; imports do not connect or migrate. |
| Wheel packaging | Built successfully; packaged migration bytes exactly equal the source file. SQL SHA-256: `ccbaab4351d3d6d128a2a9f637dcf77ab0f8459b8ebe6066403a597356e6acd5`. Optional psycopg requirement is marked `extra == "postgres"`. |
| Fixture integrity | Source-pair JSON is byte-identical to the approved review fixture. Actual incident.io variants and Cambridge Consultants Job Summary payloads round-trip through bytea intact. No new material-comparison implementation is claimed. |
| Dependency, syntax and whitespace checks | Passed. |
| Production-path diff | Empty for `data/`, `config/`, `reports/`, `.github/`, all pre-existing runtime modules and all pre-existing tests. |

During initial validation, the Docker readiness probe briefly saw the image's temporary socket-only initialization server. The launcher now waits for TCP readiness. One test initially expected a source FK error while its inconsistent posting key correctly failed a CHECK first; the test now isolates the FK failure. Both corrections preceded the passing final run. No failed check is counted as a pass.

All test containers/volumes were removed. No hosted database was contacted/provisioned, no production credentials configured, and no live data import or real provider scan executed. Main and the scheduled workflow remain unchanged; the implementation is committed only on its feature branch.

## Outstanding work

No known unresolved Stage 1 implementation defects. Tests establish the foundation, not production collector correctness. Missing capabilities are intentional: importer/Git evidence recovery, collector publication/retries/rescreen orchestration, source discovery, hosting/backups/restore rehearsal, normalizer v2, database-backed BM25, pgvector and hybrid retrieval.

Next stage: implement source-locked collection/evidence/material-history persistence with the **current normalizer output**, using the approved run-order/absence-safety contract and provider comparison regression cases. Importer/reconciliation and cutover follow separately. Stage 2 was not started.
