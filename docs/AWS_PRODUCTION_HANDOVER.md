# PostgreSQL production handover

26 October 2026. The owner explicitly accepted H1/H2 and the failed GitHub push
and authorised the single-writer cutover from release `feb3dbc`.

**Activation in progress:** the frozen source is imported and reconciled; its real
S3 archive has been independently restored and reconciled. All nine actual sources
have genuine new validation receipts and are monitored. The old Git workflow is
disabled with no in-flight run; routine PostgreSQL collection remains disabled
until the controlled scan and final recovery/security checks pass.

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

## Operations and recovery

Installed code release **R**: `e3807a084f837ede9754b406a5813a22965fa298`;
archive SHA-256 `7660c46153ddc1f301e91ea351fba22184fc03a082a6e1b2a964e5a5e5f9931a`.
PostgreSQL17.11, vector0.8.7, migrations0001–0004; native ARM64/Python3.12.3 /
psycopg3.2.13. No model inference on the 2 GiB host. Vectors remain optional derived
data; missing coverage is visible, with indexing/query inference on suitable local
hardware. SQL/BM25/FTS work without vectors.

Keep [AWS-only backups](AWS_BACKUP_POLICY.md) at 00:00/12:00 UTC and the health
check every fifteen minutes. Existing 30-day current + 30-day noncurrent retention
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
