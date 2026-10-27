# Agent/calendar live preflight — 27 October 2026

**SNS blocker resolved; host deployment not yet started.** Owner GO for `54f1d99` and its
real-data sharing matrix is recorded. A single newly identified existing SNS-policy
amendment is outside the approved 14-addition plan and needs approval. No second
sharing/architecture decision is requested.

## Verified against existing resources

| Check | Observed result |
| --- | --- |
| Identity/access | Renewed the existing `grad-jobwatch` session with official browser login; authenticated expected project/Stockholm. SSM Online and read-only host inspection succeeded. Toolkit calls returned unknown-tool errors, so the established local CLI/SSM helper was used. |
| Host/database | Expected `t4g.small` ARM64 instance/AMI; PostgreSQL 17.11, vector 0.8.7, loopback listener, no HBA parse errors. Existing installed application `e3807a0`; migrations inspected, not run. |
| Corpus/owner state | 636 postings, 1,633 immutable versions, 740 events, 114 runs, 34 leads, 24 verifications, 96 audit rows; zero applications/triage. Owner-state hashes captured privately without exposing contents. No records changed. |
| Capacity | Approximately 1.3 GiB available memory at inspection; no swap. This is available headroom, **not** native MCP/tunnel performance qualification. |
| Storage/network | Both attached EBS volumes encrypted and in use; database volume preservation flag and EC2 termination protection present. Zero inbound security-group rules; IMDSv2 required, Standard CPU credits. Mount/UUID and disk metadata captured privately. |
| Terraform | **25 managed entries**, not 20: unchanged original twenty plus the five approved backup-alert imports documented in `AWS_BACKUP_ACTIVATION_REPORT.md`. No unexplained managed-resource state difference. No Terraform apply, remote state write or lock operation performed. |
| Existing operations | Collection, backup and fifteen-minute health timers enabled/active. No collector/backup in flight at inspection. Existing four alarms OK. Latest completed backup's exact archive/manifest S3 versions, AES256 and SHA-256 receipts verified. No fresh backup/restore run in this stage. |
| Artifacts | Both previously built code-only archives verified against member/digest inventory and code commit `41f640b`; approved `54f1d99` differs only in documentation. No artifact upload or installation occurred. |
| Billing | AWS project plan API reports PAID. No paid Cost Explorer request. Credits, expiry, spend limit and exact monthly charge not newly verified; no credits assumed. No new recurring resources created. |

## Concrete blocker and smallest correction

The live SNS topic has one CloudWatch publisher statement,
`AllowReviewedCloudWatchAlarms`, limited by `aws:SourceAccount` and an exact
`aws:SourceArn` list:

- `grad-jobwatch-host-status`
- `grad-jobwatch-cpu-credit-balance`
- `grad-jobwatch-operation-failure`
- `grad-jobwatch-backup-health`

The proposed `grad-jobwatch-calendar-health` alarm is absent. Configuring its SNS
action would **not** authorize delivery. This is a missing integration permission,
not an existing backup failure. The API policy agrees with its Terraform declaration;
there is no unexpected policy drift. [AWS documents SNS resource-policy requirements
for alarm notifications](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Notify_Users_Alarms.html).

Prepared correction: append **one separate** `AllowReviewedCalendarAlarm` statement
to the existing topic policy. Permit only `sns:Publish` by
`cloudwatch.amazonaws.com`, only this topic, only the exact Stockholm calendar
alarm ARN in this project. Preserve all original statements, existing email
subscription and four alarm permissions. No wildcard alarm, human/IAM-role grant,
new topic, IAM-role expansion or additional resource is required.

`render_calendar_alert_policy.py` creates a private proposed policy from actual
read-only attributes and expected project/topic inputs, refuses drift/already-added
permission, and makes **no AWS calls**. The Terraform declaration remains owned by
the original root. After separately approved `SetTopicAttributes`, a reviewed
refresh-only state reconciliation must contain no infrastructure actions. Never
apply the normal root plan: its known protected-instance replacement remains blocked.
The independent integration plan stays **14 additions**; the amendment is explicitly
**one existing SNS policy-attribute update**, not hidden inside that count.

The correction does not add a billable resource. The original marginal usage
estimate is unchanged; actual notification/CloudFront usage and OpenAI terms still
need their documented checks. No new cost was accepted in this preflight.

## SNS amendment applied — 27 October 2026, 10:26 UTC

The owner approved the calendar-only statement. Immediately before the change the
live policy was re-read and matched the preflight snapshot exactly. One
`SetTopicAttributes` call wrote the privately rendered policy (SHA-256
`792cb570…`); read-back shows `AllowReviewedCloudWatchAlarms` unchanged plus
`AllowReviewedCalendarAlarm`, and the confirmed email subscription intact.

Reconciliation used a read-only, unlocked (`-lock=false`) refresh-only root plan
with the exact private inputs of the backup activation. It contained **no
infrastructure actions**. Compared with the previously classified refresh, the only
new drift is the approved policy, on `aws_sns_topic_policy.backup_alerts` and the
identical `policy` projection of `aws_sns_topic.backup_alerts`; the nine previously
classified entries are unchanged. As in the backup activation, the refresh-only
plan was **not applied**: `backup_alerts.tf` now declares the live policy, and every
plan refreshes first, so no state write is needed. The known EC2 user-data
replacement remains blocked and unapplied.

## Execution and handover status

| Integration | Status |
| --- | --- |
| Existing production collector/backups | **LIVE**, unchanged |
| Proton publication | **PENDING** host deployment (steps 1–9) and native qualification; SNS amendment applied; no subscription URL generated or data published |
| ChatGPT Research | Code/artifact **READY**; account billing/permissions, native qualification, two tunnel IDs and runtime keys still pending |
| ChatGPT Owner Actions | **BLOCKED** from production activation until actual synthetic ChatGPT individual-confirmation/rejection succeeds; no real owner write authorized or attempted |

No tunnel, API key, SQL identity, HTTPS endpoint, timer, credential or infrastructure
resource was created/changed. No backup/health timer pause, fresh backup, SQL migration,
real calendar projection or native installation was attempted. Only the AWS CLI
session was renewed and read-only discovery/SSM inspection performed. Private
identifiers, receipts, owner hashes and the proposed policy remain outside Git.

**Additional approval needed:** the one exact calendar-only SNS statement and
non-destructive reconciliation under existing Terraform ownership, then continuation
of the already-approved 14-addition deployment. Preserve all other approvals and
restrictions. The personal connection handover follows actual deployment; do not
invent URLs/tunnel IDs or claim Proton/ChatGPT is connected meanwhile.
