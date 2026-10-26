# Stage 4B.2d — independent recovery and remaining readiness decisions

**Historical qualification record.** The later owner-accepted maximum data-loss
tolerance is **12 hours**. The daily/26-hour policy proposed below is superseded
and does not meet it. Use the [current release checklist](V1_RELEASE_CHECKLIST.md)
and updated [cutover sequence](AWS_CUTOVER.md); no new AWS inspection is implied.

Checked 25 October 2026, base `3f8f7a4a7d28e4d34a92819eac96f974f5a898f9`,
branch `feat/aws-deployment-preflight`. Git remains authoritative. No production
import, ATS request, permanent credential, timer activation, infrastructure change,
workflow change, merge or cutover occurred.

## Independent restore — passed

A recovery operator retrieved the **exact existing synthetic archive and manifest
S3 versions**. AES256, S3/local SHA-256 and the manifest's archive-version linkage
matched the preceding receipts. The archive is 94,908 bytes; private receipts retain
its hash, object keys and version IDs. No new backup or production input was used.

The archive/manifest were encrypted, copied and decrypted locally, then restored
from the **decrypted bytes** into new disposable PostgreSQL on the development
machine, independent of EC2. Docker networking was **none**, no TCP port was
published, and PostgreSQL listened only on a private temporary Unix socket. The
container received no AWS credentials, EC2 filesystem or production connection.
No original EC2 database was queried during restore or verification.

This exercised **ARM64-origin dump → x86-64 PostgreSQL 17.11**. Required roles and
database-level grants/search paths were reconstructed using the existing bootstrap
SQL, with postgres as database owner. Local trust authentication was confined to
the private socket/test container; this does not replace the host's tested peer
configuration or authorize production trust authentication.

The unchanged `database_backup.restore` performed real pg_restore as the restricted
migration role, preserving ownership/ACLs and using one transaction. Its manifest
comparison and independent runtime checks passed:

- Exact migration checksums, all table/evidence/owner/audit rows, schema/functions,
  triggers, owners, grants, security fingerprints and archived sequence states.
- Two postings, three retained versions including NUL-bearing parsed evidence,
  one material event, one application, one triage record and four audit rows.
- Four SQL views, BM25/FTS, daily/weekly reports and private-note omission.
- Collector private-table denials, owner/collector immutable-history and audit-write
  denials; populated-target restore refusal. Zero collection runs.

Measured on the cached local image: **1.658 s** startup/security provisioning,
**0.402 s** restore/core equality, **2.164 s total** including runtime checks.
This tiny fixture does not measure production-volume recovery, host provisioning,
image downloads, key retrieval, operator availability or a replacement EC2 host.

## Commands and requirements

Requirements: local Unix-socket Docker engine, the pinned PostgreSQL image below,
Python 3.12 with psycopg 3.2.13, the reviewed code/bootstrap SQL and GnuPG for the
custody rehearsal. Source application/migrations were unchanged. Full Docker/SQL
utility argv and results are retained privately in commands.json/result.json.
No arbitrary database URL is accepted by the harness.

```sh
# Recovery operator: use the exact PRIVATE receipts, never "latest" by filename.
# Execute once per new private output path; no credentials in command arguments.
aws --profile grad-jobwatch --region eu-north-1 s3api get-object \
  --bucket BUCKET --key ARCHIVE_KEY --version-id ARCHIVE_VERSION \
  --checksum-mode ENABLED /PRIVATE/database.dump
aws --profile grad-jobwatch --region eu-north-1 s3api get-object \
  --bucket BUCKET --key MANIFEST_KEY --version-id MANIFEST_VERSION \
  --checksum-mode ENABLED /PRIVATE/database.json
# Compare VersionId, AES256, returned ChecksumSHA256, local SHA-256 and
# manifest.archive_object to the trusted receipts before decrypt/restore.

# Exact command actually executed after the synthetic GPG round trip:
/tmp/jobwatch-ops-py312/bin/python infra/aws/tests/recover_offhost.py \
  --archive .local-reports/aws-independent-recovery-4b2d/decrypted-input/database.dump \
  --manifest .local-reports/aws-independent-recovery-4b2d/decrypted-input/database.json \
  --output .local-reports/aws-independent-recovery-4b2d/offhost-restore-verified \
  --confirm-synthetic
```

Image: `postgres@sha256:858bb5e98eed02eb8cafbcdc007f9a65d1b613972028944a8ec2133ccaf80d27`.
Python 3.12.14, psycopg 3.2.13, PostgreSQL 17.11, GnuPG 2.4.8. The harness verifies
its newly created labelled container, network isolation and local Docker context;
uses C.UTF-8/UTF8; and refuses a changed archive, remote engine or existing output.
It restores this synthetic fixture only, not the future production dataset.

A first local run completed restoration but failed removing a temporary socket
owned by the container UID. The test harness cleanup was corrected; a fresh
second run passed including cleanup. Initial receipts remain private. No AWS
resource/database or reviewed production software required repair.

Additional executed tests: **3 harness safety tests**, **211 offline tests** on
Python 3.12.14, Ruff F, Python compilation and git diff checks. The earlier full
105-test PostgreSQL/deployment suites were not rerun: runtime software did not
change. This stage executed the actual additional independent recovery instead.

## Encrypted custody rehearsal — passed; durable custody — not established

A private tar containing only the synthetic dump and manifest was encrypted with
**GnuPG AES256 and integrity protection**, copied as ciphertext, then decrypted.
Package/archive/manifest hashes matched before and after. Wrong-key and corrupted
ciphertext were rejected. The round trip took **1.985 s**. A fresh random synthetic
secret was supplied through an inherited file descriptor, never argv/environment
or a report. Its temporary directory was destroyed after the test. No permanent
production key was generated. The retained test ciphertext is not a recoverable
production backup or an owner-held recovery credential.

The copy was within the local development environment. **An ephemeral workspace
is not durable independent custody.** No external storage account, paid service,
production-data upload or owner credential was created. The owner must choose:

| Practical option | Required owner configuration |
|---|---|
| Existing owner-managed encrypted backup disk/NAS, with a physically separate copy | Destination, backup/sync cadence, physical custody and proof both copies survive loss of this machine |
| Existing private non-AWS storage receiving ciphertext only | Explicit private-data approval, independent login/recovery, retention, and tested retrieval while AWS is unavailable |

Prefer an existing owner backup system over another hosted service. Encrypt
**before** copying into the independent destination. The secret/private key must
be held separately by the owner, for example in an independently recoverable
password manager plus a secured offline recovery copy. Do not rely solely on the
AWS project's login, this workspace, an EC2 file, Terraform or the destination
holding its own decryption key. Key provisioning and destination approval remain
pending; no decision is recorded on the owner's behalf.

Repeatable future owner procedure, after approval:

1. Retrieve exact completed backup versions; verify the trusted SHA-256 and archive
   linkage. Keep plaintext only in a private work area on owner-controlled storage.
2. Bundle dump, manifest, checksum receipt and reviewed recovery/bootstrap/code
   references. Encrypt with the owner's existing recipient key, or a high-entropy
   symmetric secret passed via a protected FD. Never put the secret in argv/logs.
3. Copy only ciphertext and its checksum to the independent destination. Re-read
   the destination copy and compare its hash; separately retain the trusted receipt.
4. Retrieve/decrypt from that destination on a recovery environment, verify hashes,
   and run the existing fresh-target restore/security/row/sequence checks.
5. Record a redacted success/time receipt. Retain **30 daily encrypted recovery
   points** initially; test monthly and after upgrades/key or destination changes.
   Rotate keys on compromise or an owner-approved schedule. Keep the old key until
   all retained copies using it expire or are re-encrypted and verified.

For example, an existing owner recipient key can use `gpg --encrypt --recipient
OWNER_RECOVERY_FINGERPRINT --output recovery.tar.gpg recovery.tar`, then `gpg
--output recovered.tar --decrypt recovery.tar.gpg`. These are prepared production
commands, not evidence an owner key exists. The actual synthetic rehearsal used
`--symmetric --cipher-algo AES256 --force-mdc --passphrase-fd FD` with a temporary
secret. GnuPG failure/corruption must prevent promotion even if partial plaintext
was emitted. Do not treat encryption as proof the input data was correct.

## Recovery policy proposed for owner acceptance

Daily dumps provide approximately **24 hours plus scheduling/dump time** of data
loss while healthy. A failed/missed run can make it 48 hours or longer; without
freshness detection and response there is **no finite worst-case guarantee**.
Private notes, triage, applications and observed source evidence committed after
the surviving snapshot are lost. Re-fetching cannot reconstruct all of them.

Recommend daily S3 backups plus daily independently retained encrypted copies;
a conservative **26-hour maximum recoverable age** check; and a **four-hour
recovery target during operator availability**. These are proposed objectives,
not accepted guarantees. A weekly independent copy would instead expose up to a
week of data during project lockout and must not be presented as a daily RPO.
Take and verify an extra backup before migrations/upgrades or destructive
maintenance; take one after particularly important owner updates when a day's
loss is unacceptable. No replication/WAL platform is introduced.

Freshness must verify a completed manifest, matching exact archive version/hash,
and its creation time. **S3 upload time alone is insufficient**: this archive was
uploaded about **9.4 hours after** its manifest was created. `created_at` is backup
manifest creation, not the exact exported-snapshot timestamp; conservatively add
the scheduled service's **45-minute maximum duration** to its age. For a manual backup, record its start/duration and use that bound; an unknown duration cannot establish a guaranteed snapshot age. Treat missing/future dates,
missing objects, failed checksum/version checks, or conservative age >26 hours as
NO-GO. The instance deliberately cannot read backups; an authorized recovery
operator must check independently. Existing error logs/alarms do not detect a
missing run or prove freshness. No production schedule/freshness checker is active.

When EC2 is unavailable: preserve the original disk/host, retrieve a completed
backup, prepare a separate approved local/recovery target, reconstruct roles and
security, restore/compare and verify owner/history data. A new AWS host requires
separate authorization. Do not format the old disk or apply a replacement plan
as an improvised repair.

When the AWS project is inaccessible: recover from the **already retained
independent ciphertext** with owner-held keys and reviewed code/PG17 tools,
without S3, SSM or the original host. This rehearsal proved data portability and
offline restore; actual owner storage/key availability remains unproven. Keep any
recovery copy read-only until the old writer is fenced and promotion authorized;
a paused project can resume later. Never restart stale Git persistence after
authoritative SQL writes, or run two collectors against divergent authorities.

## Read-only operational findings

- Intended `jobwatch`: all 11 domain tables empty; migration ledger has three rows.
  Both timers disabled/inactive; services inactive. Existing 20 Terraform resources,
  encrypted volume and protected S3 state version are unchanged. The plan lock was
  released normally; all baseline SSM sessions are closed.
- Root/data free approximately **7.86/14.69 GiB**; available memory **1.36 GiB** of
  1.79 GiB, no swap. Adequate small-test headroom, not a real-corpus load forecast.
- All three alarms are OK, but **AlarmActions/OKActions/InsufficientDataActions are
  empty**. There is no configured alarm delivery recipient. Select and test an
  approved notification destination and backup-freshness/capacity procedure.
- GetAccountPlanState reports **PAID, ACTIVE, US$160 remaining credits**. It returned
  **no expiry date**. Current spend limit, credit expiry and charge eligibility
  remain unverified. The previously owner-reported US$60 limit is not a fresh check;
  confirm in **AWS Settings → Billing**. Credits are not a spending cap. No paid Cost Explorer calls were made.
- One encrypted available manual Aurora PostgreSQL snapshot remains; zero RDS
  instances/clusters. Snapshot metadata reports AllocatedStorage=0, which is **not
  evidence of zero billed backup storage**. SnapshotStorageUsed returned no points.
  AWS documents that manual snapshots surviving deletion incur backup storage
  charges. The official Stockholm price list retrieved for this check lists
  **US$0.02/GB-month** for Aurora PostgreSQL backup usage, excluding tax/credits;
  actual billed size/charge remains unknown. No snapshot operation modified it.
- A normal S3-backed Terraform plan displayed **1 addition / 0 changes / 1 deletion**
  for the expected EC2 user-data replacement, then exited 1 because prevent_destroy
  blocked it. **No valid apply plan or resource change resulted.** Bootstrap/helper
  code differs from the original deployed user-data following reviewed in-place
  repairs. Do not disable guards, use -target, edit state or add ignore rules to
  hide it. Routine code updates use artifacts; bootstrap changes are for separately
  reviewed future/recovery host builds. Any later infrastructure update must first
  resolve this deployment-version pinning/control deliberately, without replacement.
- Remote SQL uses the prepared SSM localhost-forwarding document and restricted
  PostgreSQL owner login. Production owner passwords remain unset. SQL-only agents
  need scoped temporary forwarding permissions, not root shell/migration/IAM
  privileges. Credential setup, least-privilege access and tunnel testing remain
  separately approved work; no credential or IAM change occurred here.

Sources: [AWS Free Tier plan API](https://docs.aws.amazon.com/botocore/latest/reference/services/freetier/client/get_account_plan_state.html),
[Aurora retained snapshot billing](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-storage-backup.html),
[official Stockholm RDS/Aurora price list](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonRDS/current/eu-north-1/index.json).
The prior approximately £17.53/month infrastructure estimate is not repriced here.
No recurring resources were added; ordinary read/S3 requests may incur minor usage.
Local Docker/GnuPG introduced no paid service.

## Auditable production gates — no overall GO

| Gate | Status at this stage |
|---|---|
| 1. Independent recovery and owner-held custody | Synthetic off-host restore PASS; durable destination/key custody PENDING |
| 2. Accepted RPO and restoration procedure | Proposed above; owner acceptance and operational timing PENDING |
| 3. Nine genuine ATS checks and employer mappings | NOT RUN; retain all nine intended sources from the cutover runbook |
| 4. Unavailable historical evidence | Owner acceptance PENDING: missing complete schema-2 native responses and never-indexed pre-schema-2 exclusions cannot be reconstructed |
| 5. Integration with current main | Main inspected; one main-only commit and 15 feature-only commits observed; no integration/merge. Preserve newer Git data |
| 6. Freeze Git writer and owner writes | NOT STARTED; file-backed schedule remains authoritative |
| 7. Final authoritative Git commit | NOT SELECTED; choose only after freeze, never automatically reuse 90434ea |
| 8. Empty-target import/reconciliation | NOT RUN; intended database remains schema-only |
| 9. Independent restore of imported production database | NOT RUN; synthetic proof does not pass this gate |
| 10. Secure owner/agent SQL access | Passwords unset; scoped access, credential setup and tunnel proof PENDING |
| 11. Monitoring, backups and operating procedure | Logs/alarms tested; recipients, freshness/custody checks and scheduling PENDING; timers remain disabled |
| 12. Explicit single-writer cutover authorization | NOT GIVEN; no production authority switch |

Owner decisions required: independent destination/key custody and retention,
accepted RPO/RTO and operator response, notification/freshness responsibilities,
spend/credit confirmation, historical limitations, and later source/access/freeze
and cutover approvals. No incomplete item is approved on the owner's behalf.
Private command logs, exact S3 receipts and measured results remain under
`.local-reports/aws-independent-recovery-4b2d/`. No dumps, keys or owner records
are committed.
