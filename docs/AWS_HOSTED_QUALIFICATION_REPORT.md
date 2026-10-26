# Stage 4B.2c — synthetic hosted qualification complete

**26 October V1 follow-up:** [host maintenance and native vector/recovery results](AWS_V1_MAINTENANCE_REPORT.md)
now record release `43aab3b`, migrations 0001–0004 and vector 0.8.7. Intended domain
tables remain empty and timers disabled. The report below preserves the earlier
Stage 4B.2c evidence; production remains NO-GO.

Checked 25 October 2026, `feat/aws-deployment-preflight`. AWS CLI correction:
`7146de1f93a93d5b39c75bf541b448961423add0`. Installed application remains
`1b72d0d76f1ed15b56859bc891af9953458f85cd`; it was not reinstalled or upgraded.
The Git-backed production tracker remains authoritative. No production import,
ATS request, production credential, timer activation, workflow change or
Terraform apply occurred.

**Stage 4B.2d follow-up:** [independent local recovery](AWS_RECOVERY_READINESS_REPORT.md) and temporary-key encryption tests have now passed without the EC2 database. Durable owner-held custody and a restore of the future production import remain open. This document retains the narrower Stage 4B.2c hosted results.

## Permanent correction and scoped maintenance

[The correction report](AWS_CLI_CORRECTION_REPORT.md) documents the minimal
bootstrap change and executed tests. The signed CLI had inherited umask 027,
leaving root:root directories/binaries 0750 and ordinary files 0640. The new
validation/normalization fragment runs after both first and matching-version
installation, rejects unsafe layout/ownership/types/links/hard links/writable
contents before chmod, and changes only verified CLI code permissions.

The code/tests were committed and pushed before host maintenance. Authenticated
SSM verified the same instance, Region, protected encrypted EBS volume, original
release and existing synthetic databases. The preserved dump, manifest and HBA
hashes matched the preceding receipts. The AWS browser login was renewed using
the existing named profile; no permanent keys or database passwords were created.

Only the committed normalization fragment ran on the host. Before/after
inventories of **8,740 entries** prove unchanged UID/GID, regular-file hashes,
file sizes, link targets and inventory. Directories/executables are now 0755,
ordinary files 0644; root ownership and no group/world write are preserved.
Repeating the fragment changed nothing. All four real service identities executed
the native **AWS CLI 2.38.5** with **NoNewPrivileges**; jobwatch-backup also passed
an authenticated STS read using the existing instance role.

No CLI reinstall, cloud-init rerun, group-membership change, application release
installation, IAM/network change or infrastructure replacement was used.
Changing checked-in bootstrap changes Terraform user-data and may propose a
protected replacement. **Do not apply that proposal** to reconcile this repair.
Remote state and replacement safeguards are unchanged; a future separately
approved new/recovery host will use the corrected bootstrap.

## Existing application and migrations

The installed code-only artifact remains:

```text
source commit: 1b72d0d76f1ed15b56859bc891af9953458f85cd
archive SHA-256: 3e87cfcec9fd7143f31d5cb0f31cfbda4db391614ab2aac33a7fc538d7603876
```

Prior first/repeat ARM64 installation, manifest/root-code permissions, five
console commands and all four service identities passed. Python **3.12.3** and
psycopg **3.2.13** execute on ARM64 and were rechecked after reboot.
PostgreSQL is **17.11**. Existing migrations were not rerun unnecessarily;
unchanged ledger/schema survived backup, restoration and reboot:

| Version | File | SHA-256 |
|---|---|---|
| 1 | 0001_initial.sql | ccbaab4351d3d6d128a2a9f637dcf77ab0f8459b8ebe6066403a597356e6acd5 |
| 2 | 0002_collection_receipts.sql | 71bc9c2c5c42a66d68c0fbf75409d7cd358cafb587fdcd95196e7c540b058e5a |
| 3 | 0003_operational_views.sql | 34be63021b5ede06dff3c46977856fc4675fa35ee087bdfda24a7ba9db258f53 |

All four views remain available. The production-intended `jobwatch` database has
**three migration-ledger rows and zero rows in every domain table**: employers,
sources, collection_runs, postings, posting_versions, posting_events,
verifications, leads, applications, triage and owner_audit. No real or synthetic
job/owner records were inserted there.

## Preserved synthetic data and actual S3 backup

The existing synthetic source database was reused, with two postings, three
versions, one material event, one application, one triage record and four audit
rows; zero collection runs. Prior actual peer tests covered private-data
separation, immutable evidence, NUL-safe values, actor/login attribution, audit
rollback, BM25/FTS and daily/weekly report privacy. No setup/import/collection was
repeated to populate this test.

The **94,908-byte custom-format pg_dump archive and original manifest** matched
their previously recorded hashes. The original manifest was preserved privately
before the existing uploader added the archive receipt. No new dump of the empty
intended database was substituted.

The actual jobwatch-backup OS identity, under NoNewPrivileges, called the existing
upload implementation. Both PutObject requests succeeded with matching SHA-256
and nonempty S3 version receipts. The archive uploaded first, manifest last; the
completion manifest identifies the exact archive key/version/hash. Recovery
operator GetObject requests selected those exact versions and independently
confirmed **AES256**, S3 checksums, local SHA-256 and manifest linkage.

An actual backup-version download by the instance role was denied. Read-only IAM
simulation denied both DeleteObject and DeleteObjectVersion; **no destructive S3
API was called**. Existing live inline IAM policies match the preserved Terraform
state. No permissions were expanded and no backup version was deleted.

## Fresh recovery database restored and independently checked

The existing fresh, separate recovery database was used, with the source's
postgres database owner, grants, roles and search paths. Only narrowly scoped
temporary Unix-peer entries for the exact two qualification database names and
existing identities were added. No trust/TCP rule or credential was introduced.

The operator-retrieved archive/manifest bytes were transferred through the
authenticated SSM channel to a private migration-owned maintenance directory,
then hash-verified. Existing `database_backup.restore` ran as the actual
non-superuser migration identity, using real pg_restore with preserved ownership,
ACLs and a single transaction. No clean/reset/trigger bypass occurred.

The restored database equals the source and manifest for every application,
evidence, history, owner, audit and migration row; schema/functions/triggers and
ownership/grants/security fingerprints; and **every archived sequence's exact
last_value/is_called**. All views operate. Search and reports pass. Independent
actual collector/owner peer processes verified all-verdict research, private-data
denials, immutable-evidence denial and owner/audit access. Corrupted archives and
populated restore targets were refused without resetting a target.

The original HBA was restored **byte-for-byte**, validated and reloaded. The
synthetic source, restored database, original dump, retrieved copies and receipts
are retained privately; none is an authoritative production dataset.

One private test-driver call initially omitted its connection environment and
stopped before opening a database. Inspection confirmed the target remained
fresh; the corrected call resumed with the same bytes. Another read-only verifier
needed to handle null serials on unrelated block devices. No application defect,
software repair or database reset was required for either diagnostic correction.

## One reboot, persistence, logs and monitoring

After confirming zero active writers/prepared transactions and inactive services,
**one ordinary EC2 reboot** was requested and acknowledged. Boot ID changed; the
same instance returned with both EC2 health checks OK and SSM connected.
The original encrypted EBS volume serial and filesystem UUID match the prior
attachment/mount. PostgreSQL started using mounted PGDATA, with its mountpoint
precondition/RequiresMountsFor intact; no root-storage fallback occurred.

Pre/post-reboot hashes of every table in all three databases match, including the
schema-only intended database and both nonempty synthetic databases. Application
release, CLI inventory/hashes/permissions, original HBA, unset production role
passwords and original dump persist. No stop/start, replacement, detach, format
or destructive persistence experiment was performed.

The intended non-root collector identity, with NoNewPrivileges, emitted only a
redacted synthetic qualification message through the existing PutLogEvents
permission. The collector/operation runner was **not** invoked. The operator
independently read back that exact event. Log retention remains **14 days**.
All three existing alarms are **OK**. Latest sampled CPU-credit balance:
**266.82, rising**; CPU about **1.74%**. These datapoints precede the final check
by a few minutes and describe small qualification operations, not production
load. Alarm notification delivery/recipient setup was not tested.

After reboot: approximately **7.86 GiB root / 14.69 GiB data free**, **1.37 GiB
memory available** of 1.79 GiB, no swap, PostgreSQL service memory about **43.7 MiB**
with zero automatic service restarts. No memory/disk monitoring service was added;
production needs explicit capacity and backup-freshness checks.

Both timers are **installed, disabled and inactive**; both application services
remain inactive. All nine opened stage SSM sessions were confirmed closed.
The earlier expired-authentication attempt did not open a host session. Protected
S3 Terraform state object version remains unchanged with **20 managed resources**.

## Tests, costs and remaining gates

The correction reran and passed: **8** new signed-distribution/real-user CLI
permission tests, **211 offline tests on each of Python 3.11–3.13**, **105** local
PostgreSQL tests plus frozen-import/repeat/real restore rehearsal, **6** installer
permission tests, **2** destruction-plan protection tests, **5** Terraform mock
tests, fmt/validate, bash/Python syntax and Ruff F checks. Commands and versions
are recorded in the correction report. Hosted checks above used the actual
installed application and existing backup/restore implementation, not copied
reference logic. No live ATS transport was called.

No additional infrastructure or recurring capacity was created. The previous
reviewed **approximately £17.53/month** remains the infrastructure baseline,
not a new invoice forecast. Only small synthetic S3 storage/request usage was
added alongside the existing code artifact; Aurora snapshot charges remain
separate. Credits/expiry/coverage were not inferred or changed.

**This proves same-host fresh-database recovery, not off-host disaster recovery.**
A separate host/failure-domain recovery rehearsal, independently retained
secure backup custody, project-unavailability access, accepted RPO/restore time,
alert recipient/budget and backup-freshness/capacity operations remain gates.
Remote owner SQL access still requires separately approved credentials; none was
created here.

Next production stages require separate authorization: nine genuine endpoint
checks/coverage decisions, explicit legacy-evidence acceptance, the old writer
freeze and current authoritative Git commit, complete import/reconciliation,
independent pre-cutover backup/restore, secure owner access and owner go/no-go.
The existing Git-backed workflow/data, main, IAM/networking, storage protections
and Aurora snapshot were untouched. No authoritative PostgreSQL write or
production switch occurred.

Private receipts and exact resource/database/object identifiers remain under
`.local-reports/aws-cli-repair-4b2c/` and protected host maintenance directories.
No credentials, dumps, raw owner information or private receipts are committed.
