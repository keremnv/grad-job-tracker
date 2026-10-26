# V1 code-only AWS maintenance — 26 October 2026

**Installed and qualified; production NO-GO, Git remains authoritative.** The owner
withdrew the midnight-backup prerequisite for this code update, retaining it as a
pre-cutover requirement. No source request, production import or authority change
occurred during maintenance.

| Check | Actual result |
|---|---|
| Reviewed release R | `e3807a084f837ede9754b406a5813a22965fa298`, containing Riverlane fix `254e2a6` |
| Code-only archive | SHA-256 `7660c46153ddc1f301e91ea351fba22184fc03a082a6e1b2a964e5a5e5f9931a`; 5,145,999 bytes; 84 manifest files; ARM64/Python 3.12, four migrations, hash-locked wheels; no data/config/secrets/model weights |
| Installation | SSE-S3 code upload and version/checksum-verified host download; same trusted installer; install and safe replay passed. Previous `19ee933` artifact independently verified and retained for guarded rollback; rollback was not needed. |
| Maintenance interruption | Backup/health timers disabled/stopped **17:13:21.315–17:13:29.932 UTC**, **8.617 seconds**. No service/DB work in flight; next backup midnight. Original states/next execution times captured privately. Both timers restored enabled/active; collection remained disabled/inactive. |
| Native checks | **11 feed tests passed** as the non-root collector, with synthetic callbacks only. Imports passed as all four OS identities: native aarch64, Python 3.12.3, psycopg 3.2.13. Manifest bytes/root ownership and absence of non-root write permissions verified. |
| SQL-first operation | Read-only SQL as the existing peer-authenticated owner passed, including vector access; daily/weekly/requirements JSON reports and BM25/FTS commands passed against the empty intended database. An authenticated SSM loopback forwarding probe reached PostgreSQL, correctly required SQL authentication and was closed. No owner password was created; a fully authenticated SQL-only tunnel remains a separate access setup. |
| Database | PostgreSQL 17.11, pgvector 0.8.7, native cosine checks and migrations 0001–0004 correct. Same encrypted EBS mount; HBA/configuration/roles/grants/triggers unchanged; PostgreSQL not restarted. All twelve intended domain/audit/embedding tables empty. No migration or password operation. |
| Backup compatibility | Installed systemd units and migration files identical to the previous release. Last-good receipt unchanged and fresh. Existing exact-version S3 manual archive restored independently with code R into isolated local PostgreSQL 17.11/vector 0.8.7; exact data/schema/security/sequences and populated-target refusal passed. This was the existing empty archive, not a new scheduled backup or real-data rehearsal. |
| Monitoring | Post-install health check delivered successfully; actual scheduled **17:15 UTC** heartbeat received as healthy. All four CloudWatch alarms OK. No SNS/IAM/monitoring-rule/retention change. |
| Infrastructure | Protected 25-entry S3-backed Terraform state unchanged; no lock outstanding, apply or replacement. EBS encrypted/in-use, data deletion-on-termination false, API termination protection, IMDSv2, zero inbound rules. |

The first **27 October, 00:00 UTC** scheduled backup has **not yet occurred** at this
report. Timer reload can expose a persistent activation timestamp; that alone is
not service-execution or archive-completion proof. Verify the actual invocation,
new conservative snapshot time, exact S3 archive/manifest versions, checksums,
receipt, health and alarm state after the slot. No background verification or
cutover process has been started.

Two private operator-probe mistakes were corrected: the first psql invocation
omitted `-U jobwatch_owner`; the next omitted the documented
`OPERATOR(public.<=>)` qualification because runtime search paths exclude public.
The corrected read-only probe passed. These were not application or grant defects;
no reviewed software, schema or security setting was changed to make them pass.

## Final local qualification and recovery preparation

- **258 offline tests passed on each Python 3.11.16, 3.12.14 and 3.13.12**, no skips.
  Command: `PYTHONPATH=src <python> -m unittest discover -s tests -v`.
- **144 disposable PostgreSQL tests passed**, no skips: PostgreSQL 17.11/vector
  0.8.7, Python 3.12.14, psycopg 3.2.13. Command: `HF_HUB_OFFLINE=1
  TRANSFORMERS_OFFLINE=1 JOBWATCH_REAL_MODEL_TEST=1 HF_HOME=<existing-cache>
  PYTHON=<model-venv>/bin/python bash scripts/test_postgres.sh`. Includes actual
  import/reconciliation, backup/restore, permissions, concurrent publication,
  Riverlane fail-closed checks, owner/calendar/MCP and cached model inference.
- The private final-import recovery adapter uses the existing
  `database_backup.restore` and `importer.reconcile`, not a second restore engine.
  Its independent nonempty synthetic test retained **3 postings, 16 versions,
  15 events, 3 applications and 1 triage record**, plus configuration, leads,
  verifications and audit. The disposable source was removed before restoring
  into a newly labelled, network-disabled local target. Exact rows/schema/security,
  sequences and frozen-Git reconciliation passed; populated-target refusal passed.
  Restore/verification took 2.212 seconds for this tiny fixture, not a production
  RTO. Its manifest completion receipt was simulated; no AWS upload or real-data
  import occurred in this test. Actual real-data S3 recovery remains after GO.
- Markdown/link checks, inactive handover-template checks and `git diff --check`
  passed. Active workflow/instructions and all data/config/runtime files remain
  unchanged by this documentation commit. Private test logs, adapters and receipts
  remain outside Git; the existing synthetic-only committed recovery guard stays.

## Git/source preparation and operational caveat

Remote main is still `9bbcb0149d41250cc2e56b2cbb25779bd5fafc16`; the release contains
its complete history and unchanged data/config/active workflow. Every worktree was
clean before preparation; local main has no unpublished changes and is simply
behind remote. Full Git fsck and a private complete preparation bundle passed.
This bundle is **not final frozen commit C**.

The 26 October GitHub scan read eight feeds successfully, failed one, and reported
nine changes. Its runner created short commit `95b7f71`, but **both pushes were
rejected by GitHub with Internal Server Error**. Remote main did not advance.
The available logs contain no full new SHA; lookup of the short SHA returned 422,
so the complete runner-only snapshot was not recovered. Logs and available public
validation evidence were preserved privately. Existing committed history is intact;
these unpublished updates must not be claimed as imported/recovered or reconstructed
with guessed timestamps. The workflow remains active, with no in-flight run at
inspection; do not rerun it or change main under preparation authority.

The consolidated decision must disclose this additional operational gap. Re-fetch
before freeze: a subsequent legitimate scan may advance main, requiring fresh
integration/plan. The final owner decision must identify the retained source being
accepted; H1/H2 acceptance does not silently cover unexplained new reconciliation
errors. The current plan has no missing retained evidence references or hash errors.

No new infrastructure or meaningful recurring resource cost: existing host,
backup/monitoring and private S3 were reused. Code artifact requests/storage are
ordinary small usage, not a verified bill. Current credit expiry/spend limits and
total monthly charges were not inferred from the historical host estimate.
Private host identifiers, logs, receipt/version links, full Git bundle and dumps
remain outside Git. See the [single release checklist](V1_RELEASE_CHECKLIST.md)
for the consolidated approval boundary and remaining checks.
