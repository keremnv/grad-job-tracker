# Stage 4B.2b — bootstrap permission correction

Branch: `feat/aws-deployment-preflight`; base `a71c0d0c726b06d2e91d2348536381d5d934238b`.
No Terraform apply or tracker infrastructure creation/modification was performed.
The previous plan with SHA-256
`d5edebcccd6d2366576c288e4481b15cf3cd701412beea50a9cd5e27642a3ac4`
is superseded and must never be applied.

## Correction

Only two executable lines change, in `infra/aws/scripts/bootstrap_host.sh`:

- Assign all three PostgreSQL configuration files to `root:jobwatch-db` before
  setting mode `0640`. The real unit runs as `postgres`, primary group
  `jobwatch-db`. Applications can neither write the files nor replace them in
  the root-owned directory. No world access is introduced.
- Have the root shell open the unchanged `0600 root:root` role SQL through
  stdin redirection. `psql_admin` still runs as postgres with `-X` and
  `ON_ERROR_STOP=1`. First-run-only provisioning, refusal of partial roles and
  refusal of elevated database roles are unchanged. No SQL contents or
  credentials are printed.

Terraform configuration, IAM, migrations 0001–0003, collector, importer,
production data and the active workflow are unchanged. The reviewed instance,
storage, backup/state bucket protections and disabled timers are preserved.

## Regression tests actually executed

`tests/postgres/test_aws_bootstrap.py` adds ten tests to the existing verified
disposable-container harness. Its extra PostgreSQL 17 container has networking
disabled, no AWS credentials and only the provisioning SQL mounted read-only.
It extracts the actual OS-user/cluster/configuration and role-provisioning
fragments from the production bootstrap. It reads User, Group, runtime-directory
mode and ExecStart from the actual unit and runs the server under that identity.
It does not mock filesystem permissions or SQL execution.

Verified: all three configuration files readable by the service; application
identities unable to modify the files/directory; server startup with the actual
configuration; root-only SQL retained; correct peer identities and database-role
privileges; repeat bootstrap retaining a sentinel row, cluster identifier and
postmaster start time; safe refusal of missing SQL/configuration, partial roles,
unexpected PGDATA and a different major version; ON_ERROR_STOP and transactional
rollback of deliberately invalid role SQL without logging SQL contents.

Commands (private logs under `.local-reports/aws-bootstrap-correction-4b2b/`):

```sh
terraform -chdir=infra/aws init -input=false -lockfile=readonly -backend-config=/PRIVATE/production.s3.tfbackend
terraform -chdir=infra/aws fmt -check -recursive
terraform -chdir=infra/aws validate
terraform -chdir=infra/aws test -no-color
python3 -m unittest discover -s infra/aws/tests -p test_protections.py -v
PYTHONPATH=src /tmp/jobwatch-ops-py311/bin/python -m unittest discover -s tests -v
PYTHONPATH=src .local-reports/aws-bootstrap-correction-4b2b/venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=src /tmp/jobwatch-ops-venv/bin/python -m unittest discover -s tests -v
PYTHON=/tmp/jobwatch-ops-py312/bin/python scripts/test_postgres.sh --rehearse-import "$PWD" 90434ea6d9a3457f9c8ba40f01ad25fa2ceffca3 "$PWD/../.local-reports/aws-bootstrap-correction-4b2b-rehearsal"
uvx ruff check --select F infra/aws/scripts tests/test_aws_deployment.py tests/test_aws_state.py tests/postgres/test_aws_bootstrap.py
uvx --from shellcheck-py shellcheck infra/aws/scripts/*.sh scripts/test_aws_host.sh
bash -n infra/aws/scripts/bootstrap_host.sh
python3 -m compileall -q infra/aws/scripts tests/postgres/test_aws_bootstrap.py
git diff --check
```

Results: initialization/formatting/validation pass; **5 Terraform mock tests**
and **2 real local destruction-plan refusals** pass; **211 offline tests** pass
on each of Python **3.11.16, 3.12.14 and 3.13.12**. **105 PostgreSQL tests** pass
(previous 95 plus 10), including the actual backup/restore code with nonempty
synthetic owner records. PostgreSQL 17.11, psycopg 3.2.13/libpq 170006; a separate full
105-test run also passed with psycopg 3.3.6/libpq 180006 on Python 3.12.14.
The pinned PostgreSQL image is unchanged.

The frozen-production rehearsal imports **629 postings** into two independent
databases, then restores a real custom-format PostgreSQL backup into a third.
Source reconciliation, logical equality, schema/ownership/grants, exact sequence
state and runtime reads/denials all pass. The source was the explicitly named
old rehearsal commit, not a final production freeze or an AWS upload.

Shell/Python checks above pass. An additional ShellCheck invocation over the
unchanged `scripts/test_postgres.sh` reported its pre-existing SC2034 unused-loop
variable warning; it passes with only that warning excluded. The runner was not
modified to address an unrelated warning.

## Replacement-plan boundary

Generate the new plan only after committing the correction. Use the already
reviewed S3 backend/cache, default workspace, encryption and native locking;
verify identity/Stockholm, bucket safeguards, empty tracker state and lock
release. Preserve the original private variable values, AMI and provider lock.
Expected result remains **20 additions, zero changes, zero deletions**.

The exact corrected commit, new binary hash, lock-test receipts and plan JSON
belong in a private replacement-plan report, not in this pre-plan commit.
Obtain separate approval naming that commit and hash before apply. Keep the
approved checkout clean: a saved plan does not remove the need to verify the
bootstrap files used by its `file()` expressions.

No billable inventory changed. The prior reviewed estimate remains approximately
**£17.53/month** under its VAT/exchange-rate assumptions, plus existing Aurora
snapshot charges. Credits/expiry/eligibility and the spend limit remain separate
funding checks; credits are not a spending cap.

These local Linux/amd64 tests do not prove ARM64 Ubuntu boot, systemd sandboxing,
EBS mounts/persistence, SSM, hosted grants, logging or hosted restore. Those
remain post-apply acceptance checks. Import, secrets, ATS checks, timer
activation, workflow replacement and production cutover remain separately gated.
