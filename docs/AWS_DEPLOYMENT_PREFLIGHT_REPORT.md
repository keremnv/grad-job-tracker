# Stage 4B.1 AWS deployment preflight — 24 October 2026

**Policy superseded, 26 October 2026:** [the current owner-approved policy](AWS_BACKUP_POLICY.md)
uses AWS-only 00:00/12:00 UTC S3 backups, not a strict maximum-loss guarantee.
External custody/worker and automatic writer fencing are not V1 requirements.
This document preserves historical test results/proposals, not current permission.

**Current status:** the subsequently approved 20-resource infrastructure was deployed at `afda366faabf0db3bf289d55f66fb3dac7445fe1`. Hosted application qualification is blocked before installation by the release-directory permission defect documented in [AWS_HOSTED_QUALIFICATION_REPORT.md](AWS_HOSTED_QUALIFICATION_REPORT.md). The remainder of this report preserves the original preflight results; its original “not deployed” status is historical.

Implemented on `feat/aws-deployment-preflight`, based on the exact Stage 4A commit `5aeab9d9cccebdfc4ea2526cef7640fc9b39edf1`. **Not deployed, not authoritative, no cutover.** The interrupted `feat/postgresql-deployment-preflight` Neon worktree was inspected: it was clean at the Stage 4A baseline, with no unfinished implementation to recover. Its useful generic operational/import/readiness/runbook work is preserved. No design branch was merged, and no production source/data/workflow was changed.

## Implemented files and resources

`infra/aws/{versions,variables,main,storage,iam,monitoring,outputs}.tf`, `.terraform.lock.hcl`, `terraform.tfvars.example`, `.gitignore`, `tests/safety.tftest.hcl`, `aws-cli-signing-key.asc`, and `README.md` are the complete infrastructure/configuration set. The public signing key is AWS's published CLI key, not a credential.

Executable operations live in `infra/aws/scripts/`:

- `bootstrap_host.sh`: early SSM setup; serial/UUID-checked EBS mounting; signed PGDG PostgreSQL 17 and Python 3.12; peer authentication and the **unchanged existing bootstrap SQL**; bounded journal/WAL settings; official pinned signed AWS CLI. No application migration/import/collection at boot.
- `build_release.py`, `deploy_release.py`, `fetch_release.sh`: explicit-commit, code-only artifacts; hash-locked ARM64 CPython 3.12 wheels; independent archive/inventory verification; root-owned releases and atomic current symlink; repeat deployment and active-writer refusal. Wrong architecture or conflicting/incomplete releases fail closed. No private Git deploy key, production data, password or AWS key in artifacts/cloud-init/state.
- `migrate_local.sh`: explicit Unix-peer migration operation using existing `jobwatch-db-migrate`. No migration on collection startup.
- `database_backup.py`: real exported-snapshot custom-format dump, exact table/schema/security fingerprints, archived sequence states, checksum/version-confirmed encrypted S3 uploads with manifest last, and verified fresh-target restoration. Database-level grants/owner, role flags/membership/settings and application ACLs are checked; cluster credentials are not dumped.
- `run_operation.py`: existing Stage 4A collector orchestration, bounded public status/counts including positive-only coverage, accurate nonzero outcomes and one CloudWatch stream. Raw provider/database errors and owner notes are not forwarded.
- `check_plan.py`: initial-plan resource/permission/storage/network checks; never authorizes deployment.

Four systemd files under `infra/aws/systemd/` install collector/backup oneshot services and daily timers. Both timers remain **disabled**. Restart backoff and start limits bound failures; services run as dedicated non-root users. No Git state commits or dual writes.

**Authenticated plan: 20 resources to add, 0 to change, 0 to destroy.** These are one EC2 instance, one independent EBS volume and attachment, one security group, one IAM role/profile/inline policy, one S3 bucket plus six ownership/encryption/versioning/access/lifecycle/policy resources, one log group/stream, three alarms and one log metric filter. Existing default VPC/subnet/route table and Canonical AMI are reads, not new resources. No NAT, endpoints, load balancer, RDS, paid support, customer-managed KMS key or new human-access role.

Instance: `t4g.small`, Stockholm, ARM64 Ubuntu 24.04 LTS, pinned reviewed AMI, Standard CPU credits, IMDSv2, public IPv4 for outbound connectivity, no key pair and zero inbound ports. PostgreSQL uses loopback/socket only. Root is 12 GiB encrypted gp3; database is independent 16 GiB encrypted gp3. Instance, data volume and bucket have `prevent_destroy`; instance termination protection, final-volume snapshot and non-forced stopped detachment add safeguards. Mount/initialization checks prevent silently falling back to root storage or reformatting existing data. Replacements/destruction need an explicit reviewed backup/freeze procedure.

## Executed verification

Local versions: Terraform **1.17.0**, locked AWS provider **6.70.0**, AWS CLI **2.38.5**, Session Manager plugin **1.2.896.0**, psycopg **3.2.13**, libpq **170006**. Terraform and plugin downloads/signatures were verified against official published signing keys. Existing browser-authenticated profile `grad-jobwatch` and resource Region `eu-north-1` work. The configured Codex AWS MCP service in `us-east-1` passed a fresh read-only identity/plan call; resource Region was not changed.

Commands actually executed, from this worktree:

```sh
terraform -chdir=infra/aws init -backend=false -input=false
terraform -chdir=infra/aws fmt -check -recursive
terraform -chdir=infra/aws validate
terraform -chdir=infra/aws test
terraform -chdir=infra/aws plan -input=false -var-file=/PRIVATE/aws.tfvars.json -out=/PRIVATE/aws.tfplan
terraform -chdir=infra/aws show -json /PRIVATE/aws.tfplan > /PRIVATE/aws-plan.json
python3 infra/aws/scripts/check_plan.py /PRIVATE/aws-plan.json
uvx iam-policy-autopilot@latest generate-policies /PRIVATE/aws-plan.json --region eu-north-1 --account PROJECT_ID --pretty
uvx --from shellcheck-py shellcheck infra/aws/scripts/*.sh scripts/test_aws_host.sh
uvx ruff check --select F infra/aws/scripts tests/test_aws_deployment.py tests/postgres/test_aws_backup.py
python3 -m compileall -q infra/aws/scripts tests/test_aws_deployment.py tests/postgres/test_aws_backup.py
scripts/test_aws_host.sh
```

Formatting, initialization, validation, **five Terraform mock tests**, shell/Python static checks and the real authenticated read-only plan pass. No `apply`, import-to-state, provisioner or resource mutation was executed. IAM Policy Autopilot generated a private deployment-permission review baseline successfully; no policy was attached. It is not an authorization grant or a reason to reuse broad managed project access indefinitely.

The real default subnet inherits its main route table implicitly. The first plan exposed this; the configuration now resolves explicit association or main-table fallback and requires an Internet Gateway default route. The first Ubuntu probe also exposed an unavailable distribution `awscli` package; bootstrap now uses the official pinned **signature-verified** installer instead. AWS CLI runs directly inside `NoNewPrivileges` services, avoiding a privileged Snap CLI launcher.

Offline suites ran with Python **3.11.16, 3.12.14 and 3.13.12**:

```sh
PYTHONPATH=src /tmp/jobwatch-ops-py311/bin/python -m unittest discover -s tests -v
PYTHONPATH=src /tmp/jobwatch-ops-py312/bin/python -m unittest discover -s tests -v
PYTHONPATH=src /tmp/jobwatch-ops-venv/bin/python -m unittest discover -s tests -v
```

**203 tests passed on each version:** the existing 195 plus eight deployment tests. These exercise artifact corruption/traversal/identity/host-architecture rejection, active-writer refusal, actual installation filesystem/symlink replay with mocked host commands and preserved data marker, encrypted/checksummed S3 receipt handling with mocked AWS calls, and redacted partial-failure/positive-only output. They do not pretend to run ARM64 EC2/systemd.

Disposable PostgreSQL suites:

```sh
PYTHON=/tmp/jobwatch-ops-py311/bin/python scripts/test_postgres.sh
PYTHON=/tmp/jobwatch-ops-py312/bin/python scripts/test_postgres.sh
PYTHON=/tmp/jobwatch-ops-venv/bin/python scripts/test_postgres.sh
PYTHON=/tmp/jobwatch-ops-py312/bin/python scripts/test_postgres.sh --rehearse-import \
  ../grad-job-tracker 90434ea6d9a3457f9c8ba40f01ad25fa2ceffca3 /PRIVATE/new-rehearsal-directory
```

**95 PostgreSQL tests passed on each version:** existing 94 plus the actual new production backup/restore code with nonempty private owner fixtures, exact data/ACL/history/sequence proof, corrupted-archive rejection and populated-target refusal. The final backup-security refinements were rerun on Python 3.12. Tests verify their own labelled loopback Docker targets and ignore arbitrary caller database URLs. Image: `postgres@sha256:858bb5e98eed02eb8cafbcdc007f9a65d1b613972028944a8ec2133ccaf80d27`, PostgreSQL **17.11**.

The full Stage 3 rehearsal also passed: two fresh deterministic imports and a real custom-format backup/restore, independently reconciled against the frozen Git source. Counts: **629 postings, 44 employers, 9 sources, 34 leads, 24 verifications, 69 imported runs, 1,008 versions and 107 events**. Real owner ledgers remain empty; nonempty synthetic ledgers are tested separately. All 629 current descriptions were available; 537 distinct texts included two historical-only texts, and 57 native versions covered 54 postings. No recorded evidence reference was unresolved for this old rehearsal. This does not establish equivalence to current production main or repair the two known historical limitations.

`scripts/test_aws_host.sh` passed in a pinned local Ubuntu 24.04 container: **Python 3.12.3, PGDG PostgreSQL 17.11**, actual native server start/query/stop, signed AWS CLI 2.38.5 installation, and signed repository availability of ARM64 PostgreSQL server/client packages. The probe uses host networking to reach the developer machine's DNS, but the database is socket-only. This is **x86_64 execution plus ARM64 package verification**, not an ARM64 EC2 boot, EBS mount, SSM session or hosted restore. Those remain external gates.

The real explicit-commit code artifact build and full manifest verification passed; ARM64 CPython 3.12 binary wheels and all three unchanged packaged SQL migrations are included. Artifacts remain private/local and were not uploaded.

## Cost of the actual default configuration

Official Price List API rechecked Stockholm On-Demand shared Linux `t4g.small` at **$0.0172/hour**, gp3 at **$0.0836/GiB-month**, standard CloudWatch alarms at **$0.10/month** and first-tier custom metrics at **$0.30/month**. Public IPv4 is **$0.005/hour**. No free-tier/credit discount is assumed. [EC2](https://aws.amazon.com/ec2/pricing/on-demand/), [EBS](https://aws.amazon.com/ebs/pricing/), [IPv4](https://aws.amazon.com/vpc/pricing/), [CloudWatch](https://aws.amazon.com/cloudwatch/pricing/), [S3](https://aws.amazon.com/s3/pricing/).

| Item | Monthly USD at 730 hours |
|---|---:|
| t4g.small | 12.5560 |
| 28 GiB gp3 total | 2.3408 |
| Public IPv4 | 3.6500 |
| 1 GiB S3 storage allowance | 0.0230 |
| Backup request allowance | 0.0200 |
| Three standard alarms | 0.3000 |
| Failure custom metric if emitted | 0.3000 |
| Light log ingestion/storage allowance | 0.1000 |
| Small transfer contingency | 0.2000 |
| **Total before tax/credits** | **19.4898** |

Calculated with Decimal and the [ECB 23 October reference cross-rate](https://www.ecb.europa.eu/stats/policy_and_exchange_rates/euro_reference_exchange_rates/html/index.en.html), £0.74803895/USD: **£14.58 before VAT; £17.49 with a 20% VAT scenario**. At 744 hours the same scenario is **£17.77**. VAT applicability and actual billing FX remain unverified; these are planning conversions, not guaranteed invoices. Usage lines are allowances, not fixed tariffs. There are **no recurring EBS snapshots** in this configuration; an explicitly approved final snapshot costs extra until deliberately retired. Basic EC2 metrics are free; no detailed monitoring/paid notifications are enabled.

Costs can exceed £20 through currency/tax changes, growth/retained versions and detached disks/snapshots, maintenance/recovery-instance overlap, larger data volumes, verbose logs, unusual transfer, paid support or added services. Standard CPU mode prevents Unlimited surplus charges. The smallest adjustment is first removing a verified cost driver; `t4g.micro` halves compute but reduces RAM to 1 GiB and needs a separate memory/restore test. Do not sacrifice recoverability or silently change the preferred size for an unexplained billing forecast.

The paid project and aggregate credit balance were verified; expiry, eligible products and tax/credit treatment remain unknown. **Credits are not the operating budget or spending cap.** AWS Settings spend limits apply to pre-tax charges before credits and optional forecast controls can stop resources early. A spend-limit notification received during this work requires billing-service attribution before deployment; private project/billing details are deliberately not committed. Changing the limit does not authorize infrastructure deployment. [Official spend-limit behavior](https://docs.aws.amazon.com/accounts/latest/reference/create-spend-limit.html). Agent Toolkit itself has [no additional charge](https://aws.amazon.com/about-aws/whats-new/2026/05/aws-mcp-server/); paid underlying APIs/resources are separate. No paid Cost Explorer queries were used in this preflight.

## Security, recovery and remaining gates

The runtime instance profile has only minimal SSM registration/channel actions, release-code reads under `artifacts/`, AES256-required backup writes under `backups/`, and one operational log stream. It cannot read/delete private backups or access secret stores/IAM. Ordinary SQL agents use a restricted SSM port-forward document and PostgreSQL owner credentials; shell/deployment access is separate privileged administration. Existing SQL runtime boundaries/audit remain unchanged. Public signing keys, project/AMI identifiers and bucket names are not database credentials; none of the latter are embedded in Terraform.

Backups retain current and noncurrent S3 objects for 30 days, with TLS-only access, public blocking, SSE-S3 and versioning. The instance cannot recover its own private backups; an approved recovery operator selects archive versions. Real snapshot-consistent pg_dump archives, completion manifests and exact fresh-target restore checks are implemented and locally tested. This is not PITR/Object Lock/HA, and a compromised project administrator can destroy same-project backups. A separately secured encrypted offline copy, acceptable dump-only RPO, actual-host restore and recovery-custody proof remain mandatory.

The [AWS cutover procedure](AWS_CUTOVER.md) preserves all nine endpoint-check gates, separate employer association, owner acceptance of missing schema-2 native responses and never-indexed pre-schema-2 roles, final current-main freeze/commit selection, empty-target import/reconciliation, independent hosted restore, source activation, one writer and controlled first scan. Newer production commits must be integrated without replacing their data with the old rehearsal snapshot. Do not enable the alternative inert GitHub Actions collector when systemd is selected.

Remaining authorization/configuration: explicit apply/hosted-data approval, confirm selected Region in AWS Settings, review credit/spend-control/forecast details, scoped ongoing deployment and SQL-session permissions, secure Terraform-state/backup custody, owner credential setup, tested actual-host boot/mount/SSM/restore/log delivery, backup freshness/disk monitoring and approved alert recipient, nine genuine endpoint checks, legacy acceptance, final freeze/import/reconciliation and explicit owner GO. None is marked accepted by these tests.

**No AWS infrastructure was created, changed or destroyed. No hosted database, production secret, data upload, live ATS scan, enabled timer, active workflow change, main merge or production cutover occurred.** Application source, all three migrations, tracked production data/configuration, active workflow and main remain unchanged. Private plans, IAM baselines, raw pricing/billing/API receipts, dumps and reconciliation reports stay outside commits.
