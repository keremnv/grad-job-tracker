# V1 release candidate — preflight, not production approval

25–26 October 2026. Branch `feat/v1-release-readiness`, based on portfolio `2cc24c4`.
**Overall: NO-GO for production.** Git remains authoritative. The initial local
release preparation used no authenticated AWS/Platform calls, hosted database,
tunnel, timer activation or production import. Public documentation/prices were
read. Subsequent authorised AWS inspection/maintenance is recorded below.

A separately authorised [26 October hosted maintenance](AWS_V1_MAINTENANCE_REPORT.md)
now qualifies the V1 artifact, pgvector 0.8.7, migration 0004 and synthetic format-2
recovery on ARM64. Both timers remain disabled/inactive and intended domain tables
remain empty. This does not qualify production recovery or cutover. The no-AWS
statement above describes the earlier local release-preparation stage.

This is the current compact release record. Older stage reports retain their
historical results; their daily/26-hour recovery proposal is superseded by the
owner's **AWS-only twice-daily backup policy**. The previous strict maximum-loss
requirement is withdrawn; [the current policy](AWS_BACKUP_POLICY.md) records the
accepted missed-run/project-lockout/Region risks. Frequency is not an RPO guarantee.

## Integration evidence

Fetched remote `main`, rechecked 26 October:
`9bbcb0149d41250cc2e56b2cbb25779bd5fafc16`.
Its only commits outside portfolio `2cc24c4` are observation updates:

| Commit | UTC date | Inspection |
|---|---|---|
| `b6e01679d3c2e56f5b850fe814a90bc7df671d57` | 24 October 2026 | Observation files only |
| `9bbcb0149d41250cc2e56b2cbb25779bd5fafc16` | 25 October 2026 | Observation files only |

Common ancestor: `90434ea6d9a3457f9c8ba40f01ad25fa2ceffca3`. The normal merge
is committed locally as `7d76a91`, with both histories intact and no conflicts.
Six distinct paths changed on main, all under jobs/index/health/text/report
observations. The integrated `data/`, `config/` and active workflow tree objects
match inspected main exactly. Owner ledgers, including legitimately absent files,
match byte-for-byte. No hand-edited data or obsolete snapshot replacement.
All other worktrees were clean and preserved. **Nothing was merged into main.**

Repeat this comparison after any new main commit. Merge new main into the release
branch normally; stop on code/config/owner conflicts. Do not resolve with wholesale
`ours`/`theirs`, squash an old data tree over main, or force-push. Code-artifact
commit **R** and final authoritative data commit **C** are different identifiers:
select C only after the approved writer freeze. This audit SHA is not automatically C.

The actual importer **plan-only** operation against that inspected main validates:
631 postings, 1,013 planned versions and 110 planned retained events, with no
unrecoverable referenced-text entries reported. These are commit/date-specific
plan counts, not a database import, live market coverage or repaired native history.
Both known legacy limitations and all nine activation gates remain. The private
machine-readable plan is outside Git; final C still needs a new plan/reconciliation.

## Hosted compatibility — baseline and completed maintenance

The table below retains the pre-maintenance comparison. Its release, vector, 0004
and synthetic recovery work is now **VERIFIED**, with exact before/after results in
[the hosted V1 maintenance report](AWS_V1_MAINTENANCE_REPORT.md). Optional roles and
production authentication were not provisioned. Stage 4B reports retain their
historical results; this does not authorise further maintenance.

| Recorded host | V1 maintenance required, in order |
|---|---|
| ARM64 Ubuntu/Python 3.12, PostgreSQL 17.11, encrypted protected EBS; release `1b72d0d` | Read-only revalidation first. Preserve exact volume/UUID, zero inbound rules and SSM. No resize/replacement or Terraform apply. |
| Migrations 0001–0003, intended domain tables empty; timers disabled | Verify unchanged checksums/emptiness and preserve a pre-upgrade backup with its old recovery bundle. Install a hash-verified current **code-only** artifact with both timers still disabled. |
| No recorded pgvector installation | Separately approve pinned ARM64 PostgreSQL 17 extension software, verify source/package checksum and `public.vector` **0.8.7**; explicitly run `vector_extension.sql` as administrator before migration 0004. Package availability/ARM64 installation is not qualified by x86 local tests. |
| Existing migrator/collector/owner and peer/socket controls | Apply only pending migration 0004 through `migrate_local.sh` as existing migration identity. Provision optional embedding/read roles explicitly through reviewed SQL; new roles have no password. Do not rerun initial `bootstrap.sql` over existing cluster roles. |
| Old backup format 1 | Current code emits **format 2**, includes vectors, extension/role requirements and a conservative backup start timestamp. Restore 1 only with the pinned pre-vector code/tools; keep that bundle. Existing format-2 restores remain compatible; older manifests without start proof cannot pass the new freshness check. |
| No production read login/authentication | Approve separate read-role scope, narrowly scoped HBA/authentication and temporary SSM forwarding. Read tools need 0004; owner credentials never substitute. The runtime artifact includes prerequisite SQL but does not execute it automatically. |

Run new-release synthetic qualification and vector-aware backup/independent restore
before accepting host maintenance. Keep intended production domain tables empty.
The artifact ships base application/psycopg ARM64 wheels, **not** MCP/model inference
dependencies or weights. Run model-free MCP on a separately approved trusted gateway
with `requirements/agent-read.lock`. EmbeddingGemma float32 inference/indexing stays
on suitable existing local hardware, not beside PostgreSQL on the 2 GiB host.
SQL/BM25/FTS remain available without vector coverage or a running model.

The recorded Terraform user-data drift proposes an EC2 replacement, blocked by
`prevent_destroy`. Do not apply it, remove guards, edit state or hide it with an
unreviewed ignore rule. Current code-only maintenance does not need Terraform.
Resolve deployed-bootstrap version control separately before any future infra apply;
retain protected EBS, backup bucket and separately protected native-locking S3 state.

The readiness diagnostic's external transport label now recognises the approved
authenticated SSM/peer-or-SCRAM path as well as direct TLS. It remains explicitly
unverified by that command; no grant, authentication or readiness result is relaxed.

## Recovery — owner-approved AWS-only policy

Use [the compact backup policy](AWS_BACKUP_POLICY.md) for configuration, retention,
monitoring/email, costs and activation. The strict 12-hour maximum data-loss,
external encrypted custody/worker and automatic all-writer fencing proposals are
superseded by the owner's 26 October decision.

Prepared, **not installed/activated**: 00:00/12:00 UTC backups; a private atomic
receipt only after confirmed S3 archive + manifest completion; a 15-minute local
receipt checker through the existing log stream. Warning >=14h / critical >=26h
are advisory. The proposed single health metric/alarm treats missing heartbeat as
breaching, in addition to existing process-failure alarms. Owner-confirmed SNS
email and hosted failure/missing-run delivery tests are still required.

Source retention proposes 30-day current + one-day noncurrent expiration (roughly
31–32 days), versioning/encryption/protections unchanged. This lifecycle change is
not applied; the recorded bucket is still 30+30. No S3 delete or policy update ran.
The host retains the previously qualified `43aab3b` release and disabled six-hour
timer definition until a separately approved code-only update; do not describe the
prepared twelve-hour configuration as already deployed.

Integrity checks, exact S3 versions, conservative snapshot start and guarded
fresh-target restore are unchanged. Synthetic hosted and independent restores
passed; the actual imported production archive still needs restore/reconciliation.
No external retained copy or strict RPO is required. Failed/missed backups, AWS
project lockout, regional failure or administrator deletion can cause >12h loss.

Marginal planning cost for 10–100 MiB archives is about **$0.42–$0.55/month**
(illustrative £0.39–£0.51 including 20% VAT at £0.77/$), with one new metric/alarm
and small SNS/log/request usage. Actual production size/current bill is unverified;
no free allowance/credits assumed. Existing host estimate remains historical.

## One release checklist and approval ledger

VERIFIED means the scoped evidence exists. READY FOR APPROVED EXECUTION means
reviewable procedure/code, **not permission** or a passed hosted gate. BLOCKED
means a dependency or safety implementation is still missing. No row supplies GO.

| Status | Gate / exact next operation | Required authority, cost and consequence |
|---|---|---|
| VERIFIED | Feature chain + inspected main integrated without data conflicts | Local Git only; main/writer untouched. Refresh before final integration. |
| VERIFIED | Real online synthetic ChatGPT `sql_query`; local five-tool tests | Synthetic runtime stopped locally; plugin/key withdrawal owner-reported, not independently checked. No production sharing implied. |
| VERIFIED | Hosted ARM64 V1 and independent vector-aware synthetic recovery | Format 1 and 2 exact restores passed; intended domain tables empty. Does not prove final real-data recovery. |
| VERIFIED | Bounded AWS/SSM/SQL/state inspection, 26 October | Twenty-resource state and protections unchanged; intended tables empty; timers disabled. Alarms have no notification destinations; credit expiry/spend limit and current monthly bill remain unknown. No further permission implied. |
| VERIFIED — owner decision | AWS-only backups every 12h, no strict RPO | Existing S3 only; missed/failed backups, project lockout and regional failure can cause greater loss. No external custody/worker or automatic fencing requirement. |
| READY FOR APPROVED EXECUTION | Backup-only code update, SNS email and process/age/missing-heartbeat monitoring | Approve verified release install, one SNS Standard topic/confirmed private recipient, one metric/filter/alarm and existing alarm actions; manual synthetic/failure tests and explicit backup/health timer activation. Existing instance log IAM suffices; roughly $0.42–$0.55/month marginal for 10–100 MiB archives. Collection remains disabled. |
| NEEDS OWNER APPROVAL | Approximately 30-day lifecycle | Prepared 30-day current + one-day noncurrent expiration; reducing the existing noncurrent period can delete older noncurrent versions. Inventory/review first; no live lifecycle change yet. |
| VERIFIED | Code-only V1 host maintenance, vector 0.8.7, migration 0004 and synthetic requalification | Artifact R installed/replayed; PG17.11 unchanged; format-1 pre-upgrade and format-2 synthetic S3/restore proofs passed. No optional roles, inference, authentication changes, timer activation or infrastructure changes. |
| NEEDS OWNER DECISION | Explicit legacy-evidence acceptance against final manifest | Missing complete schema-2 native responses and never-indexed pre-schema-2 exclusions cannot be reconstructed. No technical fee; private dated owner acceptance, no invented evidence or employer edit times. |
| READY FOR APPROVED EXECUTION | Nine genuine endpoint checks and employer/region mapping review | Separate live HTTPS approval, initially only in a non-authoritative qualification DB, no posting publication. Repeat/persist final checks on imported source rows before activation; collector validation + audited owner activation. Failed/renamed boards need a coverage decision; no new employers. |
| READY FOR APPROVED EXECUTION | Final code integration, Git/owner writer freeze and C pin | Repository/workflow administration + owner freeze approval. Wait for in-flight work, preserve/push legitimate edits, verify full history/remote equality. Collection pauses; main/workflow changes require a later explicit instruction. |
| BLOCKED | Import C once into empty intended target, reconcile all datasets, then real-data independent restore | Needs prior gates and explicit real-data/temporary migration-access approval. Restore a downloaded S3 version into a separate target; no external storage service required. Migrator writes, scoped auth and operator version reads; S3/possible recovery egress costs. Never reset a populated target or count synthetic proof as real-data proof. |
| BLOCKED | Production owner SQL and separately scoped ChatGPT read access | Approve private owner/audit read exposure, server-side credential custody, narrow HBA and authenticated SSM forwarding; no collector/admin fallback. Separate runtime tunnel approval/terms; no restart of the synthetic tunnel or host inference. |
| NEEDS OWNER DECISION | Private revocable Proton-compatible HTTPS feed and actual subscription qualification | Stage 4E snapshots are local, not a live feed. Approve destination/domain/TLS, capability-URL custody/rotation, schedule/monitoring and cost; no public MCP, no notes/CVs. Polling is not guaranteed urgent alerting. Remains V1 work. |
| BLOCKED | Explicit single-writer GO, first controlled PostgreSQL scan, then routine scheduling | Needs all mandatory gates and separate switch/ATS/service approvals. Retire file writers/default instructions, keep alternative Actions collector inert. Inspect first-scan baselines/health/owner hashes; verify the S3 backup point, then approve collection activation. After SQL writes, recovery is roll-forward, not stale Git restart. |
| BLOCKED | Any future Terraform infrastructure update | Recorded replacement proposal must be resolved by a separate reviewed bootstrap-version maintenance plan; no apply, state editing or guard removal. Not a reason to replace this host for a code release. |

The [cutover runbook](AWS_CUTOVER.md) supplies the ordered execution and recovery
boundaries. Follow it only after the corresponding approval; never bundle every
row into implied permission from a successful synthetic test.

## Executed local checks

The [AWS-only policy validation](AWS_BACKUP_POLICY.md#local-validation-26-october-2026)
adds seven receipt/health tests: **252 offline passed on each Python 3.11/3.12/3.13**,
**141 PostgreSQL tests**, **19 deployment/protection tests** and **five Terraform
mock tests**, all final runs without skips. Archive integrity/security and actual
fresh-target restore checks remain. No host/credential/state changes or new AWS
inspection occurred in this policy stage. Logs are private outside Git.

Earlier release-preparation baseline:

- **245 offline tests passed on each Python 3.11.16, 3.12.14 and 3.13.12**, no skips;
  eight new integrity/age, failure/privacy, code-whitelist and cadence tests.
- **141 PostgreSQL tests passed**, no skips: PostgreSQL 17.11, pgvector 0.8.7,
  Python 3.12.14/psycopg 3.2.13, cached offline real-model inference. Includes actual
  import/repeatability/reconciliation/pg_dump/pg_restore, security, concurrent
  publication/rescreening, owner/calendar/MCP and isolated vector-aware recovery.
  The real dump additionally passes the new age/integrity validator after simulated
  S3 receipts; that simulation is not a new hosted upload.
- Schedule parsed with `systemd-analyze calendar --iterations=5
  '*-*-* 00,06,12,18:00:00 UTC'`; no service/timer was started.
- Terraform 1.17.0: backend-disabled local init, format/validate and **5 mock tests**
  passed. **19 deployment tests** passed, including real non-root installer/CLI
  permission checks in network-disabled containers and EBS/backup deletion-refusal
  plans against fake local state. No live AWS backend, plan, inventory or mutation.
  The first deployment run lacked the required CLI archive; rerun passed after
  fetching the pinned public installer/signature and verifying it with GnuPG.

Principal commands: `PYTHONPATH=src <pinned-python> -m unittest discover -s tests -v`;
`HF_HUB_OFFLINE=1 JOBWATCH_REAL_MODEL_TEST=1 HF_HOME=<existing-cache>
PYTHON=<model-venv>/bin/python bash scripts/test_postgres.sh`. Both use isolated
fixtures, not production records. Private local logs are outside Git.

The code-only release was built from frozen code commit
`43aab3b12e3277c96b3b0936839a6758ebda986b`. Archive SHA-256:
`f08b1911157428329485c27a94bf91388407444cc1d3c1fdcea208e3291ec31e`.
The existing installer verifier accepted its manifest/inventory/hashes. All four
migrations match Git and are present in the wheel; the four reviewed provisioning
SQL files and four hash-locked wheels are included. Native dependency tags target
ARM64/Python 3.12. No tracker data/config, Git metadata, model weights or credentials
are shipped. The original x86 build did not qualify ARM64 execution. Subsequent explicitly
approved [hosted maintenance](AWS_V1_MAINTENANCE_REPORT.md) uploaded and installed
this exact artifact and passed native checks. Later documentation-only commits
do not change this frozen code identity or authorise another deployment.
