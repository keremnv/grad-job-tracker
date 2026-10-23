# Frozen Git import and restore rehearsal (Stage 3)

This is an explicit **one-time migration facility**. Production still uses the file-backed tracker. Importing does not fetch ATS feeds, apply schema migrations, rescreen, enable unevidenced sources, write production files, start a schedule or synchronize the stores. The importer can be archived after the eventual cutover.

## Prepare and inspect an import plan

Install `.[postgres]` and use the local PostgreSQL 17 setup in [POSTGRESQL_DEVELOPMENT.md](POSTGRESQL_DEVELOPMENT.md). Apply the packaged migrations separately with migration-owner credentials. `0001_initial.sql` and `0002_collection_receipts.sql` remain unchanged; Stage 3 needs no new migration.

The source must be a local repository with complete Git objects and history: shallow and partial/promisor repositories are refused, replacement objects are ignored, lazy fetch and prompting are disabled. The full commit SHA is mandatory. Tracked files are read through Git objects; dirty/untracked working files are ignored and preserved. No checkout, reset, worktree extraction or provider request occurs.

```bash
.venv/bin/python -m jobwatch.postgres.import_cli plan \
  --repo /path/to/grad-job-tracker \
  --commit 90434ea6d9a3457f9c8ba40f01ad25fa2ceffca3 \
  --report /tmp/jobwatch-plan-90434ea.json
```

The installed equivalent is `jobwatch-pg-import`. The JSON report must be a **new file outside the source checkout**; it is created with mode 0600 and never overwritten. The CLI also prints a readable commit/count/gate summary. A plan requires no database or psycopg installation.

Default bounds are 10,000 relevant ancestor commits, 256 MiB of unique input blob content, 32 MiB per blob, and 100,000 accumulated tree entries/unique cached objects. Exceeding a bound fails rather than truncating history. The CLI permits explicit `--max-history-commits`, `--max-git-bytes` and `--max-git-objects` adjustments. A single-blob bound is fixed. Full-history traversal includes relevant merge ancestry and text changes, not unrelated branches.

## Import and reconcile

Use a newly initialized **local sandbox**. Set `JOBWATCH_MIGRATION_DATABASE_URL` for the schema-owning deployment login as described in the setup guide; runtime owner/collector logins cannot import. No superuser, CREATEDB or CREATEROLE is needed by the importer. Source reading and mapping finish before target writes begin.

```bash
.venv/bin/python -m jobwatch.postgres.import_cli import \
  --repo /path/to/grad-job-tracker \
  --commit 90434ea6d9a3457f9c8ba40f01ad25fa2ceffca3 \
  --report /tmp/jobwatch-import-90434ea.json

.venv/bin/python -m jobwatch.postgres.import_cli reconcile \
  --repo /path/to/grad-job-tracker \
  --commit 90434ea6d9a3457f9c8ba40f01ad25fa2ceffca3 \
  --report /tmp/jobwatch-reconcile-90434ea.json
```

The target must have the exact packaged migration ledger and expected application tables. A pending/changed migration, unmanaged schema, extra table, existing domain row or audit row causes refusal. Import never deletes, truncates, resets, adopts or disables triggers. Repeating an import into a populated target fails, even with the same commit.

All logical inserts, source pointers and trigger audit records commit in **one transaction**. A transaction advisory lock serializes migration/import operations; table locks exclude concurrent domain writes while emptiness is checked and imported. Deferred FKs are checked before reconciliation. Any unexplained reconciliation discrepancy aborts the import transaction. Reconciliation alone uses a read-only repeatable-read snapshot.

A lost COMMIT acknowledgment has an uncertain outcome. Reconnect and reconcile the same frozen commit before deciding whether to retry; a complete target will reconcile and refuse a second import. Never reset a target to resolve uncertainty. Report-file failure after a commit also does not undo the database. Import activity clocks and audit login identify the actual import, not the original owner or provider.

Python APIs use ordinary dict-row connections from the existing connection module:

```python
from jobwatch.postgres.connection import connect
from jobwatch.postgres.import_plan import build_plan
from jobwatch.postgres.importer import apply_import, reconcile

plan = build_plan('/path/to/grad-job-tracker', '90434ea6d9a3457f9c8ba40f01ad25fa2ceffca3')
with connect(purpose='migration') as conn:
    imported = apply_import(conn, plan)  # fresh migrated target only
    verified = reconcile(conn, plan)
assert verified['reconciliation']['ok']
```

## Evidence and representation decisions

Every indexed posting is imported, including exclusions. Stable posting/application identities, listed/not_seen state, first-seen basis, current fields, screening codes/order/rules/input/basis/time and owner fields are preserved. Import does not call a provider normalizer or screening function. Schema-1 decoding reuses the existing legacy upgrade behavior, retaining the original row alongside that representation.

Historical recovery visits every relevant retained index and text-file version, all nested description references and inline legacy text. Every file version is UTF-8/hash checked, including historical files no longer present. A bad hash fails even if another good copy survives. Recorded character counts are checked. Current empty text, unavailable text and absent owner ledgers remain distinct. Unexpected historical-only posting identities or unattributed text objects fail explicitly rather than being discarded or assigned to an invented posting.

Current and historical normalized snapshots use `origin='legacy_index'`. Their deterministic ASCII bytea evidence contains the complete original row plus a per-posting map of all recovered/unknown description references, exact text and Git commit/path/pointer provenance. Genuine schema-1 native records additionally use `origin='git_history'`; native IDs must agree with their posting. Their typed fields are the retained legacy representation, labelled accordingly, not newly parsed output. Native history never silently replaces a newer current legacy snapshot. These are parsed values, not an HTTP byte archive.

The same `posting_versions` table holds all of this. Immutable tuple IDs are deterministic for the frozen commit. No section store, blob-history table or replay engine was added. NUL remains in native/legacy bytea evidence; derived searchable values alone use U+FFFD with `nul_replaced`. The imported judgment remains unchanged even for exceptional sanitized legacy inputs; a later explicit rescreen can update its fingerprint/interpretation.

Original source, screening and mixed events remain in the one `posting_events` stream as `import/legacy_event`. `changes.legacy_category` preserves the known distinction; `original_json` preserves the complete original event, including unknown fields, NUL and old interpretation changes. Stable event IDs, occurrence ordinals and Git pointers prevent duplicate imports across repeated snapshots without dropping actual duplicate entries within one retained list. Description references point to the retained legacy evidence map and explicitly say whether the text is available. Before/after version FKs remain NULL where the old data does not establish the corresponding full source snapshots. No provider authorship, comparison version or employer edit time is invented.

Each distinct health snapshot becomes `mode='import',status='imported'`, with original health/scan JSON and chronology retained in `details`. Missing counters stay NULL. The source success pointer can reference a labelled imported baseline with the known old success time; it is never a newly executed collection. The view's current attempt fields remain unknown until a real attempt occurs because imported runs are not publish runs. Posting last-seen uses recorded per-posting sightings, source events or first observations, with conservative historical recovery; it never inherits a later board success. Original row evidence retains the limits of those old timestamps.

All employer/watchlist records survive, including employers without ATS boards. Original configuration and unprojected fields stay in `employers.details.legacy_config`. Labelled private control archives/root metadata live in the first deterministic employer's `legacy_import_json`; this is a migration archive, not a writable configuration source. Historical owner file contents and their hashes are retained there without replaying guessed owner edits. Collector grants exclude these private fields.

Legacy-enabled sources lacking separately dated endpoint evidence become candidates, with their original enabled configuration preserved and an explicit activation gate. A mapping timestamp, careers timestamp or a successful old scan is not converted into a validation timestamp. Disabled configured boards remain paused. No coverage expansion occurs.

Vacancy lead keys derive from exact URLs; programme keys use employer/programme names independent of quoted year, with an explicit cycle suffix. Collisions fail. `open_2027` becomes `open` plus quoted 2027; previous cycles keep their old year/state. Quoted vacancy intake years also use `cycle_year`/`cycle_wording`. Unknown/unspecified wording, deadline uncertainty, source notes and all unprojected fields remain in the original `details.legacy_json`. Lead associations use recorded keys or exact employer names; no fuzzy identity inference.

Verification observations are recovered from historical ledgers **and embedded index records**. Known check times/URLs/methods/wording/hash bindings and recorded observers are preserved; missing historical observer is `unrecorded_legacy`. Unknown status alone creates no observation. Missing check times remain a stated limitation and original JSON, never a synthesized check. Description checks can bind to the recovered legacy evidence map, with their original hash, rather than falsely binding to the current typed wording.

Application CSV fields and private triage fields are preserved. An application key that is an indexed posting key supplies an explicit link; manual keys stay unchanged/unlinked. Unambiguous exact-URL fallback is the existing SQL view behavior. Loose/fragment-different URLs do not create links. Owner import audit uses `import:<commit>` and the real database login; it does not pretend to be the original owner action.

## Read the reconciliation result

A passing report means every expected field/payload/reference matched, not just counts. Reports include the frozen source file hashes/presence, plan hash, exact posting key sets, row hashes and field-level failures for all tables, observation/judgment/code distributions, immutable evidence and event identity/content, source health/configuration, research/verifications, owner fields and audit coverage/images. Owner record identifiers are hashed in diagnostics; notes, source wording and credentials are not copied into report values.

Expected representation transformations and unresolved legacy limitations are separate from failures. `reconciliation.ok=true` can coexist with `ready_for_cutover=false`: missing endpoint checks, unavailable native/text evidence and permanently unretained schema-1 exclusions require explicit review before cutover. The readiness flag covers import/source-data gates, not permission to deploy or assurance about a future hosted service.

Independent imports compare all domain fields after resolving numeric employer/source references, run/event sequence offsets and trigger images to stable identities. Only new import/migration activity clocks, audit transaction IDs/sequence offsets and verification recording clocks are ignored. Original evidence, check/observation times, actor/login, ordering, private fields and JSON payloads remain compared. Restore additionally compares generated FTS, schema definitions, ownership, grants, views, functions, triggers, migration checksums and exact sequence state. PostgreSQL's pretty constraint deparser normalizes redundant associative parentheses introduced by dump/reparse while preserving operator precedence and terms.

## Run integration tests and the real frozen rehearsal

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHON=.venv/bin/python bash scripts/test_postgres.sh

# Includes the entire integration suite, then two real imports and a fresh restore:
PYTHON=.venv/bin/python bash scripts/test_postgres.sh --rehearse-import \
  /path/to/grad-job-tracker \
  90434ea6d9a3457f9c8ba40f01ad25fa2ceffca3 \
  /tmp/jobwatch-rehearsal-new-directory
```

The existing launcher creates/verifies/removes its pinned local PostgreSQL 17 container. The rehearsal derives loopback credentials from that verified container and creates three independent fresh databases: A, a second import, and B for restore. Tests never consume an arbitrary caller database URL. Administrative database creation is test scaffolding; migration/import/dump/restore execute as the non-superuser migration owner.

`pg_dump --format=custom --schema=jobwatch` creates a real archive; `pg_restore --exit-on-error --single-transaction` restores it into B **without preapplying migrations**. The dump contains the application schema, data, sequences, grants, owners and migration ledger. The same named roles must already be provisioned. No extensions beyond ordinary PostgreSQL are required. A separate cluster would need the bootstrap/equivalent role setup first; this rehearsal proves a fresh database restore within a provisioned local cluster, not a hosted cluster recovery or vendor PITR procedure.

The rehearsal independently reconciles A and B against the frozen Git source, compares A/the second import/B logically and structurally, checks sequences, exercises SQL views/FTS, and verifies runtime permission denials. A separate synthetic fixture does the same with three nonempty applications and one private triage record. Reports contain archive size/hash/tool versions and exact commands. Raw backups stay inside the disposable container and are removed; they are not committed or exported as a public artifact.

## Future cutover gates

The rehearsal does not authorize production cutover. Before that separate stage: complete evidenced endpoint checks/activation, explicitly accept remaining historical limitations, provision authenticated hosted roles/TLS/backup handling, validate SQL read/report behavior required by production, freeze scheduled/owner writes, pin the final commit and rerun the complete import/reconciliation/restore process. Then change the single production writer/read path and retire file-era persistence in one controlled release. Do not build ongoing dual writes.

Before the first authoritative DB write, the frozen file snapshot remains a viable rollback. After DB observations or owner writes commit, simply restarting the old writer at the frozen commit loses data. Freeze writers, preserve a new backup, repair/roll forward or restore separately and reconcile valid later changes before promotion. No general DB-to-JSON synchronization or historical replay system is part of this stage.
