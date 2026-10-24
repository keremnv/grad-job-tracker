# Stage 4B.2c — application installed; recovery qualification blocked

Checked 24 October 2026 on `feat/aws-deployment-preflight`. Installed release:
`1b72d0d76f1ed15b56859bc891af9953458f85cd`. This update changes documentation
only. Git-backed production remains authoritative. No live ATS request, real-data
import, production password, timer activation or Terraform apply occurred.

## Application and migrations verified on AWS

The intended project/profile and Stockholm Region, existing ARM64 t4g.small,
exact attached encrypted data EBS/mount, working SSM, zero inbound rules, live
inline IAM policies and corrected trusted installer matched prior receipts.
Terraform's 20-resource state and its S3 object version remained unchanged.

The exact existing code-only artifact was independently verified and uploaded
with SSE-S3, a matching S3 SHA-256 and object-version receipt:

```text
source commit: 1b72d0d76f1ed15b56859bc891af9953458f85cd
archive SHA-256: 3e87cfcec9fd7143f31d5cb0f31cfbda4db391614ab2aac33a7fc538d7603876
```

All 71 manifest files, member hashes, locked wheels, native CPython 3.12
AArch64 binaries and three migrations in source/application wheel passed.
Production data, owner records, private configuration, secrets and Git metadata
were excluded. The instance downloaded the artifact using its existing role.

First and repeated installation through the corrected trusted helper passed
on the real ARM64 host. The current release, completion digest and manifest
contents matched. All four actual service identities executed Python **3.12.3**,
psycopg **3.2.13** and application imports. All five console commands passed
`--help`: grad-job-watch, jobwatch-db-migrate, jobwatch-pg, jobwatch-pg-import
and jobwatch-pg-ops. Checks over 1,553 non-symlink code entries confirmed root
ownership and no group/world write access. No collection command was invoked.

The documented `migrate_local.sh`, using the actual migration Unix-peer
identity, applied unchanged migrations to PostgreSQL **17.11**. Repeating it
applied nothing and verified the ledger. Checksums:

| Version | File | SHA-256 |
|---|---|---|
| 1 | 0001_initial.sql | ccbaab4351d3d6d128a2a9f637dcf77ab0f8459b8ebe6066403a597356e6acd5 |
| 2 | 0002_collection_receipts.sql | 71bc9c2c5c42a66d68c0fbf75409d7cd358cafb587fdcd95196e7c540b058e5a |
| 3 | 0003_operational_views.sql | 34be63021b5ede06dff3c46977856fc4675fa35ee087bdfda24a7ba9db258f53 |

All four current SQL views exist. Final counts in the intended `jobwatch`
database are **zero in every domain table**: employers, sources, collection_runs,
postings, posting_versions, posting_events, verifications, leads, applications,
triage and owner_audit. Only the schema and three migration-ledger records exist.
This database contains neither imported production data nor synthetic fixtures.

## Isolated synthetic SQL tests passed

Two explicitly named qualification/recovery databases were created on the
existing cluster, owned by postgres with matching database grants/search paths.
Only synthetic records were inserted into the qualification database. The
recovery database remains fresh without an application schema.

Actual peer-authenticated non-root processes verified:

- Migration application/repeat and restricted migration-role flags.
- Collector denial of owner tables, audit reads and schema creation; owner denial
  of evidence updates, audit deletion, schema alteration and trigger disabling.
- Two retained all-verdict postings, three immutable evidence versions including
  NUL-bearing parsed values, one linked synthetic material event and zero runs.
- Nonempty synthetic application/triage records, required actor attribution,
  authenticated database login attribution, and transactional data/audit rollback.
- Current evidence inspection, BM25/FTS search, application precedence/conflicts,
  and daily/weekly reports that omit private synthetic notes/CV labels.

Temporary HBA entries allowed only the exact two named databases and existing
peer identities. The original HBA was backed up privately, then restored
**byte-for-byte**, validated and reloaded after the stop. Fresh qualification
connections through the removed entries were denied. Configuration remains
root:jobwatch-db `0640`; no trust/TCP/password changes were introduced.

## Real dump completed; S3 upload blocked

The existing `database_backup.backup` function ran as jobwatch-backup, mapping
to the restricted migration SQL role, against the synthetic database. Its
exported REPEATABLE READ snapshot, exact fingerprint, real `pg_dump` custom
archive and archived sequence inventory completed. The **94,908-byte** archive
and integrity manifest remain private on the host. No production data was read
into that dump.

The existing upload function failed **before launching AWS CLI**:
`PermissionError: [Errno 13] Permission denied: 'aws'`.
No backup archive or completion manifest reached S3. Final bucket inspection
found only the 5,094,550-byte code artifact; `backups/` remained empty.

The confirmed cause is `bootstrap_host.sh` running the signed AWS CLI installer
under `umask 027`. The installed `/usr/local/aws-cli` directories and executable
are root:root **0750**. `/usr/local/bin/aws` links into that inaccessible tree.
Root can execute the exact absolute CLI path; all four non-root service users
receive Permission denied on the same path. This is a filesystem defect,
not an IAM denial, expired login or PATH-only issue. It also blocks future
collector/backup operational-log delivery.

**Stopped at the owner's software-defect boundary.** No CLI permissions,
reviewed software, IAM policy or infrastructure was changed to bypass it.
The proposed minimal correction is to normalize only the root-owned AWS CLI
code tree to readable/traversable directories, executable binaries and readable
ordinary files, without following symlinks or touching private configuration.
Validate ownership and real non-root execution even when installation is skipped
because the version already matches. Add first/repeat, restrictive/ordinary
umask and NoNewPrivileges regression checks. A private, **unapplied** patch
records this proposal; no fix or regression pass is claimed.

An explicitly approved in-place CLI permission repair is needed on this host;
a cloud-init change must not be applied as an EC2 replacement. Do not rerun the
whole bootstrap or grant services root-group membership to work around this.

## Unfinished tests and current host status

S3 backup encryption/checksum/version and manifest-last receipts, runtime backup
read/delete denial, recovery-operator retrieval, fresh-database restore,
corrupt/archive and populated-target rejection remain **unexecuted**. No reboot
or reboot persistence test occurred. Actual CloudWatch log delivery and remote
SQL tunnelling remain untested. Even a later same-host database restore will not
complete off-host disaster recovery or independent backup custody.

Final read-only checks: all three existing alarms **OK**, CPU-credit balance
**43.12 and rising**, latest sampled CPU average about **1.70%**; log retention
**14 days**. Root/data free space about **7.88/14.69 GiB**, available memory about
**1.34 GiB** of 1.79 GiB, no swap. PostgreSQL active with zero service restarts
and about **86.5 MiB** current service memory. These are small-test observations,
not stress or steady-state production qualification. The exact mount/UUID was
rechecked; persistence across reboot remains unproven.

Both collection/backup timers are **installed, disabled and inactive**. Both
services are inactive. All 11 stage SSM sessions were confirmed terminated.
AWS authentication remained valid. Production role passwords remain unset.
The synthetic databases, private dump and failure diagnostics are preserved;
they must not be confused with production or silently discarded/reset.

A private verifier initially tried to parse raw psql boolean output as JSON.
It stopped before migrations/HBA reload. Inspection established the exact state;
corrected diagnostic queries resumed without repeating database creation or
overwriting the original HBA backup. This was a test-driver error, separate from
the confirmed installed CLI defect. No reviewed application change was needed.

No infrastructure/capacity, IAM, networking, spend limit, Terraform state,
production workflow, main or Aurora snapshot changes occurred. The prior reviewed
**approximately £17.53/month** estimate remains the infrastructure baseline,
not a fresh bill forecast. The small code object and upload/download requests add
storage/request usage; no new recurring resources were created. Existing Aurora
snapshot charges remain separate. Credit eligibility/expiry is not inferred.

## Next approval and production gates

First approve a minimal AWS CLI permission correction, regression tests and
scoped host repair. Then resume synthetic S3 backup/restore, logging and the
single unused reboot qualification, without reinitializing the migrated database.
The installed application/migrations need not be repeated to repair the CLI.

Subsequent separate gates remain: off-host restore/independent encrypted backup
custody and RPO, nine genuine endpoint checks/coverage decisions, explicit
legacy-evidence acceptance, final writer freeze and current Git snapshot,
complete import/reconciliation, secure owner access, and owner production
go/no-go. No live import, endpoint validation or authority switch is authorized
by these receipts.

Private receipts, dump hash, exact database/resource names and unapplied proposal
are under `.local-reports/aws-hosted-resume-4b2c/` and protected host maintenance
directories. They are excluded from Git. Earlier blocked attempts remain in Git
history and the prior private qualification/correction directories. No offline or
local Docker suites were rerun for this documentation-only update; the newly
executed checks above used the actual hosted application and database.
