# Stage 4B.2c — release installer permission correction

Base: `288ad5d1ce20e1b0c603d5f8d2ceaf227c78def5`, branch
`feat/aws-deployment-preflight`. This fixes code installation permissions only;
application domain code, migrations, build dependencies and Terraform are
unchanged. No application installation or migration is part of this stage.

## Fix

`deploy_release.py` now explicitly normalizes validated, completed code before
selecting the current release: application parents and directories `0755`,
ordinary files `0644`, executable files `0755`. Temporary staging can remain
private while extraction/install checks run. Ownership is retained; the real
installer runs as root, and service identities receive no write access.

Symlinked installation parents/releases are rejected before creating directories.
The code-tree traversal skips symlinks and uses lstat/no-follow chmod, so venv
links do not change system-interpreter or external-directory permissions.
Unexpected special files are refused. Incomplete or mismatched releases fail
before permission normalization. Existing archive/checksum/commit validation,
active-writer refusal, partial-install refusal and idempotence remain intact.
No PostgreSQL configuration, credentials, data or runtime grants are changed.

## Tests executed

```sh
python3 -m unittest discover -s infra/aws/tests -p test_installer_permissions.py -v
python3 -m unittest discover -s infra/aws/tests -p test_protections.py -v
PYTHONPATH=src /tmp/jobwatch-ops-py311/bin/python -m unittest discover -s tests -v
PYTHONPATH=src /tmp/jobwatch-ops-py312/bin/python -m unittest discover -s tests -v
PYTHONPATH=src /tmp/jobwatch-ops-venv/bin/python -m unittest discover -s tests -v
PYTHON=/tmp/jobwatch-ops-py312/bin/python scripts/test_postgres.sh --rehearse-import "$PWD" 90434ea6d9a3457f9c8ba40f01ad25fa2ceffca3 "$PWD/../.local-reports/aws-installer-correction-4b2c-rehearsal"
terraform -chdir=infra/aws fmt -check -recursive
terraform -chdir=infra/aws validate
terraform -chdir=infra/aws test -no-color
uvx ruff check --select F infra/aws/scripts/deploy_release.py infra/aws/tests/test_installer_permissions.py
python3 -m compileall -q infra/aws/scripts/deploy_release.py infra/aws/tests/test_installer_permissions.py
git diff --check
```

All pass: **211 offline tests** on Python 3.11.16, 3.12.14 and 3.13.12;
**105 disposable PostgreSQL tests**, plus the 629-posting frozen-import,
independent-repeat and real pg_dump/pg_restore rehearsal; **5 new Linux
permission tests plus 1 test-runner safety check**, **2 real local destruction-plan refusals**, **5 Terraform
mock tests**, formatting/validation and static checks. Database baseline:
PostgreSQL 17.11, psycopg 3.2.13, libpq 170006. No hosted SQL writes were used.

The five new tests run the actual installer in network-disabled Docker as root,
with real collector/owner/migrator/backup OS users, actual filesystem permissions
and a real Python 3.12 venv/interpreter. Package installation, systemd and the
ARM64 architecture guard are stubbed; this is Linux/amd64 permission proof,
not native ARM64 application qualification. The pinned official Python image
executed Python 3.12.15:
`python@sha256:6a0e06f5ba0b373d051aabd3fffb9b873f27f9a2a3a14bcbe37dee6f0e3bbc70`.

First and repeat installation under both 0022/0077 verify every intended identity
can run the interpreter/CLI and read code, while real write-open attempts fail.
All installed entries retain UID 0. Venv/system Python and external file/directory
symlink targets are unchanged; private configuration and data fixtures preserve
their modes/content. Additional tests reject symlinked parents/releases and
retain an incomplete release without loosening its permissions. Negative
controls with the original reviewed installer fail both umask regressions at
the original 0700 directory, confirming the tests detect the reported defect.

## AWS preflight and narrowly authorized maintenance

The existing temporary profile authenticated successfully; no browser login or
credential replacement was needed. The intended project/Stockholm instance,
protected data volume, working SSM and empty/unmigrated database were rechecked.
The original trusted helper matched the reviewed deployed bytes and root-only
0700 mode. No application release or timers were present. All three alarms were
OK; recent CPU-credit samples were rising and idle utilization was low.

Remote S3 state version/serial/resource values are unchanged. A strict exported
JSON comparison initially found reordered check_results; semantic comparison
and the unchanged S3 object version confirmed no actual state change. No apply,
live infrastructure plan or state modification was performed.

After committing/tests, build a fresh code-only archive from that exact commit
and verify its manifest/hash. Actual artifact digest and native helper-backup,
atomic-replacement and independent-readback receipts are recorded privately
under `.local-reports/aws-installer-correction-4b2c/`, after those operations.
Only `/opt/jobwatch-bootstrap/deploy_release.py` and its required private backup
may be written on the host in this stage; do not execute application deployment.

Maintenance must refuse unexpected current bytes/ownership/symlinks, preserve
the original in a root-only backup, verify the candidate against committed bytes,
atomically install root-owned 0700 code and independently verify readback.
An uncertain acknowledgement requires read-only investigation, never blind retry.
No IAM, networking, EC2 sizing, Terraform state, application or database change
is authorized. Cloud-init references this helper: future replacement proposals
must stay blocked by the existing resource protections, not applied as a repair.

## Remaining scope

Application installation, native ARM64 psycopg/console qualification, migrations,
synthetic databases, real hosted backup/restore, reboot/persistence and log
delivery still belong to the subsequent qualification stage. Timers remain
absent/inactive, PostgreSQL empty/unmigrated, and Git authoritative. There is no
new recurring infrastructure cost. Production import, endpoint checks, secrets,
legacy acceptance, final freeze/reconciliation and cutover remain separately gated.
