# Agent/calendar live preflight — 27 October 2026

**Deployed: Proton feeds, ChatGPT Research and Owner Actions LIVE.** Owner GO for `54f1d99` and its
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

## Deployment executed — 27 October 2026, 12:00–12:25 UTC

| Step | Result |
| --- | --- |
| Pre-maintenance backup | Scheduled 12:00 run succeeded; exact archive/manifest versions, AES256 and SHA-256 receipts verified in S3. |
| App release `41f640b` | Installed and re-run by the trusted host installer; collection, backup and health timers paused **9.7 s** and restored; all table counts unchanged; four identities import natively (ARM64, Python 3.12.3, psycopg 3.2.13); PostgreSQL 17.11/pgvector 0.8.7; backup health passed afterwards. |
| Agent runtime | Installed once and verified (43 members, root-owned, model-free). The installed guard wrongly refused a repeat because `systemctl is-enabled` exits 0 for the static calendar service; fixed with a regression test in `b9cfb82` (host copy updates with the next release). |
| Identities/SQL/auth | Five non-login OS identities; three password-less, membership-free roles and two sanitized views; three exact peer lines inserted after the existing local rules, both files validated by PostgreSQL before `pg_reload_conf()` (no restart). Original auth files backed up root-only on the host. |
| Permission matrix | New roles read research views only; denied notes, CV, triage note (read/calendar), lead details and audit images; default read-only. Owner role: only granted application/triage/lead columns, no notes, delete, postings, sources or audit writes. Existing collector/owner/migrator logins unchanged; wrong OS user/role and tunnel-to-database denied. |
| Native MCP | Both servers answered MCP `initialize` over their Unix sockets; read/owner tunnels reach only their own socket; collector reaches neither. About 40 MiB per server; ~1.25 GiB still available. Both stopped and disabled afterwards. |
| State protection | `DenyIntegrationStateDeletionIncludingVersions` appended to the state bucket policy; four originals preserved byte-for-byte. |
| Terraform | Saved reviewed plan applied: **14 added, 0 changed, 0 destroyed**. No name collisions beforehand. Final plan: no changes. |
| CloudFront | Synthetic feeds first: valid GET/HEAD 200; missing/wrong/cross-feed/query/unissued tokens, private manifest and traversal 404; POST and plain HTTP 403; anonymous S3 403. A temporary test capability was revoked (function-only update) and rejected with a warm cache within ~30 s; its token was destroyed. |
| First publication | One explicit `--initialize` as the calendar identity, then an ordinary scheduled run; hourly timer and owner refresh path enabled. Real feeds replaced synthetic ones (deadlines 3 events, actions 0). Event properties are standard ICS only; descriptions hold fixed guidance and public source provenance, no owner text. |
| Alarms | The new alarm entered ALARM at creation (missing data is breaching). A harness quoting error in the first initialization attempt (an unquoted transient-unit DSN, not the shipped unit) logged one unhealthy/nonzero calendar run, so calendar-health and operation-failure alarms fired; both are expected to recover after healthy runs. Owner to confirm delivered ALARM/OK emails. |

Calendar config files are root-owned `0644` (bucket name and the Git-tracked deadline
policy only) because the unit runs with `Group=jobwatch-db`; a `0640` calendar-group
file would be unreadable. No tunnel, runtime key or production owner write exists yet.

## Execution and handover status

| Integration | Status |
| --- | --- |
| Existing production collector/backups | **LIVE**, unchanged schedules |
| Proton publication | **LIVE** on AWS (hourly); owner subscription in Proton pending |
| ChatGPT Research | **LIVE**: host tunnel connected; five read tools listed and each passed a harmless native probe |
| ChatGPT Owner Actions | **LIVE**: synthetic gate passed through the owner tunnel (ChatGPT approval box → one audited write; Cancel → none), then switched to the host. Two tools listed; only an invalid-recipe prepare probe was run; no production write |

Tunnel profiles moved to `/etc/jobwatch-tunnel` (`3a7ee0c`): `/etc/jobwatch` is
`jobwatch-db`-only. A mis-entered laptop credential briefly put the synthetic owner
server on the Research tunnel, causing mixed tool discovery; it was stopped and
shredded, and no production write was possible. Both original host keys were later
invalidated in Platform and replaced by the owner. The two tunnels currently share
one runtime key, so revoking it disconnects both.

Private identifiers, receipts, capability URLs and connection commands remain outside Git.
