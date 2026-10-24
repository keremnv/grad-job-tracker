# Final AWS deployment-safety pass — 24 October 2026

Base: `feat/aws-deployment-preflight` at `7b16d88fdeab59b79eb953730b8ce600246062f2`. This follow-up changes only Terraform state preparation, its tests and deployment instructions. **No apply, AWS resource creation/change/deletion, credential configuration, private-data upload, live ATS check, timer activation, workflow switch or cutover was performed.** Existing application, database migrations, data-volume/backup controls and production files are preserved.

## Decision and changed files

The operational Terraform root now requires a separate S3 backend with `use_lockfile=true`, encryption and fixed Stockholm/profile/state-key settings in `infra/aws/versions.tf`. The state bucket is bootstrapped separately, outside the tracker state, so a tracker destroy cannot manage it. There is no recursive bootstrap state, DynamoDB table, KMS key or extra monitoring service.

- `infra/aws/state/README.md`: exact two-approval procedure, least-privilege access, bootstrap, state recovery and pre-bucket review.
- `infra/aws/state/{access-policy,bootstrap-policy,bucket-policy}.json.template` and `bootstrap.json.example`: complete reviewable policies/configuration with placeholders. Rendered actual project/bucket details stay private.
- `infra/aws/scripts/state_bucket.py`: local policy/config rendering; separately confirmed one-bucket bootstrap; read-only safeguard verification; backend-cache checks. It refuses wrong identity, an existing/inaccessible bucket or a policy appearing during creation. No silent overwrite/adoption/reset, object upload or policy attachment.
- `infra/aws/scripts/review_plan.py`: isolated initial local-backend **review-only** copy. The source keeps its S3 backend. No credentials/state/private tfvars are copied; only init/plan/show are available. The local binary plan is removed after inspection; JSON/logs remain reviewable.
- `infra/aws/tests/test_protections.py`: real Terraform destroy plans against fake local state and loopback-only SDK endpoints, proving both existing `prevent_destroy` guards reject deletion.
- `tests/test_aws_state.py`: policy, bootstrap refusal/verification, private rendering, backend and review-isolation tests. Existing deployment tests additionally verify enabled-timer refusal and no timer start/enable on actual deployment/replay command paths.
- `infra/aws/.gitignore`, `infra/aws/README.md`, `docs/AWS_CUTOVER.md`: private backend config handling and updated operational entry points.

The state bucket uses account-regional naming, versioning, SSE-S3, SSE-C blocking, verified default public blocking/disabled ACLs, TLS-only access and explicit denial of bucket deletion and state-object/version deletion. State versions have **no expiry**. The exact `.tflock` object remains deletable for normal unlock. Administrators can still deliberately replace policies; independently secured encrypted state exports remain required. State storage is distinct from PostgreSQL backups.

Normal backend access is bucket-scoped ListBucket, exact-state-key GetObject/PutObject and exact-lock-key GetObject/PutObject/DeleteObject. No bucket administration, state deletion, version deletion, other-workspace writes or backup reads. Bucket-scoped listing is intentional: Terraform 1.17 calls HeadObject for the initial absent state, and a prefix-only ListBucket condition can produce 403 rather than the required 404. This dedicated bucket holds only state/locks. [Terraform implementation](https://github.com/hashicorp/terraform/blob/v1.17.0/internal/backend/remote-state/s3/client.go), [S3 missing-object permissions](https://docs.aws.amazon.com/AmazonS3/latest/API/API_HeadObject.html).

These are policies for **named backend/bootstrap API operations**, checked against HashiCorp's backend documentation and AWS's S3 service authorization reference. They are separate from IAM Policy Autopilot's managed-resource deployment baseline, which was regenerated privately from the current plan. No role/policy was created, uploaded or attached. The EC2 instance profile has no state access. Actual SCP/RCP/hosted permissions and native S3 locking still require post-bootstrap verification; a simulation is not a hosted grant test.

## Inventory and cost

The fresh authenticated pre-bucket review still proposes **20 tracker resources, 0 changes, 0 deletions**. An independently approved bootstrap adds **one separate S3 state bucket**, plus its configuration/tags/policy. The bucket is intentionally not another resource inside the 20-resource tracker plan. Tracker compute, storage, IAM, network, logs and alarms are unchanged: `t4g.small`, 12 GiB encrypted gp3 root plus independent 16 GiB data, outbound IPv4, zero inbound ports, SSM, private backup bucket, basic monitoring. No RDS, NAT, load balancer, paid detailed monitoring or new human role.

Official Stockholm prices were rechecked through the Price List API: t4g.small $0.0172/hour, gp3 $0.0836/GiB-month, S3 Standard $0.023/GiB-month. The existing 730-hour estimate remains $19.4898 before tax/credits. Allow **an additional $0.043/month** for a conservative 1 GiB of retained state/lock versions plus $0.02 request allowance. No fixed per-bucket monthly fee, DynamoDB or KMS charge is introduced. New planning total: **$19.5328/month**.

At the same ECB 23 October reference conversion, this is **£14.61 before VAT; £17.53 with a 20% VAT scenario**, or £17.81 in a 744-hour month. The state allowance adds about **£0.04/month** in that scenario. Actual billing FX/VAT, usage growth, retained detached volumes/final snapshots, restore-host overlap and existing Aurora snapshot storage remain outside any guaranteed cap. Credits are not a budget and their eligibility/expiry is not assumed. [EC2](https://aws.amazon.com/ec2/pricing/on-demand/), [EBS](https://aws.amazon.com/ebs/pricing/), [S3](https://aws.amazon.com/s3/pricing/).

The remaining Aurora snapshot was inspected read-only. The current Stockholm Aurora backup tariff retrieved is **$0.02/GiB-month**; once a source cluster is deleted, retained manual snapshots can be billed at their full size. Metadata's zero allocated storage is **not** evidence of zero billed size; the bounded SnapshotStorageUsed metric lookup returned no datapoint. Exact ongoing cost therefore remains unknown, rather than folded into a passing budget estimate. Raw metadata/identifiers and receipts remain in ignored private reports. No snapshot was deleted, restored or modified. [Aurora storage accounting](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-storage-backup.html).

## Executed tests and checks

Terraform **1.17.0**, locked AWS provider **6.70.0**:

```sh
terraform -chdir=infra/aws init -backend=false -input=false
terraform -chdir=infra/aws fmt -check -recursive
terraform -chdir=infra/aws validate
terraform -chdir=infra/aws test
python3 -m unittest discover -s infra/aws/tests -p test_protections.py -v
python3 infra/aws/scripts/review_plan.py --vars /PRIVATE/aws.tfvars.json --output /PRIVATE/new-review
```

All pass: initialization/formatting/validation, **five provider-mock tests**, **two real local destroy-plan refusals**, and the authenticated 20-resource review plus the existing plan-security checker. The destruction fixtures contain dummy IDs/credentials with provider endpoints restricted to loopback; they perform no apply and cannot address live AWS resources. The operational S3 root is unchanged by the isolated review. No bucket is needed for validation/tests; actual S3 initialization/locking is deliberately unexecuted.

Read-only IAM `SimulateCustomPolicy` verified expected backend allows and denials, including absent-object-compatible bucket listing, state-write permission, state/version-deletion refusal, no bucket administration, and lock deletion. No state bucket or attached policy was needed. Local bootstrap tests mock all mutating APIs, including policy-race/public-control failures; they are not claims of hosted bootstrap success. CLI model skeletons verified the installed AWS CLI's account-regional namespace and SSE-C-blocking request shapes without an AWS write.

Offline tests, using the actual changed code:

```sh
PYTHONPATH=src /tmp/jobwatch-ops-py311/bin/python -m unittest discover -s tests -v
PYTHONPATH=src /tmp/jobwatch-ops-py312/bin/python -m unittest discover -s tests -v
PYTHONPATH=src /tmp/jobwatch-ops-venv/bin/python -m unittest discover -s tests -v
```

**211 passed on Python 3.11.16, 3.12.14 and 3.13.12** (previous 203 plus eight state tests); final policy/deployment assertions were also checked directly. `PYTHON=/tmp/jobwatch-ops-py312/bin/python scripts/test_postgres.sh` passed **all 95** disposable PostgreSQL tests, including actual backup/restore with synthetic owner records. PostgreSQL 17.11, psycopg 3.2.13, libpq 170006; existing pinned Docker image. No hosted database/test destination was consumed.

`uvx ruff check --select F` on the new/changed scripts and tests, Python compile checks, and `git diff --check` pass. Private plans/logs/receipts remain outside commits. The earlier Stage 4B.1 Ubuntu/ARM-package and frozen-production-import rehearsals remain preserved; they were not relabelled as new hosted results.

## Required real-AWS acceptance after approval

These are **unchecked hosted gates**, not tests passed locally:

- **State first:** verify bucket owner/Region, versioning, public/ACL controls, SSE-C blocking, policy and actual least-privilege access; test missing-state initialization, competing native S3 locks, normal lock release and state-version recovery/export. Never delete an active lock or revert to empty local state. Freeze all Terraform writers for recovery.
- **ARM64 host:** confirm actual Ubuntu 24.04 aarch64, Python 3.12, PostgreSQL 17, signed package/CLI installation, native psycopg import and cloud-init completion. Local x86_64/package metadata proof does not establish this.
- **Durable EBS:** confirm exact volume serial/UUID, encrypted gp3, mounted PGDATA and PostgreSQL's mount dependency. Reboot and verify persistence. Test missing/wrong-mount refusal only in a controlled non-authoritative setting with PostgreSQL stopped. Replacement/detachment tests and extra recovery hosts require their own approved resource/backup plan.
- **SSM/network:** verify patched agent enrollment, authenticated session and localhost port forwarding, no inbound security-group rules/public SQL/SSH, loopback binding and IMDSv2. SQL-only agent sessions must not grant shell/Run Command/deployment privileges. Do not configure owner passwords/production credentials without their own authorization.
- **PostgreSQL:** apply the unchanged packaged migrations explicitly when authorized; verify checksums, peer roles, collector private-data denials, owner schema/evidence denials, immutable evidence, constraints, attribution/audit rollback. Use synthetic records, not live ATS or production owner data.
- **Deployment/timers:** install a verified code-only ARM64 artifact when authorized; verify successful and interrupted/repeated installation behavior. Both collector and backup timers must remain disabled **and inactive** before/after deployment and reboot. No scan runs merely because deployment succeeds.
- **Backup/restore:** run actual pg_dump → encrypted S3 PutObject with checksum/version receipts and completion manifest. Confirm runtime backup-read/delete and state-access denials. An approved recovery operator retrieves exact versions and restores into a fresh isolated PostgreSQL 17 target; compare exact data/history/owner fixtures, security, grants, migrations, sequences and views. A fresh DB on the same host proves archive/SQL restoration, not host-loss or off-project recovery. Prove independent encrypted backup custody; authorize any additional recovery infrastructure separately.
- **Logging/health:** verify CloudWatch delivery/retention/alarms, accurate per-source/public counts, failure/positive-only outcomes, redaction, disk capacity and backup freshness. No owner notes or credentials in summaries. Configure an approved alert recipient rather than assuming console alarms notify anyone.

Nine real ATS endpoint checks, employer mapping/activation, explicit historical-evidence acceptance, final current-main freeze/commit, import/reconciliation, one writer and controlled first production scan remain separate cutover gates. Neither state bootstrap nor infrastructure apply authorizes them.

## Exact approval sequence

1. **Owner approves only bucket B from the reviewed private configuration and this commit:** create/configure the separate state bucket, verify controls, and permit backend initialization/transient locks for planning. No tracker resources or production data. Execute the explicit bootstrap only after that instruction; preserve and inspect any partial result instead of recreating it.
2. **Then** initialize the verified S3 backend, check its cached identity/encryption/native-lock settings, generate a fresh S3-backed tracker plan and record its SHA-256. The owner **separately approves that exact digest and commit** for the 20 reviewed infrastructure resources, timers disabled. Only that later instruction permits saved-plan apply. An empty-state local review plan is never reused for deployment. Post-apply testing must stay within the scope actually authorized.

The full commands and sample approval wording are in [the state procedure](../infra/aws/state/README.md). This report grants neither approval and does not ask for a production switch.
