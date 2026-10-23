# Stage 3 implementation report

Completed 23 October 2026 on `feat/postgresql-import-reconciliation`, starting from exact Stage 2 commit `f7e3e6531a43d51fc310b87364d06aadd0dbb1a4`. Approved contract: review `2966967c3cf7431dc3b487a72d251e6034d209e9`, sections C/G/H. No architecture branch merge and no production cutover.

Frozen rehearsal source: **`90434ea6d9a3457f9c8ba40f01ad25fa2ceffca3`**. All source reads used Git objects, not working-directory state. Full history/object requirements and bounded recovery are enforced; no provider requests or hosted connections occurred.

## Delivered files

- `src/jobwatch/postgres/git_snapshot.py`: explicit full-SHA verification, read-only/full-history Git access, strict decoding, regular-file checks, bounded object traversal and verified historical text recovery. Shallow/promisor inputs are refused; replacement objects, lazy fetching and prompts are disabled.
- `src/jobwatch/postgres/import_plan.py`: deterministic mapping/recovery for every dataset, UUIDs, complete original payloads/provenance, source activation gates and explicit missing/unknown data. Reuses legacy validation/upgrade logic without rescreening or rerunning provider normalization.
- `src/jobwatch/postgres/importer.py`: empty-target/schema refusal, one atomic import, migration/table locks, deferred-FK validation, field/payload/reference reconciliation, audit-image checks, logical and schema/privilege snapshots.
- `src/jobwatch/postgres/import_cli.py`, `pyproject.toml`: explicit `jobwatch-pg-import plan/import/reconcile`; separate migration credentials, safe diagnostics, private reports and nonzero discrepancy/refusal exits.
- `tests/postgres/import_fixture.py`, `tests/postgres/test_importer.py`, `tests/test_postgres_import.py`: realistic synthetic Git/owner fixtures, actual importer integration and offline recovery/failure tests.
- `tests/postgres/rehearsal.py`, `scripts/test_postgres.sh`: verified-container two-import/pg_dump/pg_restore rehearsal, including schema/ownership/grants/sequences and SQL queries/permission denials. The existing launcher supports an explicit rehearsal flag.
- `docs/POSTGRESQL_IMPORT.md`: reproducible developer/operator procedure, source/evidence/transaction semantics and later cutover/rollback gates. Existing development/collection guides link it.
- [Full row reconciliation](postgresql-import-results/frozen_90434ea_reconciliation.json) and [restore/repeatability result](postgresql-import-results/restore_rehearsal.json): actual safe machine-readable results. These contain hashes/statuses/identities, not source wording, private notes, raw evidence, credentials or database backups.

## Actual frozen-data results

| Dataset | Imported/reconciled |
|---|---:|
| Postings | **629**: 627 listed, 2 not_seen |
| Judgments | 572 excluded, 49 review, 8 priority; codes/order/rules/hash/basis/time preserved |
| First-seen bases | 571 index_backfill, 54 legacy_retained, 4 observed |
| Employers / ATS sources | **44 / 9**, including employers without a board |
| Vacancy leads / programme cycles | **21 / 13**; prior-cycle years remain prior cycles |
| Verification observations | **24 across 11 postings**: status/description/deadline checks |
| Real applications / triage | **0 / 0**; application CSV exists with header, triage file is absent |
| Immutable versions | **1,008**: 951 legacy snapshots and 57 genuine native evidence/representation versions |
| Existing index events | **107**: 4 source, 54 screening, 49 mixed legacy |
| Imported health snapshots | **69**, preserving complete original health/scan JSON and chronology |
| Import audit records | **87**, matching employer/source/lead inserts and their complete images |

Every one of the 1,924 non-audit rows matched all expected fields/payloads/relationships; all 87 audit images/attributions matched. There were **zero unexplained reconciliation failures**. The second fresh import and the restored database independently produced identical row comparison manifests and logical contents, including generated FTS. Research/workflow queries each return 629 rows; the same English FTS query returns 498 matches in all three databases.

The frozen history contains seven schema-1 index versions with authentic native records and two schema-2 versions; fourteen relevant commits also capture owner/config/text changes. Recovery retained **537 distinct exact texts**: 535 currently referenced distinct descriptions covering all 629 current postings, plus two historical-only variants. The frozen tree contains 536 text files. All 2,169 encountered description-reference occurrences resolve; **no recorded description hash is irrecoverable**. All historical file/inline content is verified before insertion, with commit/path/pointer provenance. Native history covers 54 postings; 57 immutable native tuples retain the original parsed records and their old representations. Original explicit per-row native observation times are preserved before the legacy decoder removes listed-row last_seen.

The nonempty synthetic fixture independently imports/restores three applications and one triage record with notes, CV labels and follow-up dates, plus leads/programmes, status/deadline/description verifications and old native evidence. It proves explicit keys, exact URL fallback, refusal of fragment-only linking, later manual-key linking and unchanged owner records during subsequent Stage 2 publication.

## Expected transformations and honest limitations

No SQL migration was added. `0001_initial.sql` SHA-256 remains `ccbaab4351d3d6d128a2a9f637dcf77ab0f8459b8ebe6066403a597356e6acd5`; `0002_collection_receipts.sql` remains `71bc9c2c5c42a66d68c0fbf75409d7cd358cafb587fdcd95196e7c540b058e5a`.

- Legacy events use the existing `import/legacy_event` carrier with the original known source/screening/mixed category, exact original JSON, occurrence identity and provenance. This preserves meaning without asserting modern provider attribution or inventing full before/after version bindings. Hash references resolve to retained legacy evidence maps. Scan-summary events remain in original imported health JSON rather than being duplicated as newly inferred material transitions.
- Historical/current normalized snapshots are honestly labelled `legacy_index`; recovered raw provider objects are separately labelled `git_history` within the **same version mechanism**. No old raw record replaces newer current state. The tested first modern collection creates a native evidence **baseline**, not a fabricated employer edit. Normalizer/screening behavior is unchanged.
- Known per-posting positive times are retained/recovered conservatively: 571 first observations, 54 historical row sightings, two explicit current last-seen values and two source-event sightings. No later board success becomes a synthetic posting sighting. Observation/reference/import clocks remain distinct from employer dates.
- UTC timestamp representation, blank application dates→NULL, deterministic surrogate identities, `open_2027`→open+2027 and explicitly quoted vacancy intake projection are documented lossless transformations. Full unprojected fields, old control files and root metadata remain in labelled private legacy archives. NUL is preserved in evidence and replaced only in derived text, with a flag.
- **Irrecoverable legacy limits:** current schema-2 native responses were never archived (`90434ea…:data/jobs.json`, all 629 normalized current rows). Older authentic native evidence exists for 54 postings but cannot establish their current native wording. Rejected roles never indexed before schema 2 have unknown identities/counts and cannot be reconstructed. Neither limitation is hidden as a failed text reference or invented data. Both remain explicit pre-cutover acceptance items.

All nine legacy-enabled sources lack a separately recorded `endpoint_checked_at`: `ashby:hyperexponential`, `ashby:incident`, `ashby:tracebit`, `greenhouse:cambridgeconsultantslimited`, `greenhouse:monzo`, `greenhouse:mw-tech-grad`, `greenhouse:riverlane`, `lever:palantir`, `lever:scottlogic`. Their prior enabled configuration/mapping evidence is retained; candidate status and `activation_required` make the gate explicit. No mapping/careers/health timestamp was relabelled an endpoint verification. Real checks are required before PostgreSQL monitoring activation.

## Tests actually executed

Environment: Docker **29.4.1**, PostgreSQL **17.11** (`Debian 17.11-1.pgdg12+2`), Python **3.13.12**, psycopg/psycopg-binary **3.2.13**, libpq **17.6** (`170006`). Both PostgreSQL backup utilities are version **17.11**. Pinned image:

`postgres@sha256:858bb5e98eed02eb8cafbcdc007f9a65d1b613972028944a8ec2133ccaf80d27`

Commands from the implementation worktree:

```bash
python3 -m venv /tmp/jobwatch-import-venv
/tmp/jobwatch-import-venv/bin/pip install -e '.[postgres]' 'psycopg[binary]==3.2.13'
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHON=/tmp/jobwatch-import-venv/bin/python bash scripts/test_postgres.sh
PYTHON=/tmp/jobwatch-import-venv/bin/python bash scripts/test_postgres.sh --rehearse-import \
  . 90434ea6d9a3457f9c8ba40f01ad25fa2ceffca3 /tmp/jobwatch-import-rehearsal-90434ea-verified
/tmp/jobwatch-import-venv/bin/python -m unittest discover -s tests/postgres -v
/tmp/jobwatch-import-venv/bin/python -m pip wheel --no-deps . --wheel-dir /tmp/jobwatch-import-wheels
/tmp/jobwatch-import-venv/bin/python -m pip check
bash -n scripts/test_postgres.sh
python3 -m compileall -q src/jobwatch/postgres tests/postgres tests/test_postgres_import.py
git diff --check
```

**188 offline tests passed** (177 unchanged + 11 new; final 4.630 seconds), with psycopg absent from the offline interpreter. **77 PostgreSQL tests passed** (63 unchanged + 14 new; final 18.256 seconds excluding container startup and the additional real-data rehearsal). Marker-free integration discovery deliberately skipped all 77 without connecting.

Tests cover four-revision text recovery, corrupt hash rejection even with an older good object, unavailable evidence/event retention, genuine native identity validation, mixed history, judgment/input preservation, source collisions/activation/health, cycle identity/year, nonempty private owner data, embedded verifications, manual/conservative links, populated/extra-table refusal, late SQL failure and complete audit/domain rollback, retry into the still-empty target, field tampering/privacy-safe discrepancies, CLI exit behavior, real independent imports/restoration, and subsequent actual Stage 2 publishing. Existing Stage 1/2 constraints, audit, role, evidence, transaction/concurrency and normalization tests continue passing unchanged.

Wheel, dependency, syntax and whitespace checks passed. All packaged modules and both migrations were compared byte-for-byte with source. A separate wheel-only venv without psycopg successfully executed installed `jobwatch-pg-import plan` from `/tmp`, with deliberately invalid runtime/migration URLs; its plan hash equals the rehearsed plan. No DB connection was attempted.

## Backup/restore proof and cutover boundary

The real custom-format `pg_dump` archive was **7,886,136 bytes**, SHA-256 `0a8cfe608053a8ba60de977803a500626dc2aac3a7e321e1bb146a772782e994`. `pg_restore --exit-on-error --single-transaction` restored into a fresh third database without preapplying migrations. Roles were provisioned first; import/dump/restore used `jobwatch_migrator` with superuser/CREATEDB/CREATEROLE all false. The restored migration runner is a no-op. Schema, ownership, grants, functions, triggers, constraints, generated FTS and exact sequence state match; runtime queries/denials pass. PostgreSQL's canonical pretty deparser handles redundant OR parentheses changed by dump/reparse without dropping constraints from comparison.

The complete derived-data logical digest is `24d9204464ad74213cd588fe3b4441bac3133204774b70aa3cf160796922fb61`; schema digest `a76cc58a5fe165e4ca2e40e2cfa2c28a74167654ddeb6d341ec72876eac484db`. Dumps are not expected to be byte-identical across imports because activity timestamps/transaction IDs differ. Raw archives and test databases/containers/volumes were removed. This proves a fresh database restore in a provisioned local PostgreSQL cluster, not Neon PITR or a separate hosted cluster recovery.

No known unresolved Stage 3 implementation defects. Production cutover remains blocked on nine genuine endpoint checks, explicit acceptance of the two historical limits, hosted role/TLS/backup provisioning, required production SQL read/report behavior, and a final freeze/import/reconciliation/restore rehearsal at the then-current frozen commit. After authoritative DB writes begin, rollback requires preserving/reconciling later DB changes; restarting the frozen file writer is insufficient.

Production runtime modules, tracked data/config/reports, the scheduled workflow, `main`, existing migration bytes and all existing tests remain unchanged. Other worktrees are clean. No hosted resources/credentials, live ATS scan, source expansion, dual writes, normalizer change, vector capability or merge was introduced. Hosted deployment and cutover were not started.
