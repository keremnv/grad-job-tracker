# AWS authority handoff — prepared, not executed

Separately approved infrastructure creation and code-only application installation have occurred. Unchanged migrations are applied to an otherwise empty intended database. Hosted qualification is **blocked by non-root AWS CLI permissions**; see the [actual results](AWS_HOSTED_QUALIFICATION_REPORT.md). This runbook grants no permission for repair, further infrastructure changes, hosted real-data import, secret setup, live endpoint checks, timer activation, workflow disable, main merge or authority switch. Use the [infrastructure instructions](../infra/aws/README.md) and existing [PostgreSQL cutover runbook](POSTGRESQL_CUTOVER_RUNBOOK.md); this document resolves AWS-specific sequencing without reopening the schema.

## State and infrastructure approval boundary

Follow the [protected state procedure](../infra/aws/state/README.md) before infrastructure deployment: first approve only the separate versioned S3 state-bucket bootstrap; then initialize native S3 locking, generate a new reviewed S3-backed plan and obtain separate approval of that saved plan’s digest. The empty-state local review plan is never a deployment artifact. Both timers remain disabled throughout. The state bucket is outside the tracker stack, retains state versions indefinitely and grants no runtime instance access. This document does not authorize either step.

## Deployment and source gates

Before provisioning, the owner reviews a fresh private Terraform plan, estimated post-credit cost, credit eligibility/expiry, spending-limit status and permissions. Explicit apply approval is separate. Preserve encrypted state, a recovery custodian and a scoped deployment identity; do not edit the project's `/managed/` roles. Verify the chosen subnet/AMI are still available. A plan proves API compatibility for reads, not permission to create resources. `prevent_destroy` deliberately blocks ordinary replacements and must not be removed to make a plan pass.

After an approved deployment, keep both timers disabled. Verify cloud-init completed, exact EBS mount/UUID, PostgreSQL 17, Python 3.12 ARM64, patched SSM Agent and authenticated SSM channels, no inbound SG rules, loopback-only SQL, root/OS/socket permissions, role denials, migration checksums, S3 checksum/version receipts, logs, console alarms and CPU Standard mode. Reboot and prove the data mount is required; exercise an approved replacement recovery without formatting existing storage. Application installation, restricted SQL access and migrations have now passed on the existing host. Backup upload is blocked by CLI permissions; reboot/persistence, real S3 restore and log delivery remain unproven. The current report takes precedence over historical preflight status. Do not repeat deployment or replace persistent resources to address the CLI defect.

Rehearse actual EC2 → S3 backup and independent fresh-target restore, including owner records, audit, grants and evidence hashes. A local Docker restore is not this gate. Provision/restore roles and database-level grants first; the dump retains application ownership and ACLs. Secure an independent encrypted off-project backup and demonstrate access when the project is unavailable. Confirm the acceptable dump-only RPO (up to 24 hours), restore time, disk capacity and backup freshness checks. Add an approved alert recipient/budget; no alert is a hard spending cap.

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

Use the existing `jobwatch-pg-ops source inspect/check/activate` operations with attributed actors. Inspect employer association separately; endpoint validity alone is insufficient. Keep renamed/failed/ambiguous boards visible for an owner coverage decision rather than silently reducing coverage. Checks do not publish postings. No new employers during this cutover.

Owner acceptance of these unavailable legacy facts is pending:

1. Schema-2 snapshots did not archive complete current native ATS responses. Retained normalized fields/descriptions and genuine recovered native Git evidence remain labelled honestly; the next real native observation creates a baseline, not a fabricated past employer edit.
2. Roles rejected before coverage-first schema 2 and never indexed have unknown identities/counts. Import cannot reconstruct them. Future successful PostgreSQL collections retain excluded postings and full parsed provider records, not original HTTP bytes.

Record explicit acceptance, actor, timestamp and final reconciliation references privately. Do not infer acceptance from this implementation brief. Newly missing historical references at the final commit require separate review; old successful rehearsal results do not pre-accept them.

## Integration and freeze

1. Build a reviewable integration branch preserving **all** legitimate current-main commits and the complete PostgreSQL feature chain. Merge current main into that integration branch normally; inspect changes to data/config/owner files and resolve any conflict without replacing newer data with the old rehearsal tree. Never squash a stale data snapshot over production main. Test code again. Keep the active workflow unchanged until the authorized freeze/switch.
2. Prepare the code-only artifact from an explicit integrated application commit. Its hash and commit are independent of the eventual authoritative **data** commit. Deploy while the timers remain disabled. No application import/migration on scheduled startup.
3. Obtain an explicit owner freeze window. Disable the old file-backed schedule only with approval, wait for every in-flight run/retry to finish, and stop owner ledger/config/triage/verification writes. Preserve and push legitimate pending edits. GitHub concurrency groups alone do not prevent two authorities.
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

Take a real backup, restore independently and reconcile the restored DB to C and the imported source. Verify all excluded postings remain searchable, descriptions/native evidence/events, private owner hashes and role/audit behavior. Conduct genuine checks and activate the intended sources with original identity/mapping evidence. Take a second pre-promotion backup including activation facts; independently verify it. Keep baseline versus post-activation receipt meanings distinct. The existing readiness command reports local gates; it cannot approve hosting/legacy acceptance or establish that the old writer is stopped.

## Owner go/no-go and switch

**NO-GO** if any endpoint/coverage, source reconciliation, historical acceptance, independent hosted restore, role/SSM, backup custody/RPO, production credential, freeze/single-writer or operational-report gate remains unresolved. Record the owner's explicit decision privately; this branch does not supply it.

After GO, merge/deploy the reviewed code and SQL-first read instructions without restoring old data files. Replace/disable the active file workflow deliberately; no observation JSON/text/report commits, rebase or push. The existing inert PostgreSQL Actions template is an alternative and **must stay inert** when systemd is selected. GitHub Actions must never collect concurrently with the EC2 timer. Retire file-backed owner writers/default reads at the authorized switch; the old snapshot is recovery evidence, not an ongoing operational source. Stage 4B.1 does not delete them.

Keep the collection timer disabled for the first explicitly approved controlled `jobwatch-collect.service` invocation. Confirm actual identity, per-source run receipt/outcome, independent successes/failures, native baselines, source history, health, positive-only Lever behavior, no false `not_seen`, all-verdict research, deadlines/uncertainty, SQL reports/search, unchanged owner hashes and audit attribution. A partially failed scan may already have committed successful sources. Enable the collection timer only after these checks and a fresh verified backup; enable backup scheduling deliberately. Review `systemctl list-timers`, failure/restart limits, journal and CloudWatch output.

## Recovery on both sides of authority

Before authoritative PostgreSQL writes: preserve failed staging evidence, leave the old frozen Git snapshot authoritative, and only resume its writer after confirming no later authoritative transaction would be lost. Candidate endpoint checks are not an excuse to discard provenance.

After any committed authoritative observation or owner change: stop affected timers/services and owner writes, preserve the current DB with a new backup, and restore a verified backup to a **separate** target. Reconcile all valid later evidence/decisions, repair or roll forward, and promote only a checked recovery state. Do not restart the stale Git writer, overwrite immutable evidence, force-detach a live volume or use a Terraform replacement as data recovery. Preserve the damaged DB/archive and state until recovery is accepted. No reverse JSON synchronization or general replay platform is introduced.
