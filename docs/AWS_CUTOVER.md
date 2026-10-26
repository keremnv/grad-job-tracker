# AWS authority handoff — prepared, not executed

The [26 October host maintenance](AWS_V1_MAINTENANCE_REPORT.md) qualifies release
`43aab3b`, PostgreSQL 17.11, public.vector 0.8.7 and migrations 0001–0004. Intended
domain tables remain empty; both timers remain disabled/inactive. Format-1
pre-upgrade recovery and vector-aware format-2 synthetic hosted/independent restores
passed. Twice-daily backup/monitoring activation and real-data restoration
remain open; this runbook does not authorise their execution.

The owner's [AWS-only policy](AWS_BACKUP_POLICY.md) now specifies **00:00/12:00 UTC**
backups to existing versioned S3, approximately 30-day retention, process/age/missing-run
monitoring and confirmed SNS email. It supersedes the strict maximum-loss, six-hour,
external-custody and fencing proposals. **Frequency is not a maximum RPO:** missed
runs, project lockout and regional failure can cause greater loss. The [release
checklist](V1_RELEASE_CHECKLIST.md) remains the current approval ledger. Synthetic
ChatGPT SQL connectivity is complete; production access and Proton hosting are not.

This runbook grants no permission for AWS changes, import, credentials, live
checks, timer/workflow activation, main merge or authority switch.

## State and infrastructure approval boundary

The state bucket and tracker infrastructure already exist in the committed
qualification record. Do not bootstrap or deploy them again. Preserve the
[protected state procedure](../infra/aws/state/README.md), native locking, retained
versions and runtime state-access denials. The recorded user-data replacement is
blocked by `prevent_destroy`; no current Terraform apply is approved. Code-only
releases use the existing artifact installer, not cloud-init replacement.

## Deployment and source gates

Before provisioning, the owner reviews a fresh private Terraform plan, estimated post-credit cost, credit eligibility/expiry, spending-limit status and permissions. Explicit apply approval is separate. Preserve encrypted state, a recovery custodian and a scoped deployment identity; do not edit the project's `/managed/` roles. Verify the chosen subnet/AMI are still available. A plan proves API compatibility for reads, not permission to create resources. `prevent_destroy` deliberately blocks ordinary replacements and must not be removed to make a plan pass.

After an approved deployment, keep both timers disabled. Verify cloud-init completed, exact EBS mount/UUID, PostgreSQL 17, Python 3.12 ARM64, patched SSM Agent and authenticated SSM channels, no inbound SG rules, loopback-only SQL, root/OS/socket permissions, role denials, migration checksums, S3 checksum/version receipts, logs, console alarms and CPU Standard mode. Reboot and prove the data mount is required; exercise an approved replacement recovery without formatting existing storage. Application installation, restricted SQL access, migrations, synthetic S3 backup/fresh same-host restore, reboot/persistence and log delivery have passed on the existing host. Synthetic off-host data restoration is now qualified separately. Twice-daily scheduling, alert delivery, backup freshness and production data/load remain unqualified. The current report takes precedence over historical preflight status. Do not repeat deployment or replace persistent resources to address the CLI defect.

V1 software/extension maintenance has passed; do not reinstall PostgreSQL, vector
or migrations unnecessarily. Deploy only the reviewed backup-policy software update,
with all timers still disabled until separately authorised. Existing restore targets
must match role flags, DB grants/settings and vector 0.8.7 ownership; keep inference
off this 2 GiB host.

Before freezing production, separately qualify the [AWS-only monitoring setup](AWS_BACKUP_POLICY.md):
confirmed S3 completion receipt, failed/stale/missing backup and checker-heartbeat
alarms, confirmed SNS recipient and real delivery. Enable only backup/health timers
when explicitly authorised; collection remains disabled. No external-copy job or
automatic writer fencing is required. Record actual restore results, never a promised
12-hour maximum loss bound.

All nine previously enabled sources still need separately authorized **real endpoint checks**, not fixtures or historical mapping timestamps:

- `ashby:hyperexponential`
- `ashby:incident`
- `ashby:tracebit`
- `greenhouse:cambridgeconsultantslimited`
- `greenhouse:monzo`
- `greenhouse:mw-tech-grad`
- `greenhouse:riverlane`
- `lever:palantir`
- `lever:scottlogic`

With separate live-check approval, inspect mappings and run initial
`jobwatch-pg-ops source inspect/check` checks in a **non-authoritative qualification
database**, preserving provider/board/region and dated receipts. These checks do not
publish postings. Do not seed checks/sources in the intended import target first:
the importer deliberately refuses populated domain tables. Do not copy a synthetic
or unrelated source's run as validation evidence. After final import, repeat/persist
genuine checks against its actual sources, then use owner-authorised `source activate`
with the latest successful same-source run. Endpoint validity alone is insufficient
employer association evidence. Renamed/failed/ambiguous boards require an explicit
coverage decision; do not silently drop them or expand employers.

Owner acceptance of these unavailable legacy facts is pending:

1. Schema-2 snapshots did not archive complete current native ATS responses. Retained normalized fields/descriptions and genuine recovered native Git evidence remain labelled honestly; the next real native observation creates a baseline, not a fabricated past employer edit.
2. Roles rejected before coverage-first schema 2 and never indexed have unknown identities/counts. Import cannot reconstruct them. Future successful PostgreSQL collections retain excluded postings and full parsed provider records, not original HTTP bytes.

Record explicit acceptance, actor, timestamp and final reconciliation references privately. Do not infer acceptance from this implementation brief. Newly missing historical references at the final commit require separate review; old successful rehearsal results do not pre-accept them.

## Integration and freeze

1. Build a reviewable integration branch preserving **all** legitimate current-main commits and the complete PostgreSQL feature chain. Merge current main into that integration branch normally; inspect changes to data/config/owner files and resolve any conflict without replacing newer data with the old rehearsal tree. Never squash a stale data snapshot over production main. Test code again. Keep the active workflow unchanged until the authorized freeze/switch.
2. Prepare the code-only artifact from an explicit integrated application commit. Its hash and commit are independent of the eventual authoritative **data** commit. Deploy while the timers remain disabled. No application import/migration on scheduled startup.
3. Close initial source/coverage, legacy-acceptance, host/current-recovery,
   backup/monitoring and access-preparation gates before requesting a freeze.
   Obtain an explicit owner freeze window. Disable the old file-backed schedule
   only with approval, wait for every in-flight run/retry to finish, and stop owner
   ledger/config/triage/verification writes. Preserve and push legitimate pending
   edits. GitHub concurrency groups alone do not prevent two authorities.
4. Fetch the real production main after the freeze and resolve its full commit **C**. Verify remote/local equality and that no writer advanced it. Check `git rev-parse --is-shallow-repository` is `false`, `git fsck --full`, and `git cat-file -e C^{commit}`. Preserve a private complete Git bundle/tag and source-file hashes. **Do not use `90434ea...` automatically.** That is an old rehearsal, not the final source. Do not expose owner input in artifact/log output.
5. On the operator's complete frozen checkout, prepare `jobwatch-pg-import plan --repo /PRIVATE/repo --commit C --report /PRIVATE/new-plan.json`. Inspect all datasets, hashes, recovery limits, distributions and activation gates before authoritative import. No network collection or re-screening during import.

## Final import and independent proof

Use the ordinary migration identity, never collector/owner credentials. The simplest no-extra-copy path is an **approved temporary local-only migration tunnel**: the administrator adds a narrowly scoped loopback SCRAM HBA entry for `jobwatch_migrator` on the target DB, sets a temporary password with interactive `psql \password`, reloads PostgreSQL, and opens the same authenticated SSM localhost tunnel. No public listener/port or permanent AWS key. Store the operator's libpq service/passfile outside Git with `0600`, and set `JOBWATCH_MIGRATION_DATABASE_URL` through that service. Run importer locally against the complete frozen Git repository. Close the migration session, revoke the temporary password (`ALTER ROLE jobwatch_migrator PASSWORD NULL`), remove the temporary HBA entry and reload after import/reconciliation. The permanent migration/backup path remains Unix peer; routine agents do not receive this credential. Alternatively, explicitly approve transferring a private Git snapshot to a restricted maintenance location; **never place private input under the instance-readable artifacts/ prefix**.

```sh
# Prepared commands only; require hosted-data and credential approval first.
jobwatch-pg-import import --repo /PRIVATE/repo --commit C --report /PRIVATE/new-import.json
jobwatch-pg-import reconcile --repo /PRIVATE/repo --commit C --report /PRIVATE/new-reconcile.json
jobwatch-pg-ops readiness --reconciliation /PRIVATE/new-reconcile.json --restore /PRIVATE/new-restore.json
```

Use actual CLI help for required report paths; reports must be new private files. The target must be empty except applied migrations; the importer refuses a populated database. If COMMIT acknowledgement is lost, **reconcile** before any retry; never truncate/reset or import twice. A changed frozen source requires a deliberate new baseline, not a synchronization pipeline.

Take a format-2 backup of the imported baseline and independently restore the
**actual downloaded archive**, not a freshly constructed lookalike. Reconcile that
target against C and compare source/restore fingerprints, grants, roles, audit,
sequences, extension and migration checksums. Check exclusions/history, owner-state
hashes and operational queries. Use this real S3 archive, not synthetic proof. A
separate temporary local recovery target is sufficient; no external backup service
is required. Keep collection disabled; retain the separately approved backup/health
schedule, or perform verified manual backups while it is deliberately paused.

The committed `recover_offhost.py` harness deliberately accepts only its synthetic
fixture. Do not bypass its guard or call it a production restore test. The
production restore operation is the existing `database_backup.restore` against a
fresh, independently prepared PostgreSQL 17/vector target with matching role/DB
security. A reviewed operator connection/utility adapter and real-data handling
approval are required; preserve failed targets. Never restore onto `jobwatch`.

Then persist genuine source checks/owner activation on the imported source IDs.
Run report/search/read-identity checks and compare the import baseline in
`--phase operational` so new validation runs/owner audit are not misreported as
lost baseline data. Take and independently verify another pre-promotion backup
including activation facts. Do not claim this post-activation DB is byte-identical
to the frozen baseline: the extra attributed runs/control audit are intentional.

The existing readiness CLI's `--restore` accepts the Stage 3 local-rehearsal receipt,
not the AWS format-2 restore result. Keep actual hosted/separate-target proof in the release
ledger; never fabricate a matching receipt. The CLI always reports
`deployment_ready=false`: it cannot accept history, prove writer freeze or grant GO.

Use the [current release checklist](V1_RELEASE_CHECKLIST.md#one-release-checklist-and-approval-ledger),
not historical daily/strict-RPO proposals. Twice-daily frequency and its AWS-only
risks are accepted; scheduling/notifications still need qualification. No fixed
RTO is promised. A separate-target real-data restore is mandatory after final import.

## Owner go/no-go and switch

**NO-GO** if any endpoint/coverage, source reconciliation, historical acceptance, independent hosted restore, role/SSM, S3 backup/monitoring, production credential, freeze/single-writer or operational-report gate remains unresolved. Record the owner's explicit decision privately; this branch does not supply it.

After separate explicit GO, integrate/deploy the exact reviewed code and update
`AGENTS.md`/`CLAUDE.md` and owner operating instructions to the approved SQL-first
authority. Their current file-backed command/default guidance is not a PostgreSQL
handoff. No stale data-tree replacement. Deliberately retire the old file writer,
disable scheduled observation commits/rebase/push and stop owner file writes.
When systemd is selected, the alternative PostgreSQL Actions template **stays
inert**. Never activate two collectors. Preserve the frozen Git snapshot as recovery
evidence, not an active data source; no deletion of old runtime code is implied.

Keep the collection timer disabled for the first explicitly approved controlled `jobwatch-collect.service` invocation. Confirm actual identity, per-source run receipt/outcome, independent successes/failures, native baselines, source history, health, positive-only Lever behavior, no false `not_seen`, all-verdict research, deadlines/uncertainty, SQL reports/search, unchanged owner hashes and audit attribution. A partially failed scan may already have committed successful sources. Enable the collection timer only after these checks and a fresh verified backup; enable backup scheduling deliberately. Review `systemctl list-timers`, failure/restart limits, journal and CloudWatch output.

Make the post-scan backup and verify exact S3 versions/freshness. Confirm the
approved **00:00/12:00 UTC** backup and 15-minute health timers are active, with
next deadlines and delivered process/missing-run alarms, before enabling routine
collection. Activate them only under the separate backup/monitoring approval;
never start a second Git/database writer or an external copy job.
Qualify owner SQL and the separate production read identity through authenticated
SSM. If online ChatGPT is approved for real data, deliberately establish its own
authenticated tunnel/credential scope and make a bounded production read; the
earlier synthetic test grants no real-data sharing. Keep model inference local.
Proton hosting/subscription has its own privacy, endpoint and approval gate.

## Recovery on both sides of authority

Before authoritative PostgreSQL writes: preserve failed staging evidence, leave the old frozen Git snapshot authoritative, and only resume its writer after confirming no later authoritative transaction would be lost. Candidate endpoint checks are not an excuse to discard provenance.

After any committed authoritative observation or owner change: stop affected timers/services and owner writes, preserve the current DB with a new backup, and restore a verified backup to a **separate** target. Reconcile all valid later evidence/decisions, repair or roll forward, and promote only a checked recovery state. Do not restart the stale Git writer, overwrite immutable evidence, force-detach a live volume or use a Terraform replacement as data recovery. Preserve the damaged DB/archive and state until recovery is accepted. No reverse JSON synchronization or general replay platform is introduced.
