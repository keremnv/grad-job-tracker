# Stage 4B.2c — blocked before application installation

**Follow-up:** the installer correction and real-user regression tests are now implemented in [AWS_INSTALLER_CORRECTION_REPORT.md](AWS_INSTALLER_CORRECTION_REPORT.md). This document preserves the earlier blocked attempt and its evidence. Application installation, migrations and hosted recovery qualification remain pending; the helper-maintenance stage does not authorize them.

Checked 24 October 2026. Existing infrastructure/application source reference:
`afda366faabf0db3bf289d55f66fb3dac7445fe1`, branch
`feat/aws-deployment-preflight`. This status update changes documentation only.
No application, installer, database migration or Terraform code was changed.

## Actual AWS results

Read-only checks confirmed the intended project/profile and Stockholm Region,
unchanged S3 Terraform state with **20 managed resources**, a running ARM64
t4g.small with both EC2 health checks OK, encrypted attached root/data volumes,
zero inbound security-group rules and SSM Online. The application bucket was
empty. No unexpected infrastructure change was observed.

An authenticated SSM administrative session verified Ubuntu 24.04.5, Python
3.12.3, PostgreSQL 17.11 active as postgres:jobwatch-db, successful cloud-init,
and the exact database EBS serial/mount/UUID. PostgreSQL still uses the mounted
PGDATA. The database has no application schema or public application tables:
no job records, source runs, imported owner records or qualification fixtures.
The application release is absent. Both collector and backup timers remain
**not installed and inactive**. The inspection session was confirmed closed.

Initial idle-host checks showed available disk/memory capacity and zero
PostgreSQL service restarts. Exact measurements and resource identifiers remain
in private receipts. No application workload or stress qualification occurred.

Latest observed alarms: host status OK, operation failure OK, CPU-credit balance
ALARM. An additional read-only metric query failed with ExpiredToken; the cause
and current credit trend remain unverified. Renew the existing temporary AWS
login before further AWS work and recheck this alarm. No permission, spend-limit,
CPU-credit mode or instance-size change was attempted. CloudWatch log delivery
was not exercised.

## Local release and installer findings

The existing builder produced and hash-verified a code-only artifact from the
exact commit above: 71 manifest files, ARM64 CPython 3.12 dependencies including
psycopg 3.2.13, and all three migrations in both source and the application wheel.
Allowlist checks excluded production data, owner ledgers, configuration, secrets,
Git metadata and local reports. Its digest/manifest remain private. The artifact
was **not uploaded or installed**.

The reviewed installer creates staging with tempfile.TemporaryDirectory and
renames that directory to the permanent release path. TemporaryDirectory uses
mode 0700; rename preserves it. No subsequent operation adjusts that mode.
Consequently every non-root application identity is denied traversal, even when
the root-run install checks and a repeated installation report success.
`migrate_local.sh` would also fail because it switches to jobwatch-migrator
before invoking the release's migration command. A restrictive operator umask
can additionally leave code files and newly created parents inaccessible.

A disposable network-disabled Linux container executed the actual installer
filesystem paths as root, using the actual validated release archive and real
jobwatch-collector/owner/migrator/backup OS users. Under umasks 0022 and 0077,
both first and repeat deployment retained a root-owned 0700 release; all four
users failed read/execute access. Architecture checks and native package/systemd
commands were stubbed for this Linux/amd64 permissions probe. Filesystem
permissions and UID-based access were real. This is not an ARM64 installation
or psycopg-import pass. The previous root-only replay tests missed this case.

## Proposed repair — not applied

The private patch explicitly grants read/traversal of the code-only release:
root-owned application parents/directories 0755, ordinary files 0644, executable
files 0755. It skips symlinks so venv links cannot change system-interpreter
permissions. It normalizes completed code before selecting the current link;
root ownership and absence of runtime write access are preserved. It does not
touch PostgreSQL configuration, credentials, data files or database grants.

The candidate passed the same real-OS access probe under both umasks and repeat
installation. All four service identities could read code and execute the venv
interpreter but could not write the release; a private configuration fixture
outside the release retained mode 0640. This candidate exists only in private
review artifacts, with its test program and explicit test limitations.

The owner's section 9 requires stopping for software defects rather than
silently changing the reviewed release. Resume requires approval for a minimal
installer correction and committed regression tests, a new reviewed code-only
artifact, and checksum-verified in-place replacement of the trusted host
installer at /opt/jobwatch-bootstrap/deploy_release.py. Updating only the
artifact leaves that installed helper unchanged. No Terraform apply or instance
replacement is required or authorized; a future cloud-init replacement proposal
must remain blocked by the existing protections.

## Work not performed

No AWS artifact upload, application installation, migrations, qualification or
restore databases, synthetic database records, S3 backup/restore, reboot, log
write, SQL-tunnel credential or timer action occurred. Migration checksums are
packaged and unchanged, but **no versions have been applied on AWS**.

Native application/psycopg execution, restricted schema access, evidence/audit
checks, real EC2-to-S3 backup and fresh-database restore, corruption rejection,
reboot persistence and operational-log delivery remain unqualified. A same-host
restore would still leave off-host disaster recovery and independent encrypted
backup custody as separate gates.

No new recurring infrastructure or S3 objects were introduced. The existing
reviewed monthly estimate is unchanged; resources continue running. Production
Git data/workflow, main, IAM/security-group policies, database credentials and
the Aurora snapshot were untouched. PostgreSQL remains non-authoritative.

Nine genuine endpoint checks, explicit historical-limitations acceptance,
final writer freeze/current Git snapshot, import/reconciliation, independent
restore, credentials and owner go/no-go remain production cutover gates.

Private build, host, permissions-probe and proposed-patch receipts are under
`.local-reports/aws-hosted-qualification-4b2c/`; the code-only archive is outside
the checkout as required by the builder. No private receipts or fixtures are
included in this documentation commit.
