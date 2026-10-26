# V1 release candidate — preflight, not production approval

25–26 October 2026. Branch `feat/v1-release-readiness`, based on portfolio `2cc24c4`.
**Overall: NO-GO for production.** Git remains authoritative. This stage used no
authenticated AWS/Platform calls, live ATS requests, hosted database, tunnel,
timer activation or production import. Public documentation/prices were read.

This is the current compact release record. Older stage reports retain their
historical results; their daily/26-hour recovery proposal is superseded by the
owner's accepted **12-hour maximum data-loss tolerance**.

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

## Hosted compatibility — committed evidence only

Last recorded host: [Stage 4B qualification](AWS_HOSTED_QUALIFICATION_REPORT.md)
and [independent recovery](AWS_RECOVERY_READINESS_REPORT.md), not re-inspected here.

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

## Recovery implementation and 12-hour operating requirement

Prepared source changes, **not installed or activated**:

- Backup timer: **00:00, 06:00, 12:00, 18:00 UTC**, one-minute accuracy, no random
  delay, persistent catch-up when enabled. Deployment still refuses active/enabled
  timers and never enables them. Service duration remains bounded at 45 minutes.
- Format-2 manifests now record `backup_started_at` before the exported transaction
  snapshot. This is a conservative lower bound, not an exact snapshot timestamp.
  Late S3 upload or manifest creation cannot make old data appear fresh.
- `backup_freshness.py` streams local archive hashing, verifies size and the completion
  manifest's exact archive-version linkage, rejects missing/future/reversed timing,
  and reports **warning at 8h, critical at 10h, breached at 12h**. Exit 0 is fresh,
  1 warning, 2 invalid/critical/breached. It makes no cloud call or readiness claim.

```sh
# After approved retrieval of the EXACT trusted manifest/archive versions.
python3 infra/aws/scripts/backup_freshness.py \
  --archive /PRIVATE/database.dump --manifest /PRIVATE/database.json
```

Smallest proposed operating arrangement: retain the existing write-only EC2 →
versioned S3 backup path. An **existing owner-controlled, reliably online backup
worker** retrieves exact completed versions after each six-hour run, checks them,
encrypts a recovery package and verifies its ciphertext copy at an approved
independent destination within one hour of backup completion. The package includes
dump, manifest, trusted receipts, code/migration/extension pins and role/grant
reconstruction instructions. Only ciphertext leaves that worker; the private key
or recovery secret stays in owner-controlled independently recoverable custody,
with an offline recovery copy. An ephemeral Codex directory is not that destination.

Keep all four recovery points/day for **at least 30 days** offsite, test decrypt/
fresh-target restore monthly and after upgrades/key changes, and retain old keys
until their archives expire or are re-encrypted and verified. Take verified manual
backups before migrations/upgrades and before/after the authority switch.
Do not grant the instance backup reads/deletes to implement copying. The offsite
worker needs only approved prefix-scoped list/version-read and destination-write
permissions, with renewable temporary authentication; an expiring browser session
and a sleeping laptop are not unattended scheduling.

Healthy six-hour cadence + 45-minute service bound + one-hour offsite completion
budget + scheduling slack keeps the older recoverable copy approximately under
eight hours. This is a planning bound to measure, not an availability guarantee.
An **independent** check at least every 15 minutes must verify the latest usable
S3 point **and independently retained ciphertext/receipt**, age from backup start,
hash/version linkage, copy-job heartbeat and credential renewal. Test a delivered
warning/failure notification and a missing-run alarm. Existing success/error log
alarms alone cannot detect a backup that never ran. The local validator is only
one component; no copy/monitor/notification job was deployed by this stage.

At 10 hours, the runbook requires pausing **all** collector and owner writers while
repairing backup/custody; at 12 hours the recovery objective is breached. A missed
backup plus unattended alerts otherwise has no finite worst-case loss bound.
**Cutover remains blocked until an owner-approved, tested response/write-freeze
arrangement can protect the 12-hour limit.** Owner SQL is not automatically fenced
by this helper or by stopping only the collector. Do not describe the current
configuration as meeting a hard 12-hour guarantee. If reliable response coverage
is unavailable, a narrowly approved automatic writer-fencing change is required;
neither silent RPO relaxation nor a new replication platform is proposed here.

Four hours during operator availability remains a **proposed RTO**, not accepted
or production-measured. Synthetic sub-second SQL restore timings do not include
host provisioning, production volume, key retrieval or human response. Project-loss
recovery must work from already retained ciphertext, reviewed tools and owner keys
without S3, SSM or the original EC2 host. Fence old writers before any promotion.

## Cost boundary

No new AWS resource or paid service is proposed by these local changes. Use existing
owner storage/worker if suitable. Historical host/state allowance is **£17.53/month**
under its recorded FX/20% VAT assumptions, not a current bill or spending guarantee.
The public Stockholm S3 tariff checked here is $0.023/GB-month, PUT/LIST $0.005/1,000,
GET $0.004/10,000. [Official pricing](https://aws.amazon.com/s3/pricing/) and
[regional tariff](https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonS3/current/eu-north-1/index.json).

Four full dumps/day with the current 30-day current + 30-day noncurrent expiration
can retain approximately **240 archive-sized objects**, not just 120: expiration
adds a delete marker before noncurrent deletion. Every version is charged in full.
[Versioning](https://docs.aws.amazon.com/AmazonS3/latest/userguide/Versioning.html).
For a **hypothetical** 0.1 GB archive this is about $0.552/month storage; for 1 GB,
$5.52/month. Under the previous conversion/VAT scenario, conservatively adding the
whole latter amount to the base already exceeds £20. These are sizing examples,
not measured production backup sizes or a newly approved retention policy change.

Request charges are small at this cadence, but independent-copy internet egress,
notifications, logs, destination storage and worker operation must be priced after
owner selection. Existing Aurora snapshot billed size remains unknown; no snapshot
was inspected or changed here. Credits, expiry/eligibility and spend limit are not
freshly verified and do not approve deployment. Budget GO requires measured archive
growth/retention and those charges, preserving the £10–£20 preference. Do not lengthen
the backup interval to save money; review retention/derived-vector storage first.

## One release checklist and approval ledger

VERIFIED means the scoped evidence exists. READY FOR APPROVED EXECUTION means
reviewable procedure/code, **not permission** or a passed hosted gate. BLOCKED
means a dependency or safety implementation is still missing. No row supplies GO.

| Status | Gate / exact next operation | Required authority, cost and consequence |
|---|---|---|
| VERIFIED | Feature chain + inspected main integrated without data conflicts | Local Git only; main/writer untouched. Refresh before final integration. |
| VERIFIED | Real online synthetic ChatGPT `sql_query`; local five-tool tests | Synthetic runtime stopped locally; plugin/key withdrawal owner-reported, not independently checked. No production sharing implied. |
| VERIFIED | Recorded AWS synthetic recovery; current vector-aware independent disposable restore | No new AWS inspection. Does not prove final real-data recovery/custody. |
| READY FOR APPROVED EXECUTION | **First approval-sized step:** read-only AWS/SSM/SQL/state revalidation | Approve existing-host inspection, 20-resource state comparison, mount/role/migration/timer/log/alarm status and available billing labels. Read-only AWS + SSM host inspection; ordinary read/log request costs only, no repair, credentials or new plan/apply. Stop on mismatch. |
| NEEDS OWNER DECISION | Independent destination, key custody, always-on worker and notification recipient/response | Approve ciphertext destination/retention, renewal identity and private-data handling; owner retains recovery key. Existing storage preferred; worker/egress/destination/alert cost unquoted. 12h tolerance is already accepted, not reopened. |
| BLOCKED | Demonstrate six-hour backup/copy + independent freshness/missing-run monitoring + delivered alerts + all-writer response | These jobs/custody controls are not installed. Later approval must name schedules, scoped read/write permissions, recipient and write-freeze consequences. A validator and daily timer do not pass this gate. |
| READY FOR APPROVED EXECUTION | Code-only host maintenance, pinned vector 0.8.7, migration 0004 and isolated synthetic requalification | After revalidation, approve root code/package install, PostgreSQL admin extension and restricted migrator DDL. Preserve data/roles, disabled timers and Terraform guards; no inference/weights. Existing-host CPU/log usage; no extra infrastructure. |
| NEEDS OWNER DECISION | Explicit legacy-evidence acceptance against final manifest | Missing complete schema-2 native responses and never-indexed pre-schema-2 exclusions cannot be reconstructed. No technical fee; private dated owner acceptance, no invented evidence or employer edit times. |
| READY FOR APPROVED EXECUTION | Nine genuine endpoint checks and employer/region mapping review | Separate live HTTPS approval, initially only in a non-authoritative qualification DB, no posting publication. Repeat/persist final checks on imported source rows before activation; collector validation + audited owner activation. Failed/renamed boards need a coverage decision; no new employers. |
| READY FOR APPROVED EXECUTION | Final code integration, Git/owner writer freeze and C pin | Repository/workflow administration + owner freeze approval. Wait for in-flight work, preserve/push legitimate edits, verify full history/remote equality. Collection pauses; main/workflow changes require a later explicit instruction. |
| BLOCKED | Import C once into empty intended target, reconcile all datasets, then real-data independent restore | Needs prior gates and explicit real-data/temporary migration-access approval. Migrator writes, scoped admin auth setup and approved recovery-operator version reads; private storage/S3/egress costs. Never reset a populated target or count synthetic proof as real-data proof. |
| BLOCKED | Production owner SQL and separately scoped ChatGPT read access | Approve private owner/audit read exposure, server-side credential custody, narrow HBA and authenticated SSM forwarding; no collector/admin fallback. Separate runtime tunnel approval/terms; no restart of the synthetic tunnel or host inference. |
| NEEDS OWNER DECISION | Private revocable Proton-compatible HTTPS feed and actual subscription qualification | Stage 4E snapshots are local, not a live feed. Approve destination/domain/TLS, capability-URL custody/rotation, schedule/monitoring and cost; no public MCP, no notes/CVs. Polling is not guaranteed urgent alerting. Remains V1 work. |
| BLOCKED | Explicit single-writer GO, first controlled PostgreSQL scan, then routine scheduling | Needs all mandatory gates and separate switch/ATS/service approvals. Retire file writers/default instructions, keep alternative Actions collector inert. Inspect first-scan baselines/health/owner hashes; verify backup/offsite point, then approve timer activation. After SQL writes, recovery is roll-forward, not stale Git restart. |
| BLOCKED | Any future Terraform infrastructure update | Recorded replacement proposal must be resolved by a separate reviewed bootstrap-version maintenance plan; no apply, state editing or guard removal. Not a reason to replace this host for a code release. |

The [cutover runbook](AWS_CUTOVER.md) supplies the ordered execution and recovery
boundaries. Follow it only after the corresponding approval; never bundle every
row into implied permission from a successful synthetic test.

## Executed local checks

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
