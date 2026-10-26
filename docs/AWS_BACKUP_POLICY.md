# V1 AWS-only backup policy

**Owner decision, 26 October 2026:** automatic PostgreSQL dumps at **00:00 and
12:00 UTC**, stored only in the existing private, encrypted, versioned Stockholm
S3 bucket. This replaces the strict 12-hour maximum-loss requirement, six-hour
schedule, independent-custody requirement and automatic writer-fencing proposals.
No external worker/provider, new VM, replication or PITR is required for V1.

**Twelve hours is the backup frequency, not a maximum RPO.** A failed/missed run,
late recovery, project lockout, malicious administrator or regional failure can
cause greater loss or prevent recovery. Same-project S3 is independent of EC2
failure but not independent of the AWS project/Region. The owner accepts this
trade-off. Private decisions made since the last usable snapshot may be lost.
No fixed restoration-time guarantee or automatic all-writer fencing is promised.

## Prepared operation and retention

- `jobwatch-backup.timer`: 00:00/12:00 UTC, one-minute accuracy, no random delay,
  persistent catch-up after downtime. Catch-up runs once, not once per missed slot.
  The existing service remains bounded to 45 minutes with limited failure retries.
- Use the existing exported PostgreSQL snapshot and custom `pg_dump` archive.
  Archive/version/SHA-256 receipts, AES256, manifest-last completion, immutable
  evidence, database/security fingerprints and sequence checks are unchanged.
- The instance can upload backups but cannot read/delete them. Recovery uses the
  separately authorised operator and exact S3 object versions; no runtime IAM
  expansion is needed for the prepared checker.
- **Proposed retention:** expire current backup objects after 30 days; delete
  noncurrent versions one day later; retain one-day incomplete-upload cleanup.
  With unique backup keys this is roughly 31–32 days / 62–64 archive-sized objects
  at two runs/day, plus small manifests and any extra manual backups. S3 lifecycle
  is asynchronous and day-rounded, not an exact count/age cap. Versioning, encryption,
  public blocking and destruction protections remain enabled.
- This lifecycle proposal changes only `backups/`, never artifacts or Terraform
  state. **It is not applied.** The recorded bucket still has 30-day current +
  30-day noncurrent retention, potentially roughly 60 days / 120+ archives at
  two/day. Applying the one-day noncurrent rule later can permanently remove
  already-noncurrent versions older than one day; inspect inventory first and
  approve that consequence explicitly. It is not Object Lock/WORM.

[Versioned S3 expiration semantics](https://docs.aws.amazon.com/AmazonS3/latest/userguide/lifecycle-expire-general-considerations.html).

## Minimum monitoring — prepared locally, not deployed

1. **Failed process:** reuse the existing `FailedOperations` log metric/alarm.
   Connect its currently empty notification actions to an approved SNS topic.
2. **Missed backup / stale recovery point:** after BOTH S3 uploads are checksum/version
   confirmed, the backup command atomically saves a private `0600` completion receipt
   under `/var/lib/jobwatch-backup/`. Invalid/incomplete uploads cannot replace the
   last good receipt. The receipt contains timestamps, hashes and exact version
   links, never source text/notes/credentials.
3. **Checker heartbeat:** a small oneshot under the existing backup OS identity runs
   every 15 minutes on the same host. It has no database connection, no S3 read,
   no new IAM permission and no new cloud compute. It emits only redacted status,
   numeric snapshot age and `backup_healthy=0/1` to the existing CloudWatch stream.
   Age is measured from `backup_started_at`, never upload or manifest creation.
   Missing/invalid receipts or age **>=14h** are unhealthy; **>=26h** is critical.
   These are advisory thresholds, not an RPO or a reason to block owner SQL.
4. **CloudWatch:** one new `JobWatch/BackupHealthy` metric filter and one standard
   alarm: Minimum <1, 900-second period, two of three periods, missing data
   **breaching**. Thus backup-timer disablement ages the last receipt into ALARM;
   host/checker/log-delivery failure stops the heartbeat and also alarms. No default
   metric value is set: collector logs must not fabricate backup health.
   [Reviewable input](../infra/aws/backup-monitoring.example.json) is inert and has
   no configured SNS ARN. Actual delivery/evaluation timing needs hosted testing;
   CloudWatch's wider evaluation range means missing-data alerts can be delayed.
5. **Email:** one Stockholm SNS **Standard** topic, owner-selected email and confirmed
   subscription; CloudWatch-only publish permission constrained to the intended
   project/alarm ARNs. Attach ALARM/OK/INSUFFICIENT_DATA actions to the new alarm and
   the existing failure/host/credit alarms without overwriting other settings.
   No email address, ARN or subscription has been created by this work. The owner
   must confirm the subscription and observe real alarm/recovery delivery.

[CloudWatch missing-data behaviour](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/AlarmThatSendsEmail.html)
and [SNS email confirmation](https://docs.aws.amazon.com/sns/latest/dg/sns-email-notifications.html).
Notifications occur on alarm state changes, not continuous reminder emails. The
26h critical log status does not send a second email while the same alarm stays
ALARM. Check
an outstanding alarm and repair/retry manually; do not assume an unread email
provides a loss guarantee. A compromised host can falsify its receipt/heartbeat;
this checker reports the last confirmed upload, not ongoing S3 integrity. Keep
operator restore rehearsals to verify that the stored versions remain recoverable.

## Restore and routine practice

Retrieve the exact archive and completion-manifest versions over authenticated AWS
access. Check encryption, S3/local checksums, archive size, format and version linkage.
Provision a **separate fresh** PostgreSQL 17/vector 0.8.7 target with matching roles,
database ACLs/settings and extension ownership. Use the existing guarded restore;
compare rows/evidence/history/audit, owners/grants, migrations, sequences and views.
Never restore over `jobwatch`, use `--clean`, disable triggers or reset data to make
checks pass. Retain the pinned pre-vector bundle for format-1 archives. A temporary
local restore target is valid verification, not a second operational database or
an external custody requirement.

Take a verified manual backup before migrations/upgrades and before/after cutover.
Test restore monthly and after relevant software changes. After authoritative SQL
writes, freeze affected writers and preserve the current database before repairing
or restoring separately; do not resume stale Git as automatic rollback. A real-data
restore/reconciliation after final import remains mandatory, with separate approval.

## Marginal monthly estimate — not verified charges

Public Stockholm tariffs checked 26 October 2026: S3 Standard **$0.023/GiB-month**,
PUT **$0.005/1,000**, GET **$0.004/10,000**; one new CloudWatch metric **$0.30/month**,
one standard alarm **$0.10/month**, logs **$0.54/GiB**. SNS list rates are $0.50/million
publishes and $2/100,000 emails; estimates below do not rely on free allowances,
credits or a spending limit. Existing host/storage/state/alarms are reused.

| Compressed archive size (scenario, not measured production) | Backup storage + new monitoring/requests/month, USD | Illustrative GBP including 20% VAT at £0.77/$ |
|---|---:|---:|
| 10 MiB | $0.42 | £0.39 |
| 100 MiB | $0.55 | £0.51 |
| 1 GiB | $1.88 | £1.73 |

Calculated conservatively with 64 retained archives, 120 backup PUTs/month, 100
email/publish events and 1 KiB/15-minute heartbeat. Small manifests/log storage
add fractions of a cent; manual backups/retries increase usage. No Glacier,
paid query/observability service, external copy or new instance is proposed.
Recovery downloads to a local machine can incur ordinary internet egress beyond
applicable allowances; same-Region EC2/S3 uses the existing path. Retain approved
archives until recovery completes.

Rates: [S3](https://aws.amazon.com/s3/pricing/), [CloudWatch](https://aws.amazon.com/cloudwatch/pricing/),
[SNS](https://aws.amazon.com/sns/faqs/); exact [regional S3](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/current/eu-north-1/index.json)
and [CloudWatch](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonCloudWatch/current/eu-north-1/index.json)
tariffs were read without an authenticated project API call. FX/VAT are illustrative.
The previous £17.53 host estimate is historical, not a current invoice. Preserve
£10–£20/month where practical; measure real dump growth and include the retained
Aurora snapshot and existing usage before promising the total.

## Smallest next approval

Approve one **backup/monitoring-only activation** on the existing empty host:
install a separately verified code-only release with these changes, keep collection
inactive, establish the SNS topic/confirmed owner email/filter/alarm and attach
notification actions, then take one verified manual backup to initialise the
receipt (the prepared backup service supplies `--receipt`; an ordinary manual
CLI backup without that option does not initialise monitoring). Test failure, stale/missing receipt, missing checker heartbeat and delivered
email/recovery without altering real records; explicitly enable only backup and
health timers after those checks. No SQL roles/credentials or instance IAM changes
are proposed. No PostgreSQL/vector reinstall or schema migration is needed.

Review the lifecycle shortening separately (or retain existing 30+30 initially).
Any Terraform execution needs a fresh plan excluding the known protected instance
replacement; do not apply the root stack blindly, remove guards, use `-target` or
hide drift in state. No new AWS action is authorised by this document. Final source
validation, history acceptance, writer freeze/C selection, real import/reconciliation/
restore, production access and single-writer GO remain separate gates. Git remains
live; **production NO-GO** until those gates close.

## Local validation, 26 October 2026

- **252 offline tests passed on each Python 3.11.16 / 3.12.14 / 3.13.12**, no skips.
  Seven new receipt/health tests cover incomplete/corrupt uploads, last-good atomic
  preservation, private files/symlinks, timestamps, redaction and missing heartbeat
  configuration. Existing corruption/version/security checks remain.
- **141 disposable PostgreSQL tests passed**, no skips, PostgreSQL 17.11/vector
  0.8.7, Python 3.12.14/psycopg 3.2.13. Includes real custom dump/fresh restore and
  receipt validation of an actual archive after simulated S3 receipts, plus
  independent vector-aware restoration and cached offline model tests.
- **19 deployment/protection tests and five Terraform mock tests passed**; real
  Linux permissions, replacement/deletion refusals and the proposed retention
  assertion remain intact. Backend-disabled init/format/validate used an isolated
  copy, never the S3 backend or AWS. A generated private backend override first
  needed formatting; no operational Terraform/state was changed.
- Both schedules parsed with `systemd-analyze calendar`; Ruff F, documentation
  lint/links and `git diff --check` passed. Commands: `PYTHONPATH=src <python> -m
  unittest discover -s tests -v`; `HF_HUB_OFFLINE=1 JOBWATCH_REAL_MODEL_TEST=1
  HF_HOME=<existing-local-cache> PYTHON=<model-venv>/bin/python bash
  scripts/test_postgres.sh`; `<python> -m unittest discover -s infra/aws/tests
  -p 'test_*.py' -v` with the existing verified CLI test archive.

The initial minimal Python environment lacked the optional MCP runtime and
skipped two tests; the final runs above used the existing complete environments
and skipped none. These are local/disposable results, not deployed monitoring,
SNS email delivery or a fresh AWS inspection. No AWS credentials or project APIs
were used; public documentation/pricing downloads only. Private logs stay outside Git.
