# V1 production release checklist

**Executed 26 October 2026 under the owner's final production GO.** PostgreSQL is
now authoritative. This is the compact current ledger; dated preparation reports
remain historical. See [production handover](AWS_PRODUCTION_HANDOVER.md) for actual
recovery evidence, SQL access and operating instructions.

**V1 release closeout, 27 October:** [actual unattended operation](V1_RELEASE_REPORT.md)
verified midnight S3 completion and 06:17–06:18 UTC publication by all nine sources.
No errors or new material events; owner state unchanged, alarms OK. The following
ledger retains the cutover checks; ordinary scheduled operation is now observed.
[Daily user guide](USER_GUIDE.md).

## One release checklist and approval ledger

| Gate | Status | Actual evidence / remaining boundary |
|---|---|---|
| Owner GO and legacy acceptance | VERIFIED | Owner explicitly accepted H1/H2 and the unpublished 26 October runner-snapshot caveat; approved freeze, import, real recovery and single-writer switch. [Acceptance record](LEGACY_EVIDENCE_ACCEPTANCE.md). |
| Writer freeze and final C | VERIFIED | Old workflow disabled; no queued/running writer, no local owner/file writer. Frozen at 20:35 UTC: `9bbcb0149d41250cc2e56b2cbb25779bd5fafc16`. Complete Git history/bundle verified. |
| Integration | VERIFIED | Both feature history and newer observation commits preserved. Main advanced normally; `data/`, `config/`, `reports/` remain byte-identical to C. Retired workflow has no schedule or writer. No force-push or stale snapshot replacement. |
| Final plan and single import | VERIFIED | Plan SHA-256 `96e99a0a0b41a408933b963142e126b9abcebde9a23ad99e9e6b7b28c1a22f7f`. One committed import: 631 postings/574 exclusions, 1,013 versions, 110 events, 44 employers, nine sources, 34 leads/programmes, 24 verifications, zero applications/triage. 87 imported health runs; all 3,510 text references resolve. |
| Reconciliation | VERIFIED | Exact imported datasets, evidence/provenance and audit images matched C. An initial timeout fully rolled back; emptiness was independently proved before the one committed retry with an operator-only longer timeout. Sequence gaps retained. |
| Real-data independent recovery | VERIFIED | Imported-baseline, activated-source and post-scan actual S3 archives restored into separate network-disabled PG17.11/vector0.8.7 targets. Exact schema, security, data, audit, migrations and sequences; baseline reconciled to C, later immutable baseline retained. Exact versions/SHA-256/AES256/manifest linkage and populated-target refusal checked. |
| Genuine sources | VERIFIED | All nine actual production sources checked 21:13–21:14 UTC, then activated through their same-source receipts and existing mapping evidence. No added/dropped source. [Dated source record](ATS_SOURCE_VALIDATION_REPORT.md). |
| Controlled collection | VERIFIED | Actual installed service, 21:23:29–21:23:55 UTC: nine published runs, 620 positives, eight absence-safe and Palantir positive-only. No false positive-only absence or invented legacy employer edit. Imported immutable history and owner state unchanged. |
| Current corpus | VERIFIED | 636 postings, 1,633 versions, 740 events; 579 exclusions; 34 leads, 24 verifications, zero applications/triage. 96 owner/control audits, unchanged by collection. Counts are dated observations, not market coverage. |
| SQL operations/security | VERIFIED | Actual owner-role daily/weekly/requirements, BM25/FTS reads passed. PG17.11/vector0.8.7 and migrations0001–0004 unchanged; role denials, HBA, loopback/socket, protected mount and release verified. Temporary migration password removed and HBA restored exactly; no new permanent credential. |
| Scheduling and exclusivity | VERIFIED | Existing systemd collector enabled at 22:31 UTC for 06:15 UTC daily + up to five minutes. No immediate duplicate scan. Git workflow disabled and inert; no alternative cron writer. Backup/health timers remained enabled. |
| Backup monitoring | VERIFIED | 00:00/12:00 UTC, 30-day current + 30-day noncurrent S3 retention, fifteen-minute health checks. Fresh actual post-scan receipt; four alarms OK with configured actions. Prior failure/stale/missing-heartbeat ALARM and recovery emails owner-confirmed. Future scheduled runs must be monitored, not predeclared successful. |
| Operating handover | VERIFIED | Main uses SQL-first AGENTS/CLAUDE guidance and retired workflow. Frozen Git remains provenance; never automatically restart it after authoritative SQL writes. [Runbook](AWS_CUTOVER.md) and [backup policy](AWS_BACKUP_POLICY.md). |
| Real-data ChatGPT / Proton | NEEDS OWNER DECISION | Separate sharing scope, authentication/credential custody and revocation; no production read login, public endpoint, tunnel or calendar feed activated. Synthetic ChatGPT success is not real-data approval. |
| Embedding population | READY FOR APPROVED EXECUTION | Exact vector/hybrid implementation available; production currently has zero embeddings. Index/query inference stays on suitable local hardware, off the 2 GiB host. SQL/BM25/FTS are live without vectors. |
| Future Terraform operation | BLOCKED | Known bootstrap/user-data change proposes protected EC2 replacement. No apply, guard removal or state workaround. Resolve separately before any infrastructure change. |

## Recovery and known limits

Frequency is **not** a maximum twelve-hour RPO. Failed/missed runs, AWS project
lockout, Region failure or administrator deletion can cause greater loss. The
owner-selected AWS-only design has no external custodian, worker or replication.
Restore exact archive/manifest versions to a separate target; preserve later valid
observations and decisions before promotion. Do not reset a populated database.

The installed service's three-start/six-hour limit was reached by successful
qualification backups. One failed-counter acknowledgement allowed the verified
post-scan backup; the limit and permanent units were unchanged. Routine cadence
fits the existing limit. Investigate real failures rather than repeatedly resetting.

The generic readiness CLI still consumes the older Stage3 restore-rehearsal receipt;
its `local_restore_rehearsal` label is not passed by inventing one. Independently
verified format2 production restores are recorded in the handover/private proofs.

## Software and validation references

Installed application R: `e3807a084f837ede9754b406a5813a22965fa298`, artifact SHA-256
`7660c46153ddc1f301e91ea351fba22184fc03a082a6e1b2a964e5a5e5f9931a`.
Native ARM64/Python3.12.3/psycopg3.2.13; all four migration contents unchanged.
No model inference, Terraform apply, new infrastructure or public exposure for cutover.

Earlier full preparation: 258 offline checks on Python3.11–3.13 and 144 disposable
PostgreSQL17.11/vector0.8.7 checks, including cached real-model qualification; see
[the pre-cutover ledger](https://github.com/keremnv/grad-job-tracker/blob/feb3dbc2d90a35bc47296ed9b4dc39c2159d2746/docs/V1_RELEASE_CHECKLIST.md).
Current cutover adds actual native reports/search, nine provider publications,
three real-data independent restores, immutable/owner integrity and operational
schedule/security/monitoring checks. Private receipts remain outside Git; infrastructure identifiers and application
records are not published in this repository.

Final integrated-checkout rerun: **258 offline + 144 PostgreSQL tests passed** on
Python3.12.14/psycopg3.2.13; PostgreSQL17.11/vector0.8.7, including the cached real
model and import/restore suite. No skips. Markdown lint/relative links/Mermaid and
Git whitespace checks passed; full commands are in the handover.
