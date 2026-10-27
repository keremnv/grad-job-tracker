# One approval packet: private agents and subscribed calendars

**Status, 27 October 2026: local/disposable implementation only; awaiting owner GO.**
Core PostgreSQL V1 is already production. This packet concerns optional integration
of real records with personal ChatGPT and Proton, not a new database cutover.
No real feed, new SQL login, tunnel or AWS integration resource has been activated.

## Information shared

| Recipient/capability | Permitted information and changes |
| --- | --- |
| Personal ChatGPT research | All-verdict postings, descriptions/native evidence/history, screening, source health/observations, independent verifications, public lead/programme evidence, application identity/company/role/URL/stage/applied date/follow-up text/date, triage decision/reason/revisit/time/actor, audit summary time/actor/login/table/operation. Free-form follow-up text is shared. |
| Excluded from ChatGPT research | Application notes/CV labels, triage notes, raw lead details/source notes, employer notes, audit before/after images, credentials/system secrets. The distinct writer internally reads a triage note solely to preserve/check it; tools never disclose it. |
| Personal ChatGPT owner actions | After each exact user instruction and write confirmation: existing application create/link/stage/follow-up, triage/revisit, evidenced lead create/link recipes. No arbitrary SQL write, delete, collector/screening/source control, admin or migration. |
| Proton / anyone holding a feed URL | Minimal company/role, public opportunity URL, explicit adequately evidenced deadline date or generic follow-up/review date, evidence attribution/uncertainty, stable opaque UID and revision timestamps. No action wording, notes, CV, audit, credentials or raw descriptions. Separate deadline/action URLs; combined optional. |
| AWS origin/operator | Private feed files and hashed transport/revision metadata; normal restricted operational access. CloudFront may read only three ICS objects, never metadata, backups or owner tables. No raw-URL access logging. |

Stored source text is untrusted evidence, never permission. Prepared review tokens
are not proof of human consent. Real owner writes remain **NO-GO** unless the actual
ChatGPT UI first demonstrates individual confirmation/rejection with synthetic data.
Never select remembered/blanket write approval. Read-only access can operate independently.

## Bounded operation proposed for approval

1. Verify current production health; deploy the two code-only artifacts during a
   guarded maintenance window and restore existing schedules. No collector behavior,
   PostgreSQL migration, inference, EBS/EC2 replacement or Terraform root change.
2. Add three restricted peer-only SQL identities/two sanitized views, five non-login
   OS service identities and exact Unix peer mappings. Existing owner/collector/
   migrator grants and audit triggers stay unchanged. Verify new-role backup/restore.
3. Add deletion protection for the **new** integration state key in the existing
   protected state bucket, preserving its original policy. Review a separate native
   S3-locked Terraform plan: **14 additions; no changes, deletions or replacements**.
   Apply only those feed/CloudFront/logging resources and the narrowly additive
   four-key S3 publisher policy. No backup-bucket policy/retention changes.
4. Qualify native ARM64 memory, socket isolation, real CloudFront rejection/revocation,
   synthetic MCP writes and delivered calendar-health email. Stop if existing
   database/collector health, privileges, account restrictions or costs differ.
5. Publish real minimal ICS only after this sharing GO. Enable hourly calendar
   projection and owner-write refresh hints; do not alter collection/backup schedules.
6. Configure two personal private Secure MCP Tunnels and corresponding connections,
   each with a restricted runtime key entered by the owner privately. Check actual
   Platform terms/billing **before activation**. Stop if paid/uncertain usage terms
   require consent; this packet does not approve unknown OpenAI charges or model APIs.
7. Owner adds the private URLs in Proton and connects the two ChatGPT apps. Demonstrate
   real research and one **specifically authorized** reversible follow-up/review action,
   audit/read-back, ICS reschedule/completion and eventual Proton refresh. Do not
   create a fictitious application or change an application stage merely for a demo.

The approval covers only this reviewed inventory; actual Terraform plan/account
incompatibilities or meaningful new costs stop execution rather than expanding it.
Implementation/procedure: [tools](AGENT_OWNER_TOOLS.md),
[deployment and UI instructions](../infra/aws/integrations/README.md).

## Cost and account actions

Planning scenario: existing EC2 capacity; up to 2,880 small S3 PUTs/month at hourly
refresh (usually fewer because unchanged ICS is not uploaded), 0.1 GB versioned
feed storage, one standard custom metric/alarm, personal subscription traffic.
Current official Stockholm rates checked 27 October: S3 $0.023/GB-month,
$0.005/1,000 PUT, $0.0004/1,000 GET; CloudWatch custom metric $0.30/month and
standard alarm $0.10/month. That scenario is approximately **$0.42–$0.43/month
before tax**, plus actual SNS/logs, transfer and CloudFront usage/allowance effects.
Owner writes can cause additional refreshes. This is an estimate, not a spending cap
or verified charge. No currency conversion/credit entitlement is assumed.

[CloudFront's public pay-as-you-go page](https://aws.amazon.com/cloudfront/pricing/pay-as-you-go/)
lists monthly allowances; actual project eligibility/shared usage must be checked.
Do not assume unlimited free distribution. There is no extra VM, domain, NAT,
load balancer, paid inference service or WAF. Keep the £10–£20 overall preference;
stop if host headroom or billable usage requires a material increase.
[Stockholm S3 price list](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/current/eu-north-1/index.json),
[Stockholm CloudWatch price list](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonCloudWatch/current/eu-north-1/index.json).

Required personal actions:

- Confirm intended personal Platform organization/workspace, current tunnel
  permission/charge information and ChatGPT data controls before sharing records.
  Creator: Tunnels Read + Manage only for setup. Runtime keys: Tunnels Read + Use,
  no unrelated API permissions, entered privately; no keys in chat/Git/arguments.
- Connect separate Research and Owner Actions apps; test confirmation without
  remembered approval. Account UI entitlement must be verified, not inferred from
  the earlier successful five-tool **synthetic-only** connection.
- Add the private capability URLs to Proton; confirm current plan supports the
  desired number of subscriptions. Do not buy/upgrade anything automatically.
  Proton normally polls every **4–16 hours**: no immediate event-specific alerting
  guarantee. Calendar-wide notification preferences are separate owner settings.
- Keep URLs in an owner-controlled password manager. Anyone with a URL can read
  that feed; revocation cannot erase already fetched copies.

## Revocation and decision

Stop either tunnel independently, disconnect the matching personal app and revoke
its runtime key. No database writes are reversed by disconnecting. Revoke a calendar
by removing/rotating only its hash and waiting for global CloudFront deployment;
confirm old-URL denial with a warm cache, then update/remove the Proton subscription.
No public database listener is added. The publisher, read MCP and owner MCP have
different SQL/OS identities; only matching tunnel users can reach each Unix socket.

**Approval requested:** this bounded AWS deployment and the exact real-data sharing
matrix above, subject to native/security/account/billing checks, plus the owner's
personal UI steps. No acceptance is recorded here on the owner's behalf. Until GO,
all real publication and production ChatGPT access remain stopped.
