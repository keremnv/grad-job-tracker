# AWS backup activation — paused after verification failure

26 October 2026. Application base `19ee9338ae902423e69d01d6eeb6f0f00bceaec2`.
**Production NO-GO. Git remains authoritative.** This is a partial execution record,
not notification or scheduled-backup qualification.

## Executed and verified

| Operation | Result |
|---|---|
| Baseline | Intended domain/audit/embedding tables empty; matching protected EBS mount, PostgreSQL 17.11/vector 0.8.7, migrations 0001–0004, roles/authentication and protections. |
| Artifact | Built/verified code-only ARM64/Python 3.12 release from `19ee933`; SHA-256 below. No data/config/secrets/model weights included. |
| Installation | Installed/replayed safely through existing trusted helper. Eight command-help checks and imports as all four OS service identities passed; root-owned code not writable by runtime users. No PostgreSQL/extension/migration reinstall. |
| SNS | One approved Standard alerts topic and its CloudWatch-only publish policy created; source project + four intended alarm ARNs constrain the policy. No instance IAM/SQL-role change. |
| CloudWatch | New backup-health metric filter/alarm configured. SNS actions added to existing host/credit/failure alarms; fresh before/after comparisons confirm unrelated evaluation/configuration settings preserved. |
| Stop-state recheck | Intended tables still empty; collection, backup and backup-health timers all **disabled/inactive**. HBA/configuration, PostgreSQL start time, migration checksums, roles and original grant/trigger checks unchanged. |
| Storage/state | Live S3 retention remains **30-day current / 30-day noncurrent**; no lifecycle change. Existing Terraform state still has twenty resources and unchanged object version. No apply, replacement, guard removal or state write. |

Installed artifact SHA-256:

```text
3ddb7de922a03e05add2d0e2eee124c2be4f0220a21a7819216f89692239ce94
```

## Verification failure and sequencing error

The initial five mock runs passed after adding the monitoring resource declarations.
An additional security assertion then failed: computed SNS/alarm ARNs were unknown
at plan time, so Terraform could not evaluate the JSON policy assertion. This was
an offline test-fixture defect, not an observed AWS policy or application failure.
The command chain was not correctly gated on that new failure, and the monitoring
API sequence completed before its result was inspected. This was an execution
sequencing error. No further AWS mutation occurred after discovery; subsequent
AWS/SSM operations were read-only checks.

The mock-only correction supplies explicit dummy ARNs using plan-time overrides;
no policy assertion was removed or weakened, and no mock apply is used. Offline
format/validate and all **five mock tests now pass**; both protected EBS/bucket
destruction-refusal tests also passed locally. These results do not erase the failed check or grant
permission to resume beyond the owner's requested verification stop.

The root Terraform replacement risk was not bypassed. Source now describes the
approved monitoring objects/actions and preserves 30+30 retention. **Four normal
Terraform resource imports remain pending**: topic, topic-policy attribute, metric
filter and health alarm. The three new AWS resources exist but are not yet in
Terraform state. Import them with normal protected backend locking before any
future root plan/apply; do not recreate/adopt them silently or apply the blocked
instance replacement. A confirmed subscription would be a fifth import later.

## Not performed

- No notification address collected, subscription created or email delivered.
- No receipt-generating manual backup, S3 archive retrieval/restore or freshness
  initialisation in this stage. The completion receipt remains absent.
- No timer enabled/started, collector run or scheduled execution observed.
- No production import, owner-record write, credential/SQL-role change, live ATS
  call, GitHub workflow change, main merge or authority switch.

The health alarm is currently ALARM because no health heartbeat exists. With zero
subscriptions, that is **not** email-delivery evidence. Do not enable backup/health
timers until actual notification/failure/missing-heartbeat tests and the approved
manual backup/restore checks succeed.

## Costs and resume point

Current additions are one Standard SNS topic, one log metric filter and one standard
alarm, plus the small code artifact in existing S3. No new compute or IAM role.
No active checker or scheduled backups; no email charges from this stage. The new
alarm's public list rate is $0.10/month prorated; metric/request/storage charges
depend on usage, not a verified invoice. With the retained 30+30 lifecycle, future
10–100 MiB twice-daily archives plus monitoring are approximately **$0.43–$0.68/month**
(illustrative £0.40–£0.63 with 20% VAT at £0.77/$), allowing 124 archive-sized versions.
At 1 GiB/archive the estimate is $3.26/month. No credit eligibility or total project
monthly spending is inferred. [Current policy/rates](AWS_BACKUP_POLICY.md).

Resume requires the owner's instruction following this failed-verification stop:
record the four exact existing resources through normal Terraform imports, collect
the intended email through a private local file, subscribe and await owner confirmation,
test actual alert/recovery delivery and missing heartbeat, run/verify the empty-DB
manual backup and a fresh-target restore, then enable only the approved backup and
health timers. Do not repeat installation, recreate the topic/alarm/filter, shorten
retention or reinstall PostgreSQL/vector/migrations. Record a scheduled execution
only after it actually occurs. All private identifiers, receipts and logs stay
outside Git. Production cutover remains separately gated.
