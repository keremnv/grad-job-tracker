# PostgreSQL production handover

26 October 2026. The owner explicitly accepted H1/H2 and the failed GitHub push
and authorised the single-writer cutover from release `feb3dbc`.

**Production activated at 22:31 UTC:** PostgreSQL is the single authoritative
store. All nine actual sources passed genuine validation and controlled publication.
The daily collector timer is enabled; the Git workflow is disabled and its
replacement definition has no schedule, data writer or write permission.
Both histories were integrated normally into main, preserving the exact frozen
observation tree. No ongoing file/database synchronisation exists.

## Data and authority

Frozen data commit **C**: `9bbcb0149d41250cc2e56b2cbb25779bd5fafc16`.
Plan SHA-256: `96e99a0a0b41a408933b963142e126b9abcebde9a23ad99e9e6b7b28c1a22f7f`.
Committed import: 631 postings, including 574 exclusions; 1,013 immutable versions,
110 retained events, 44 employers, nine sources, 34 leads/programme cycles,
24 verifications and zero applications/triage. All 3,510 recorded text references
resolved. Import audit images matched. The first attempt hit the operator's
30-second statement timeout and fully rolled back; read-only reconciliation proved
all intended tables empty. A bounded longer operator timeout then completed the
single committed import. Runtime limits and application code were not changed.
Normal sequence gaps from the rolled-back attempt were preserved, not reseeded;
all independent restores matched exact sequence state.

The baseline real-data archive is 8,353,441 bytes. Exact S3 versions, AES256,
SHA-256, manifest linkage and receipt were verified; independent local PG17.11 /
vector0.8.7 restore matched rows/schema/grants/audit/sequences and reconciled to C.
The temporary migration password was revoked, local passfiles removed, forwarding
closed and the original HBA restored byte-for-byte. No permanent credential was
created. Private receipts, full Git bundle and archives remain outside Git.

H1: missing old native ATS fields cannot be reconstructed. H2: never-indexed
old exclusions cannot be recovered. The 26 October runner snapshot was not
published/recovered; nine reported changes do not establish nine lost postings.
Later current observations never recreate its original observation history.
These accepted limits do not excuse loss/corruption of retained records.

## First collection and recovery proof

Controlled service invocation: **21:23:29–21:23:55 UTC, 26 October 2026**.
All nine sources published **620 actual positive observations**. The retained
corpus is now **636 postings**, 1,633 versions and 740 events; 579 exclusions remain
researchable. There are 622 listed and 14 `not_seen` records. Listed is not a claim
of a current positive observation: two remain listed after positive-only coverage.
Palantir's multi-page run was positive-only and generated no absence events.
Eight other runs were absence-safe under their actual response contracts.

All 631 imported identities, first-observation values, versions and events remain.
New history consists of 615 native-evidence baselines, five first observations and
ten absence transitions; no legacy-only baseline was labelled an employer edit.
Applications/triage remain zero; leads, verifications and owner audit were unchanged
by collection. The nine authorised source activations added nine audit records;
the final audit count is 96. Actual owner-role daily/weekly/requirements reports
and BM25/FTS queries passed without model inference.

Three actual imported-data archives passed separate off-host restoration:

| Archive | Qualification |
|---|---|
| Imported baseline | 8,353,441 bytes; full reconciliation to C, including all datasets, schema/security and sequences; independent restore/verification about 9.2 seconds |
| After source activation | Exact restored data/security/sequences; immutable imported baseline retained |
| After controlled scan | 13,033,724 bytes, conservative snapshot start **21:32:54 UTC**; exact restored data/security/sequences and retained imported baseline; verification about 10.7 seconds |

Each used the actual installed backup service, exact versioned S3 archive and
manifest, AES256, SHA-256 and manifest-last completion receipt. Each restore ran
in a fresh, network-disabled local PostgreSQL17.11/vector0.8.7 container, without
reading the original EC2 database. Populated-target refusal passed. These measured
small-dataset verification times are not a recovery-time guarantee.

Qualification exhausted the backup service's existing three-start/six-hour limit;
a fourth start was refused before any dump began. All three previous operations
had succeeded. The operator acknowledged only that test-induced failed counter
once, retained the unchanged rate limit, and the actual post-scan service then
completed successfully. No backup, timer or protection setting was weakened.

The general readiness command still expects the older Stage3 restore receipt and
therefore does not mark its `local_restore_rehearsal` check passed using format2
proof. Actual format2 restore and reconciliation above are independently verified;
no legacy receipt or generic `deployment_ready=true` claim was fabricated.

## SQL-first operation

Use the existing authenticated operator SSM path (`grad-jobwatch`, `eu-north-1`);
resolve the existing instance from private operator configuration or its Name tag.
No public PostgreSQL port or SSH listener is required. Within the approved session:

```sh
sudo -u jobwatch-owner psql -X -h /run/jobwatch-postgresql -U jobwatch_owner -d jobwatch
sudo -u jobwatch-owner env \
  JOBWATCH_DATABASE_URL='host=/run/jobwatch-postgresql dbname=jobwatch user=jobwatch_owner' \
  /opt/jobwatch/current/venv/bin/jobwatch-pg-ops report daily
```

Use [SQL research](../sql/postgresql/operational_queries.sql) and
[audited owner transactions](../sql/postgresql/owner_workflows.sql). Owner SQL
is distinct from infrastructure administration and collector/migrator privileges.
Do not restore temporary migration authentication for ordinary research.
All verdicts remain researchable; `not_seen` is not closure or an eligibility claim.

## Validation at handover

Final integrated checkout: **258 offline tests passed** in 6.023 seconds, and
**144 disposable PostgreSQL17.11/vector0.8.7 tests passed** in 121.682 seconds,
including cached real-model inference, MCP interoperability, import and restoration.
Python3.12.14/psycopg3.2.13 on the local test machine; actual ARM64 hosted checks
above used the installed native runtime. Model tests never ran on EC2.

Commands: `PYTHONPATH=src python -m unittest discover -s tests -v` and
`HF_HUB_OFFLINE=1 JOBWATCH_REAL_MODEL_TEST=1 HF_HOME=<existing-model-cache>
PYTHON=<model-venv>/bin/python bash scripts/test_postgres.sh`.
Markdown lint, changed relative links, Mermaid rendering and `git diff --check`
passed. No schema, package, infrastructure or application logic changed in the
handoff documentation commit. Actual source collection and three independent
production-data restores are additional to the disposable suite.

## Operations and recovery

Installed code release **R**: `e3807a084f837ede9754b406a5813a22965fa298`;
archive SHA-256 `7660c46153ddc1f301e91ea351fba22184fc03a082a6e1b2a964e5a5e5f9931a`.
PostgreSQL17.11, vector0.8.7, migrations0001–0004; native ARM64/Python3.12.3 /
psycopg3.2.13. No model inference on the 2 GiB host. Vectors remain optional derived
data; missing coverage is visible, with indexing/query inference on suitable local
hardware. SQL/BM25/FTS work without vectors.

Enabled schedules: collector **06:15 UTC daily**, with up to five minutes' random
delay and no persistent catch-up; [AWS-only backups](AWS_BACKUP_POLICY.md) at
**00:00/12:00 UTC** with persistent catch-up; health every fifteen minutes.
At activation the actual next collection was **27 October 06:17:19 UTC**, backup
**27 October 00:00 UTC**, health **26 October 22:45 UTC**. All four alarms were OK
with notification actions enabled. Actual failure/stale/missing-heartbeat and
recovery email delivery was confirmed during prior monitoring qualification.
Do not infer the future midnight/daily executions from these results. Existing 30-day current + 30-day noncurrent retention
and delivered SNS notifications remain. Frequency is not a maximum RPO; AWS
project/Region failure, missed jobs or administrator deletion can prevent recovery.
Check fresh receipts, four alarms and actual service results. Restore monthly and
before/after material maintenance using exact S3 versions into a separate target.
Never overwrite a populated database or restart stale Git after authoritative SQL
writes. Preserve the affected DB, restore separately and reconcile/roll forward.

No Terraform apply: the known protected-instance user-data replacement proposal
is still blocked. Preserve EBS/bucket/state guards; code maintenance uses the
reviewed artifact installer. No new infrastructure was created for cutover.

Real-data ChatGPT/MCP access and Proton HTTPS subscriptions remain separately
approved follow-ons. No production read login, public endpoint, tunnel or calendar
sharing is implied by this handover. Repository visibility remains private.
