# Protected Terraform state — deployed backend

The protected state bucket and tracker are already deployed (20 managed tracker resources). Stage 4B.2d verified the unchanged state object version and normal lock release; a read-only plan was blocked by prevent_destroy on the known user-data replacement. See [current readiness](../../../docs/AWS_RECOVERY_READINESS_REPORT.md). The initial bootstrap commands below are historical/new-environment procedures: do not rerun them against the existing deployment. Its operational root declares **S3, `encrypt=true`, `use_lockfile=true`**, Stockholm and the named `grad-jobwatch` profile. Only the bucket and allowed project identity are supplied privately. Terraform 1.17.0 is tested; the existing minimum 1.10 supports native S3 locking. No DynamoDB table, KMS customer-managed key, hosted service, credential change or running-tracker switch is added.

## Ownership, retention and permissions

Use a **separate** account-regional S3 bucket named `grad-jobwatch-state-PROJECT_ID-eu-north-1-an`. The rendered name is private; the example identifier is a placeholder. It is deliberately **outside the tracker's Terraform state**. A tracker destroy or instance replacement therefore cannot manage it. A small, explicitly approved one-time AWS CLI bootstrap records its configuration in reviewed code without recursive bootstrap-state management.

Controls: versioning, SSE-S3 AES256, SSE-C blocked, AWS's default four public-access blocks and ACL-disabled ownership verified after creation, TLS-only policy, denial of other-project access, denial of bucket deletion, and denial of current **and version** deletion for `tracker/terraform.tfstate`. There is **no lifecycle expiry**, including for noncurrent state versions, and the verifier refuses any lifecycle configuration. Bucket-policy deletion denials alone do not stop S3 lifecycle expiration, so lifecycle administration is deliberately absent from both supplied access policies. Ordinary updates create recoverable versions. The lock key is exactly `tracker/terraform.tfstate.tflock`: it remains deletable so native unlock works. Do not apply Object Lock/default retention to the lock file. S3 Bucket Keys are an SSE-KMS optimization, not needed for SSE-S3. No extra billed access-log delivery, data-event trail or ABAC permission system is introduced by this narrowly scoped bootstrap; the proposed first approval explicitly includes that choice. CloudTrail management history remains available; it does not provide a state-object access audit.

These controls do not make an administrator powerless. A privileged principal could replace the bucket policy or overwrite current state. Treat administrative policy changes and state recovery as separately reviewed operations. The normal state operator receives **only** `access-policy.json.template`, rendered for the one bucket:

| Operation | Permission/resource |
|---|---|
| Discover default workspace | `s3:ListBucket` on this dedicated state bucket only (including absent-object checks) |
| Read/update state | `s3:GetObject`, `s3:PutObject` on the exact state key |
| Acquire/release lock | `s3:GetObject`, `s3:PutObject`, `s3:DeleteObject` on the exact `.tflock` key |

No state deletion/version deletion, bucket administration, other workspace writes, backup access or IAM changes. Use only the default workspace. The `no-workspaces/` listing allows Terraform's ordinary workspace discovery; object permissions intentionally prohibit creating a second workspace. The backend policy is based on HashiCorp's **named backend API requirements**, not inferred from the managed-resource plan. [HashiCorp S3 backend and locking](https://developer.hashicorp.com/terraform/language/backend/s3).

`bootstrap-policy.json.template` grants only creation/configuration/inspection of the **one** Stockholm bucket. It does not grant object uploads or deletion. ACL/Object Lock variants of CreateBucket are not requested and their extra permissions are not granted. Backend and bootstrap permissions are separate from the Terraform provider's EC2/IAM/S3 deployment permissions. No policy is automatically attached and no protected `/managed/` role is modified. The EC2 runtime profile remains unchanged and has **no state-bucket access**.

## Review before the bucket exists

These commands remain valid without any S3 bucket or backend authentication:

```sh
terraform -chdir=infra/aws init -backend=false -input=false
terraform -chdir=infra/aws fmt -check -recursive
terraform -chdir=infra/aws validate
terraform -chdir=infra/aws test
python3 -m unittest discover -s infra/aws/tests -p test_protections.py -v
```

The last command runs real **destroy plans**, never apply, against fake local EBS/bucket state with dummy credentials and loopback-only provider endpoints. Both plans must be rejected by `prevent_destroy`; no AWS resource IDs or endpoints are used.

For an authenticated initial infrastructure review, use an isolated copy with a local backend:

```sh
umask 077
python3 infra/aws/scripts/review_plan.py --vars /PRIVATE/aws.tfvars.json --output /PRIVATE/new-review
```

This keeps the source S3 backend unchanged, copies no state, credentials or private tfvars, reuses the checksum-locked provider installed by the first command, and saves review JSON, logs and a summary. The temporary local-backend binary plan is removed after inspection so it cannot be reused for apply. Its only Terraform operations are init, plan and show. **Never apply this local-backend plan.** It deliberately sees empty state and is only for initial review before deployment. After deployment, use the actual S3-backed root, not this empty-state review. The existing inventory/security checker must report the same 20 tracker resources. The separate bootstrap bucket is not one of those 20.

## Step 1 — explicit approval for the state bucket only

No authorization is granted by this document. First prepare/review private inputs:

1. Copy `bootstrap.json.example` to a private `0600` file outside tracked source; insert the actual expected project identifier. Keep profile `grad-jobwatch` and Region `eu-north-1`. Confirm the selected Region in AWS Settings > View all projects > Overview > Additional Info > Region. Review current spend controls/credits without treating credits as a spending cap.
2. Render the exact bucket name, three policies and partial backend configuration locally:

   ```sh
   python3 infra/aws/scripts/state_bucket.py render --config /PRIVATE/bootstrap.json --output /PRIVATE/new-state-preparation
   ```

3. Review the exact name and policies privately. Obtain an owner instruction such as: **“Approve only the reviewed state-bucket bootstrap in this commit, for bucket B in Stockholm, with versioning, AES256, no extra access-log/data-event pipeline, and the supplied retention/deletion policies. Permit backend initialization and transient locking for a new plan; do not create tracker infrastructure.”** This is the first authorization boundary, not permission to apply the tracker plan.
4. Only after that instruction, a temporary authorized bootstrap operator runs:

   ```sh
   python3 infra/aws/scripts/state_bucket.py bootstrap --config /PRIVATE/bootstrap.json --confirm-create-bucket B --output /PRIVATE/new-bootstrap-receipt.json
   python3 infra/aws/scripts/state_bucket.py verify --config /PRIVATE/bootstrap.json
   ```

   It checks STS identity, refuses any existing or inaccessible bucket, uses `--bucket-namespace account-regional`, configures versioning/encryption/tags and installs the reviewed policy **only after checking that none already exists**. It verifies all controls with expected-owner reads. It never modifies an existing policy, deletes anything or writes state objects. A partial failure/uncertain acknowledgment requires read-only inspection; do not rerun bootstrap over the resulting bucket. Preserve receipts and obtain approval for exact corrective commands. No automatic cleanup or silent adoption.
5. Confirm the operator that will initialize Terraform has the exact backend access policy plus appropriate deployment/read permissions. No permanent access key is required; retain official browser-authenticated temporary access. Do not give the instance, collector or SQL-only agent these state permissions.

## Step 2 — fresh S3-backed plan, then a separate apply approval

1. Confirm no existing local state contains managed resources and no previous deployment has happened. If nonempty state exists, **stop**: preserve it and review an explicit `init -migrate-state` procedure. Never use `-reconfigure` to abandon existing state. For this never-applied root, initialize the new remote backend with a private cache:

   ```sh
   umask 077
   mkdir -m 700 /PRIVATE/new-live-cache
   export TF_DATA_DIR=/PRIVATE/new-live-cache
   terraform -chdir=infra/aws init -input=false -reconfigure -backend-config=/PRIVATE/new-state-preparation/production.s3.tfbackend
   python3 infra/aws/scripts/state_bucket.py check-backend --config /PRIVATE/bootstrap.json --backend-cache /PRIVATE/new-live-cache/terraform.tfstate
   ```

   The cache check refuses local/unlocked/insecure/wrong-project backends, embedded credentials, endpoint overrides and DynamoDB locking. Backend config contains only bucket/identity, never AWS keys or login tokens. HashiCorp caches backend settings and embeds them in saved plans, so private paths/permissions matter. Verify default workspace and bucket safeguards before planning; use the bootstrap/security reviewer’s read permissions for bucket-control checks rather than adding bucket administration to the ordinary backend policy.
2. Generate a **new** authenticated S3-backed plan, using native locking. It will create/delete a transient `.tflock` object, which is explicitly included in step 1's approved backend use. No tracker resources are created by planning:

   ```sh
   terraform -chdir=infra/aws workspace show
   terraform -chdir=infra/aws plan -input=false -lock-timeout=5m -var-file=/PRIVATE/aws.tfvars.json -out=/PRIVATE/tracker.tfplan
   terraform -chdir=infra/aws show -json /PRIVATE/tracker.tfplan > /PRIVATE/tracker.plan.json
   python3 infra/aws/scripts/check_plan.py /PRIVATE/tracker.plan.json
   sha256sum /PRIVATE/tracker.tfplan
   ```

   Review the Git commit, target identity/Region, provider/AMI pin, exact resource diff, cost, guards, IAM and disabled timers. Verify the lock object is released normally; after uncertain failure inspect the holder/ID and running Terraform processes. Never automatically delete locks, force-unlock, use `-lock=false`, or reconfigure to local state. Test competing plan locking after bootstrap; actual S3 locking has **not** been tested while the bucket is absent.
3. Obtain a **second** owner instruction naming the full commit and SHA-256 of this **S3-backed** plan, such as: **“Approve applying exactly plan digest H from commit C for the 20 reviewed tracker resources in Stockholm, with both timers disabled. No production-data import, live ATS checks, owner-secret setup, workflow switch or cutover.”** Any changed/stale plan requires new review/approval; never reuse the pre-bucket local review plan.
4. Only after that separate approval, verify the digest/backend again and run:

   ```sh
   terraform -chdir=infra/aws apply -input=false -lock-timeout=5m /PRIVATE/tracker.tfplan
   ```

   This is a prepared command, **not executed during this safety pass**. Saved-plan apply does not itself ask a second interactive yes/no; the explicit owner approval and digest check are essential. Preserve current remote state and an encrypted off-project state export after successful/partial apply. No automatic application migration/import or timer start occurs. Non-authoritative post-apply test operations need authorization within their actual scope; infrastructure approval is not production cutover approval.

For apply-permission review, retain the existing IAM skill workflow, distinct from backend permissions:

```sh
DISABLE_IAM_POLICY_AUTOPILOT_TELEMETRY=true uvx iam-policy-autopilot@latest generate-policies /PRIVATE/tracker.plan.json --region eu-north-1 --account PROJECT_ID --pretty > /PRIVATE/deployer-baseline.txt
```

No policy is uploaded automatically. Scope PassRole to the instance role, preserve managed-role protections and retain ordinary separation between deployment, SQL owner and collector.

## State recovery and durability

State is infrastructure inventory, **not a PostgreSQL data backup**. Keep S3 versions indefinitely and maintain an independently secured encrypted state export after each applied change (including partial failures), with its SHA-256 and commit. Store it outside Git and outside the project's sole failure boundary. Do not print state in logs. Same-project versioning alone does not cover project lockout, administrator compromise or service unavailability.

A recovery operator may temporarily need `s3:ListBucketVersions` on this bucket and `s3:GetObjectVersion` on the exact state key. Those history permissions are deliberately absent from the ordinary backend policy. Before promoting a prior version: freeze all Terraform operators, preserve the latest state/version, check lineage/serial and actual resources, inspect a refresh-only recovery plan and obtain explicit approval. Copy a verified older version into a **new current version**, rather than deleting newer history. Existing object-deletion denies remain in force. Never blindly push old state, lower serials, `terraform state rm`, recreate resources from empty state, or force-unlock an active process. Recover the database independently through its real backup/restore procedure.

A planned retirement needs separate owner authorization, verified infrastructure/database backups, a reviewed bucket-policy change and deliberate cleanup of retained versions. It is not an ordinary tracker update. Main data-volume and backup-bucket `prevent_destroy` guards and backup `force_destroy=false` remain unchanged; removing resource blocks/guards or using broad administrative APIs can evade Terraform protections, which is why those changes require review.
