# Stage 4E implementation report

25 October 2026. Branch `feat/agent-owner-calendar`, based on completed retrieval
`598a64b7c20f823672b4e650f20a912d6025b2eb`. Local/disposable only.

## Delivered and reused

- Reused the existing owner grants, audited transactions, application precedence,
  evidence tables, SQL/retrieval functions and RFC 5545 implementation. No migration,
  database role/grant change, write MCP, CRUD facade or new dependency.
- Added [ordinary SQL recipes](../sql/postgresql/owner_workflows.sql) for manual
  application creation/linking, stage changes, pending-action reschedule/clear,
  triage, evidenced leads and explicit vacancy linkage. Expected-field guards,
  postconditions, idempotent retries, rollback and read-back are tested as the
  actual owner login. Actor claims remain distinct from database identity.
- Completed shared PostgreSQL calendar projection: dated triage revisits respect
  application precedence; checked current programmes use explicit deadlines and
  retain their cycle year. Historical cycles, unknown/conflicting dates and
  ambiguous identities do not become calendar facts.
- Corrected the PostgreSQL unlinked-vacancy UID fallback to the legacy exact URL.
  Bounded private publication metadata preserves a published UID after explicit
  or unambiguous linkage, retires merged duplicates and retains manual action IDs.
  It stores hashes/revisions, not a second history of owner/source records.
- Added `calendar snapshot/status`: separate deadline/action feeds and combined
  fallback, minimal fields, byte/count/window bounds, checksums, advisory locking,
  fsync + atomic generation pointer, last-good preservation, validated freshness.
  Identical projected data keeps feed bytes, mtime, DTSTAMP, LAST-MODIFIED and
  SEQUENCE stable while manifest generation time advances. Material projection
  changes increment sequence. Removed/completed events disappear from full feeds.

The existing file-backed runtime/export remains unchanged. The old PostgreSQL
`report calendar` remains a private stateless compatibility export; published
minimal files are the subscription/manual-fallback interface. No HTTP server or
publication schedule is installed. Stage 4C read tools/MCP are unchanged/read-only.

## Executed validation

| Check | Result |
|---|---|
| Offline unittest suite | **232 passed each** on Python 3.11.16, 3.12.14, 3.13.12 (215 baseline + 17 snapshot tests) |
| Complete disposable PostgreSQL suite | **138 passed**, no skips (127 baseline + 11 owner/calendar tests); PostgreSQL 17.11, pgvector 0.8.7, Python 3.12.14, psycopg 3.2.13 / libpq 170006 |
| Real embedding smoke | Passed using the pinned cached text-only float32 EmbeddingGemma 2 runtime with HF network disabled |
| Existing deployment/protection regression suite | **19 passed**, signed local CLI archive and disposable Linux users; fake offline Terraform destruction-refusal plans, no AWS calls |
| Static checks | Ruff F checks, compileall, relative documentation links, `git diff --check` passed |

Commands (local runtime paths, no credentials):

```sh
PY312=/home/kerem/.cache/jobwatch-retrieval-stage/venv/bin/python
PYTHONPATH=src /tmp/jobwatch-ops-py311/bin/python -m unittest discover -s tests -v
PYTHONPATH=src "$PY312" -m unittest discover -s tests -v
PYTHONPATH=src /tmp/jobwatch-ops-venv/bin/python -m unittest discover -s tests -v
HF_HOME=/home/kerem/.cache/jobwatch-retrieval-stage/huggingface \
  HF_HUB_OFFLINE=1 JOBWATCH_REAL_MODEL_TEST=1 PYTHON="$PY312" scripts/test_postgres.sh
terraform -chdir=infra/aws init -backend=false -input=false -lockfile=readonly \
  -plugin-dir=/home/kerem/Desktop/Job/grad-job-tracker-starter-updated/pgvector-retrieval/infra/aws/.terraform/providers
JOBWATCH_CLI_TEST_ARCHIVE=/home/kerem/.cache/jobwatch-retrieval-stage/aws-cli-test/aws.zip \
  PYTHONPATH=src "$PY312" -m unittest discover -s infra/aws/tests -v
ruff check --select F src/jobwatch/calendar.py src/jobwatch/postgres/calendar_feed.py \
  src/jobwatch/postgres/operations.py src/jobwatch/postgres/reports.py \
  src/jobwatch/postgres/research.py tests/test_calendar_feed.py tests/postgres/test_owner_calendar.py
```

The PostgreSQL launcher removes inherited connection URLs, verifies its labelled
loopback Docker target and runs the actual application/migrations. The full suite
includes synthetic frozen Git import/reconciliation, two-database repeatability,
actual pg_dump/pg_restore, independent network-isolated recovery, collector
concurrency and read-only MCP tests. No actual tracker data/backup was used.

New tests cover SQL concurrency/stale guards, audit attribution/no-op/rollback,
manual-key linkage, programme years and uncertainty, ambiguous URLs, actual
research → owner SQL → audit → snapshot, unchanged collector/evidence/vector row
hashes, reschedule/completion/removal, privacy/injection, UTF-8 folding, byte/state
bounds, stable times/revisions, checksum corruption, unsafe paths/hardlinks,
umasks, writer exclusion, atomic failure preservation and publication-state copy
recovery. Console output is tested without private action text.

Initial tests caught a cookbook error: PostgreSQL 17 psql ignores `\quit 3`.
Rejected postconditions now roll back and raise a fixed SQL error under
`ON_ERROR_STOP`; conflict tests require exit 3, including concurrent losers.
An earlier database run skipped the optional model; the final complete run enabled
it. Initial deployment-guard setup lacked its cached archive/provider; the final
19-test run supplied both without AWS access. No outstanding failing tests remain.

## Remaining approvals and practical limits

See [owner SQL](POSTGRESQL_OWNER_WORKFLOWS.md) and [Proton operation](PROTON_CALENDAR.md).
Real HTTPS hosting, a stable/revocable feed credential and private-data scope,
publication scheduling/monitoring, transport-metadata custody, actual Proton plan
limits/subscriptions and notification/removal behaviour remain **unverified** and
require separate approval. No live Proton integration is claimed. Refreshes are
approximately 4–16 hours; subscribed calendars lack event-specific notifications.
Polling is not guaranteed deadline alerting; urgent follow-ups need a separately
reviewed reminder. Separate feeds support differing calendar-wide defaults.

One pending follow-up per application is retained; no general task/reminder service.
Stale guards compare fields, not an artificial row version (ABA is documented).
Missing/corrupt publication state fails closed; an old restored manifest cannot be
assumed to preserve a newer client's sequence. Actual cross-client identity/removal
and crash/power-loss testing belong to approved hosting qualification.

No AWS access or changes, hosted extension installation, credentials, real imports,
live ATS calls, timers, workflow changes, main merge or cutover occurred. Migrations
0001–0004, bootstrap grants, ChatGPT permissions and production data/config/workflow
paths are unchanged. Remote main was read as
`9bbcb0149d41250cc2e56b2cbb25779bd5fafc16`; all newer observations remain separate
and must be preserved on future integration. Other worktrees were preserved.

No recurring infrastructure/model/API cost was introduced. Hosting and any
notification costs are not assumed approved. Git remains authoritative; the AWS
host was not inspected in this stage and prior hosted status was not re-certified.
