# AWS backup and monitoring activation

**Historical activation evidence.** The later [production handover](AWS_PRODUCTION_HANDOVER.md)
records real-data independent restoration and the completed single-writer switch.
The empty-database/NO-GO statements below describe the earlier activation only.

26 October 2026. Installed application: `19ee9338ae902423e69d01d6eeb6f0f00bceaec2`.
**Production NO-GO. Git remains authoritative.** Backup and backup-health timers
are enabled; PostgreSQL collection remains disabled. Intended domain tables are
still empty. This approval did not include production import or cutover.

Subsequent [26 October code-only maintenance](AWS_RIVERLANE_MAINTENANCE_REPORT.md)
installed Riverlane release `e3807a0` and restored both backup schedules. This report
retains the original activation evidence; it is not a first-midnight-backup claim.

## Installed-service qualification — 26 October, 20:01 UTC

**Pre-cutover backup qualification PASSED.** The owner withdrew the requirement
to await the first midnight run and approved this immediate end-to-end test.
Production remains NO-GO pending the consolidated freeze/import/switch decision.

| Check | Actual result |
|---|---|
| Preconditions | Same approved project/host and encrypted data-volume UUID; release `e3807a0`, PostgreSQL 17.11/vector 0.8.7, unchanged migrations 0001–0004 and security. All twelve intended domain/audit/embedding tables empty; no backup or other DB transaction in flight. Installed unit bytes match reviewed source with no drop-ins. |
| Actual systemd path | One uniquely named runtime-only timer, `OnActiveSec=5s`, invoked **the existing `jobwatch-backup.service` exactly once**. No alternate backup script or transient replacement service. Its existing OS user/group, peer migration identity, hardening, configuration, S3 upload, manifest and receipt paths were retained. Journal UID/invocation and service exit 0 confirmed. |
| Timing | Test 20:01:10–20:01:21 UTC. Conservative backup start **20:01:16.457510 UTC**; completed receipt **20:01:18.918822 UTC**. Service's redacted elapsed time: three seconds. |
| S3 and receipt | Fresh **97,567-byte format-2 custom archive**, manifest-last completion, new exact archive/manifest versions, AES256, S3/local SHA-256, size and manifest-to-archive-version linkage verified. Operator retrieved both exact versions. Receipt is valid/fresh and reflects this service invocation. Older versions preserved; 30+30 retention unchanged. |
| Independent recovery | Downloaded archive restored using pinned `e3807a0` recovery code into a new local **network-disabled** PostgreSQL 17.11/vector 0.8.7 target. Exact rows, schema, owners/grants, migration/security fingerprints, extension requirements and sequences matched. Corrupt-archive and populated-target refusal passed. Restore verification took **0.493 seconds for the empty archive**, not a production RTO. Only the verified disposable local target was removed. No EC2 DB connection during restore. |
| Monitoring | Actual installed health service succeeded. CloudWatch received the backup exit-0 and fresh healthy-check events; a new `BackupHealthy=1` metric point was verified. All four alarms OK with notification actions enabled. Earlier delivered ALARM/recovery emails remain the notification proof; no new email was expected while alarms stayed OK. |
| Cleanup and final state | Temporary timer stopped, runtime file removed and unit unloaded. Both permanent timers remained enabled/active throughout; backup next **27 October 00:00 UTC**, health every fifteen minutes. Permanent unit bytes unchanged. Collection disabled/inactive; intended DB still empty; HBA/roles/grants/schema/mount/release unchanged; PostgreSQL not restarted. |

This proves an actual timer-driven execution of the installed service, **not the
27 October midnight run**. That future run is left to ordinary monitoring and is
no longer a cutover gate under the owner's revised decision. The test adds only
ordinary tiny S3/log/request usage, no infrastructure or recurring service.
Private receipts retain invocation details, exact versions and hashes; no dump,
credential or resource identifier is committed. No production import, source
request, collector activation, workflow change or Terraform operation occurred.

## Executed and verified

| Operation | Observed result |
|---|---|
| Baseline/final inspection | Same protected encrypted EBS mount, PostgreSQL 17.11/vector 0.8.7, migrations 0001–0004, roles, grants, audit triggers and authentication. All twelve intended domain/audit/embedding tables remain empty. No PostgreSQL restart, package reinstall or migration. |
| Code-only installation | Built, verified, installed and safely replayed release `19ee933`. Eight command-help checks and imports as all four OS service identities passed. Root-owned code is not writable by runtime users; artifact contains no production data, secrets or model weights. |
| Monitoring | One SNS Standard topic, its constrained CloudWatch publish policy, one confirmed email subscription, one health metric filter and one health alarm. Added notification actions to the three existing host/credit/failure alarms without changing their evaluation settings. Existing instance IAM suffices; no SQL-role or IAM-role changes. Logs retain fourteen days. |
| Terraform ownership | Imported the exact existing topic, policy attribute, subscription, filter and health alarm with protected S3/native locking. State now has **25 managed entries**, including the unchanged original twenty. No Terraform apply, instance replacement or protection removal. |
| Email | Owner confirmed subscription and receipt of **all three actual ALARM emails**: operation failure, stale backup health and missing checker heartbeat. AWS action history also records successful SNS actions. Labelled baseline OK emails were not counted as ALARM tests. |
| Manual backup | One real format-2 custom archive of the still-empty intended database: **97,567 bytes**. Archive and completion manifest uploaded with AES256 using the backup identity; exact S3 versions, checksums, manifest-last linkage and private atomic completion receipt verified. |
| Independent restoration | Operator retrieved the exact versions. Pinned recovery code restored into fresh, network-isolated local PostgreSQL **17.11/vector 0.8.7**, without querying EC2. Exact rows, schema, owners/grants, extension requirements and sequence fingerprints matched. Populated-target refusal passed. Restore/verification took about **0.59 seconds** for this empty archive, not a production RTO. |
| Scheduling | Backup timer enabled/active at **00:00 and 12:00 UTC**; health timer enabled/active every fifteen minutes. Actual scheduled health execution at **12:45:13 UTC** completed successfully and CloudWatch received `BackupHealthy=1`. Collection timer remains disabled/inactive and its service inactive. |
| First scheduled backup | **Not yet observed.** At the final host check its next execution was **27 October 2026, 00:00 UTC** and no timer trigger had occurred. Manual success is not scheduled-execution proof. Check the actual archive, manifest and receipt after that run. |
| Retention/security | Existing **30-day current / 30-day noncurrent** lifecycle unchanged, with versioning, SSE-S3, public-access blocking and ACL-disabled ownership. Encrypted volumes, termination protection, zero inbound rules, IMDSv2 and Standard CPU credits remain. |

Installed artifact SHA-256:

```text
3ddb7de922a03e05add2d0e2eee124c2be4f0220a21a7819216f89692239ce94
```

Private receipts retain exact S3 object/version links, checksums, test timestamps
and protected state history. No address, infrastructure identifier, receipt, dump,
private record or credential is committed here.

## How the notification tests worked

- **Operation failure:** the installed backup command refused an invalid option
  combination before database/S3 work. Its nonzero redacted result went through
  the real non-root log publisher and existing failure metric/alarm.
- **Stale backup:** the installed checker evaluated a separate synthetic receipt
  older than fourteen hours and published unhealthy status. The real last-good
  completion receipt was never replaced by test data.
- **Missing heartbeat:** temporarily selected an intentionally unpublished metric
  on the same health alarm, retaining its 900-second/two-of-three/missing-breaching
  evaluation. Real missing-data evaluation generated ALARM. The final
  `BackupHealthy` configuration was restored and verified before activation.

The two health cases used clearly labelled forced **OK baselines**, then required
actual metric-driven ALARM transitions and delivered emails. No forced ALARM was
counted as evidence. After real healthy heartbeats, the health alarm returned to
**OK through normal evaluation at 12:56 UTC**; the operation-failure alarm had also
recovered. This was not a forced reset. CloudWatch can retain a test ALARM while
older/missing periods remain in its evaluation range; a successful checker alone
does not establish recovery. SNS action history confirms the recovery action;
the owner also confirmed receipt of the final recovery **OK email**, separately
from the synthetic baselines, as well as all three required ALARM emails.

## Execution history and infrastructure review

The first attempt paused after a new mock-policy assertion could not evaluate
unknown computed ARNs. Its command chain had not gated AWS configuration on that
new result: an execution sequencing error. Mock-only explicit dummy ARN overrides
fixed the test without weakening policy checks. The owner subsequently instructed
resume, confirmed the subscription, and acknowledged the three ALARM emails.

A read-only refresh initially flagged more fields than its narrow alarm-only
allowlist. All **25 semantic comparisons** resolved them as the approved alarm
actions, empty-map normalization and existing separately managed EBS attachment,
IAM inline policy and S3 policy/versioning/30+30 lifecycle appearing in parent
resource projections. No unexpected infrastructure change was found and no refresh
plan was applied. The known source user-data replacement remains blocked by
`prevent_destroy`; this activation did not resolve or bypass it. A future root
Terraform update needs a separate reviewed maintenance plan, never blind apply.

## Validation and costs

- Final read-only audit: **40 checks passed** across host, EBS, security group,
  IAM, S3, notification setup, database metadata and timer state. Protected S3
  backend verified encrypted/versioned, default workspace, `use_lockfile=true`,
  lock released and state unchanged since the five imports.
- This resume reran **7 receipt/monitor tests** and **8 deployment tests**, all
  passing, using `PYTHONPATH=src python3 -m unittest discover -s tests -p
  test_backup_monitor.py -v` and the same command with `test_aws_deployment.py`.
  Documentation lint, 53 relative link targets and `git diff --check` passed.
- Five Terraform mock tests, format/validate and both persistent-resource
  destruction-refusal tests passed during this activation's local preparation.
- Earlier approved policy validation remains **252 offline tests on each Python
  3.11/3.12/3.13**, **141 PostgreSQL tests** and **19 deployment/protection tests**.
  These full suites were not repeated for this status-only documentation update.
- Hosted checks above exercise the installed application, non-root services,
  actual SNS/CloudWatch delivery, S3 uploads/version reads, guarded independent
  restore and one actual scheduled health run. They do not prove a scheduled
  backup, production-data recovery or production-scale load.

No new compute, storage bucket, instance IAM permission or outside provider.
Retaining 30+30, twice-daily **10–100 MiB** archives plus the new metric/alarm and
small SNS/log/request usage are estimated at **$0.43–$0.68/month** marginal
(illustrative £0.40–£0.63 with 20% VAT at £0.77/$). At 1 GiB/archive, about
$3.26/month. This is an estimate, not a verified invoice or credit entitlement;
the historical £17.53 base-host estimate and retained Aurora snapshot are separate.
[Rates and accepted risks](AWS_BACKUP_POLICY.md).

## Remaining gates

The installed-service qualification above closes the revised pre-cutover backup
gate; the first ordinary midnight run remains a normal monitoring check, not an
approval prerequisite. Maintain monthly and pre-change restore checks.
Twelve hours is frequency, **not a maximum RPO**. Missing/failed
runs, AWS project lockout and regional failure can cause greater loss. Notifications
are state changes, not repeated reminders or a guarantee.

The next decision is **one consolidated GO** covering H1/H2, the disclosed failed
GitHub push, writer freeze/final C, real import/reconciliation/independent restore,
genuine checks/activation against imported sources and the controlled single-writer
switch. All nine bounded public checks already pass; do not refetch them during
preparation. Routine execution substeps need no repeated approval after GO while
checks pass. Real-data ChatGPT/Proton sharing remain separate. Git stays authoritative.
