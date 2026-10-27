# Graduate Job Watch V1.0.0

> **Historical snapshot:** This report captures the 27 October V1 database release *before* the separately deployed ChatGPT and calendar integrations. For their subsequent operational status and authority boundaries, see [the 27 October integration release](INTEGRATION_RELEASE.md). Do not read the original integration-pending statements below as current status.

**Released 27 October 2026.** PostgreSQL on the existing AWS host is authoritative;
the Git collector is retired. [Start here](USER_GUIDE.md) for daily use, or use
[the handover](AWS_PRODUCTION_HANDOVER.md) for access, schedules and recovery.
This is a private repository/release; there are no attached data or backup assets.

## Shipped scope

- Validated Greenhouse, Lever and Ashby collection across the existing nine feeds;
  per-source transactions, health/freshness, stable IDs and fail-closed absence.
- Immutable parsed source evidence/versions, material history and honest legacy
  provenance; exclusions retained for research, not filtered out of storage.
- PostgreSQL17.11/vector0.8.7, migrations0001–0004; ordinary SQL, BM25 and FTS.
  Exact vector/hybrid retrieval is implemented and locally tested, but not populated
  in production. Local text-only EmbeddingGemma2 inference stays off the 2 GiB host.
- Audited owner SQL for applications, triage, leads, follow-ups and verifications,
  with collector/owner/migration privilege separation.
- Stable, privacy-minimised deadline/action ICS snapshots and five read-only MCP
  tools. External calendar/ChatGPT production sharing is not activated.
- Existing ARM64 EC2, protected encrypted EBS, private versioned S3, SSM, operational
  logs, CloudWatch/SNS email and independently verified real-data restoration.

## First actual unattended operation — 27 October

Read-only inspection after both schedules passed; no service or collection was
started for this verification.

| Operation | Genuine execution evidence |
|---|---|
| Midnight backup | Timer `LastTrigger` and service start both **00:00:13 UTC**; service exit0 at **00:00:23**. Conservative snapshot start 00:00:13.465841; completed receipt 00:00:22.033663. |
| Completed S3 backup | **13,033,724-byte custom archive** and manifest retrieved by exact version. AES256, S3/local SHA-256, sizes and manifest-to-archive-version linkage verified; fresh completion receipt. Retention/protections unchanged. |
| Daily collector | Timer trigger and service start both **06:17:54 UTC**; service exit0 at **06:18:24**. Nine database-issued runs all `published`, no recorded errors; **620 positive observations**, eight absence-safe feeds and one positive-only feed. |
| History correctness | Unchanged responses reused versions: **zero new material events**. No positive-only absence events. Owner rows/audit unchanged since cutover. |
| Monitoring / exclusivity | Actual backup/collect exit0 and fresh health events delivered to CloudWatch. All four alarms OK with notification actions. Three timers enabled/active. Git workflow disabled, definition inert, no queued/running Git writer. |

| Feed | Published positive observations | Absence inference for this run |
|---|---:|---|
| Ashby hyperexponential | 32 | Safe |
| Ashby incident.io | 33 | Safe |
| Ashby Tracebit | 3 | Safe |
| Greenhouse Cambridge Consultants | 91 | Safe |
| Greenhouse Monzo | 70 | Safe |
| Greenhouse Marshall Wace technology graduates | 5 | Safe |
| Greenhouse Riverlane | 36 | Safe |
| Lever Palantir | 307 | **Positive-only**, offset pagination |
| Lever Scott Logic | 43 | Safe for this single short page |

Dated corpus: **636 retained postings**, including 579 exclusions; 1,633 versions,
740 events, 44 employers, nine sources, 34 leads/programmes and 24 verifications.
There are 622 listed and 14 `not_seen` records. Listed is not proof of an open form,
and the two retained positive-only records were not positively observed in this run.
No application/triage records were added; the private ledger was not changed.

Backups remain **00:00/12:00 UTC**; collection **06:15 UTC + up to five minutes**;
health every fifteen minutes. Actual next runs at inspection: backup 27 October
12:00, collection 28 October 06:16:14. These future executions are not predeclared
successful. Existing delivered failure/stale/missing-heartbeat and recovery emails
remain the notification proof; no new alarm email was expected while alarms stayed OK.

## Release checks and recovery

- **258 offline tests on each Python3.11.16/3.12.14/3.13.12**, with optional MCP
  dependencies present and no skips. **147 disposable PostgreSQL tests** passed on
  Python3.12.14/psycopg3.2.13, PostgreSQL17.11/vector0.8.7; cached real-model tests enabled.
  The three added tests execute this release's actual user-guide SQL, including
  all-verdict evidence reads, audited bookmark/application replay, stale refusal
  and rollback on an unknown key. No synthetic owner writes were made on AWS.
- Markdown lint, local links/anchors, GitHub Mermaid rendering, version consistency,
  frozen-data equality, packaging and release privacy checks passed.
- Three real production archives were independently restored at cutover into
  fresh network-disabled PostgreSQL17/vector0.8.7 targets. Schema, migrations,
  evidence, owner/audit records, grants and sequences matched; the initial archive
  reconciled to frozen C, later archives retained that immutable baseline.
  Midnight's exact S3 completion/integrity was checked again for this release;
  it is not presented as another restore rehearsal.

Exact commands and non-sensitive outcomes are recorded in the handover. Private
receipts, version IDs, logs, archives, infrastructure identifiers and records stay
outside Git and release assets. Package/source version is now **1.0.0**; the running
host remains the verified, hash-pinned code release `e3807a0`. Its runtime code and
schedules were not redeployed for a documentation/version closeout.

## Final scope boundaries and known limitations

- Coverage is bounded to nine feeds, not the graduate market. Screening is not a
  personal fit judgment; `not_seen` is not confirmed closure. Legacy H1/H2 and the
  unpublished 26 October runner snapshot remain [explicitly accepted](LEGACY_EVIDENCE_ACCEPTANCE.md).
- AWS-only twice-daily dumps are not a guaranteed twelve-hour RPO or PITR. Missed
  backups, project/Region lockout and administrator deletion remain risks. Keep
  monitoring and separate-target restore practice; never restart stale Git.
- Production vector coverage is zero. Optional vector population/local inference,
  authenticated ChatGPT real-data access and revocable Proton HTTPS subscriptions
  are follow-ons, not blockers to SQL research and owner workflows.
- The general readiness CLI expects an older Stage3 restore receipt. It does not
  consume the separately verified format2 production restore proof; the handover
  records the actual recovery evidence without fabricating a generic green result.
- A known Terraform user-data change would propose replacing the protected EC2
  instance. It remains blocked; no apply, guard removal or state workaround was
  part of this release. Future infrastructure maintenance needs its own review.

## Provenance and integration

Frozen import source **C**: `9bbcb0149d41250cc2e56b2cbb25779bd5fafc16`.
The complete feature chain and newer observation commits are integrated into main.
`data/`, `config/` and `reports/` remain byte-identical to C; current observations
and owner changes live only in PostgreSQL. Do not rerun an import or file writer.

Validation commands: `PYTHONPATH=src python -m unittest discover -s tests -v` for
each supported interpreter; `HF_HUB_OFFLINE=1 JOBWATCH_REAL_MODEL_TEST=1
HF_HOME=<existing-cache> PYTHON=<model-venv>/bin/python bash scripts/test_postgres.sh`;
`uv build --wheel --out-dir <private-directory>`; Markdown lint, link/anchor checks,
Mermaid CLI rendering and `git diff --check`. Hash-locked optional read dependencies
came from `requirements/agent-read.lock`; tests used no production database URL.
