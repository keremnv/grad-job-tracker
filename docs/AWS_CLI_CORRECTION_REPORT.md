# AWS CLI service-user permissions correction

Base `f96211e1443f51e1490028ea91eb5a5efa83046e`, branch
`feat/aws-deployment-preflight`. Application release remains
`1b72d0d76f1ed15b56859bc891af9953458f85cd`; no new release is required.

## Correction

The existing signed AWS CLI installation inherits bootstrap's `umask 027`:
root-owned code directories/executables become 0750 and ordinary files 0640.
Non-root services cannot traverse/execute it. `bootstrap_host.sh` now validates
and normalizes only `/usr/local/aws-cli`, after the version/install condition,
so both a first install and an already matching 2.38.5 installation are covered.

Validation precedes all chmod operations: fixed root/version layout, root:root
ownership, no writable/special-permission files, no special files or regular-file
hard links, and no dangling or escaping symlinks. Internal official CLI links
are validated but never chmodded or traversed while walking. Unexpected owners,
layout or unsafe contents fail closed. Files are opened with O_NOFOLLOW and
checked against the validated inode/device/mode/owner before fchmod. Directories
and executable files become 0755; ordinary files 0644. Each regular-file SHA-256
is checked before/after. No ownership, contents, private configuration, credential,
PostgreSQL, SSM or unrelated /usr/local path is modified. All four non-root OS
identities must subsequently pass the installed CLI's `--version`.

## Local tests executed

```sh
# Official pinned x86_64 ZIP and detached signature downloaded privately from
# https://awscli.amazonaws.com/awscli-exe-linux-x86_64-2.38.5.zip[.sig]
JOBWATCH_CLI_TEST_ARCHIVE=/tmp/jobwatch-cli-permission-test/aws.zip python3 -m unittest discover -s infra/aws/tests -p test_cli_permissions.py -v
python3 -m unittest discover -s infra/aws/tests -p test_installer_permissions.py -v
python3 -m unittest discover -s infra/aws/tests -p test_protections.py -v
PYTHONPATH=src /tmp/jobwatch-ops-py311/bin/python -m unittest discover -s tests -v
PYTHONPATH=src /tmp/jobwatch-ops-py312/bin/python -m unittest discover -s tests -v
PYTHONPATH=src /tmp/jobwatch-ops-venv/bin/python -m unittest discover -s tests -v
PYTHON=/tmp/jobwatch-ops-py312/bin/python scripts/test_postgres.sh --rehearse-import "$PWD" 90434ea6d9a3457f9c8ba40f01ad25fa2ceffca3 "$PWD/../.local-reports/aws-cli-repair-4b2c-rehearsal"
# Existing private initialized cache; no live plan/apply or backend state mutation.
TF_DATA_DIR=/PRIVATE/EXISTING_CACHE terraform -chdir=infra/aws fmt -check -recursive
TF_DATA_DIR=/PRIVATE/EXISTING_CACHE terraform -chdir=infra/aws validate
TF_DATA_DIR=/PRIVATE/EXISTING_CACHE terraform -chdir=infra/aws test -no-color
bash -n infra/aws/scripts/bootstrap_host.sh
uvx ruff check --select F infra/aws/tests/test_cli_permissions.py
python3 -m compileall -q infra/aws/tests/test_cli_permissions.py
git diff --check
```

The eight new Docker tests verify the detached signature with the committed key
before executing the real official x86_64 CLI installer. They use the exact
production normalization fragment, actual root/non-root UIDs and setpriv
NoNewPrivileges; network and AWS credentials are absent. First/matching-version
repeat under umasks 0022/0027, all four users, actual write-open denial, unchanged
hashes/private files, and unsafe ownership/link/hard-link/type/layout/writable
refusals pass. The restrictive case reproduces execution denial before repair.
This is real Linux/x86_64 CLI testing; hosted ARM64 proof is recorded separately.

Existing results: 211 offline tests on Python 3.11.16, 3.12.14 and 3.13.12;
105 PostgreSQL tests plus frozen-import/repeat/real restore rehearsal;
6 installer permission tests; 2 destruction-plan refusals; 5 Terraform mock tests;
format/validate and static checks. PostgreSQL 17.11 / psycopg 3.2.13. All pass.

## Hosted maintenance and Terraform

The source correction is committed/tested before scoped host repair. Private
before/after inventories and hosted results belong in the qualification report;
this source report alone does not claim a completed repair or restore.

For the existing host, execute only the reviewed normalization fragment after
comparing the full installed inventory/hashes to the preserved preflight. Never
rerun cloud-init or the complete bootstrap, reinstall the CLI, change group
membership or deploy another application release to repair permissions.

Changing this bootstrap changes Terraform EC2 user-data. With
user_data_replace_on_change, a future live plan may propose a **protected
instance replacement**. No such plan is applied; prevent_destroy/termination
protections and the remote state remain untouched. In-place permission
maintenance is explicitly recorded, not hidden by state edits or lifecycle
ignore rules. A future separately approved new/recovery host uses the corrected
bootstrap; it is not permission to replace this instance.
