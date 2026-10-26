# Graduate Job Watch

Graduate Job Watch turns public recruitment feeds into a source-grounded record
of opportunities: what was observed, what changed and what remains uncertain.
Built for researching UK 2027 graduate roles in software, systems and applied AI,
it connects evidence-backed search with application triage and dated follow-ups.

**Status · 26 October 2026:** the live tracker still uses Git-backed files.
The PostgreSQL replacement is implemented and locally qualified; an AWS host has
passed synthetic deployment/recovery checks. Production migration is pending.

[Architecture](#how-it-works) · [Engineering decisions](#engineering-decisions) ·
[Example workflow](#a-synthetic-research-to-follow-up-workflow) ·
[Validation](#evidence-and-validation) · [Quickstart](#quickstart)

## At a glance

- **Reliable collection:** Greenhouse, Lever and Ashby ingestion with identity
  validation, source health and failure-safe retention.
- **Evidence and history:** stable posting keys, retained exclusions and immutable
  PostgreSQL versions distinguish new information from interpretation.
- **Composable research:** ordinary SQL, Python BM25, PostgreSQL full-text search
  and exact pgvector similarity, with optional hybrid ranking.
- **Scoped agent access:** five read-only MCP tools; separate, audited owner SQL.
  A real ChatGPT SQL call through a private tunnel passed against synthetic data.
- **Useful owner workflow:** applications, triage, evidenced leads, programme cycles
  and minimal deadline/action calendars share the same records.
- **Operational engineering:** Terraform, ARM64 EC2, protected encrypted storage,
  Systems Manager access, versioned S3 backups and tested independent restoration.

## How it works

The Git-backed collector remains authoritative. The PostgreSQL path is tested
with mock feeds and frozen-snapshot import rehearsals. The dashed import edge is
one-time migration, not ongoing synchronisation.

```mermaid
flowchart LR
    ATS["Public ATS APIs<br/>Greenhouse · Lever · Ashby"]
    ATS --> SCAN["LIVE · GitHub Actions<br/>collector"]
    SCAN --> FILES[("LIVE · Git-backed<br/>observations and reports")]
    ATS -. "mock-feed tests" .-> PUBLISH["LOCAL · validated<br/>source publication"]
    PUBLISH --> PG[("LOCAL · PostgreSQL 17<br/>evidence · history<br/>owner records")]
    FILES -. "one-time import" .-> PG
    OWNER["LOCAL · authorised SQL<br/>applications · triage · leads"] --> PG
    PG --> RESEARCH["LOCAL · research<br/>SQL · lexical · vector<br/>optional read-only MCP"]
    PG --> ICS["LOCAL · minimal ICS<br/>deadlines and actions"]
    PG -. "cutover pending" .-> AWS["HOSTED · AWS qualification<br/>synthetic data only"]
    ICS -. "HTTPS feed pending" .-> PROTON["PENDING · Proton<br/>subscription"]

    classDef live fill:#e8f5e9,stroke:#287a42,color:#122619
    classDef qualified fill:#edf4ff,stroke:#3868a8,color:#172b49
    classDef pending fill:#f5f5f5,stroke:#666,color:#222,stroke-dasharray:5 4
    class SCAN,FILES live
    class PUBLISH,PG,RESEARCH,OWNER,ICS qualified
    class AWS,PROTON pending
```

Coverage is bounded by configured feeds, not the graduate job market. The
25 October 2026 configuration enables **nine ATS boards**, alongside a wider employer
watchlist. Reports expose per-source freshness and failures; queue selection does
not define the research corpus.

## Engineering decisions

The PostgreSQL path makes the following trade-offs explicit.

**Separate evidence from judgment.** Provider wording, collector observations,
screening, independent checks and owner actions are distinct. Excluded postings
remain researchable. `not_seen` does not prove closure, feed presence does not
verify an open form, and screening is not a personal fit judgment.

**Publish atomically.** Runs register before fetching, with database-issued ordering.
A source lock serialises publication; retries/replays cannot duplicate history or
overwrite newer results. Postings, evidence and health commit together. Network
requests hold no locks. Multi-page Lever scans publish positives without inferring
absence; malformed feeds cannot masquerade as empty boards.
[Transaction and absence contract](docs/POSTGRESQL_COLLECTION.md#exact-transaction-and-absence-contract).

**Keep evidence immutable.** Versions retain complete parsed provider records as
hashed, NUL-safe bytes and track normaliser revisions. This preserves parsed values,
not original HTTP bytes. Comparison rules distinguish HTML formatting noise from
material metadata changes; legacy imports label missing native evidence honestly.
History timestamps describe collector observations, not asserted employer edit times.

**Use ordinary SQL with scoped authority.** Agents construct joins and aggregates
through psql/psycopg. Collector, owner, migrator, embedding worker and chat-read
roles have separate grants. Owner audit records the claimed actor and actual
database login, and rolls back with the transaction. MCP remains read-only.

**Treat retrieval as derived data.** BM25 preserves distinctions such as `C++`,
`C#` and `C` that English FTS tokenisation collapses. Semantic retrieval uses pinned,
text-only **EmbeddingGemma 2**, float32 and native **768-dimensional** vectors.
Exact cosine and optional reciprocal-rank fusion join back to retained versions.
Model/input/source hashes identify derivations; missing coverage is visible.
Inference stays local: it is not qualified for the 2 GiB database host.
SQL/BM25/FTS need no model.
[Retrieval implementation and limits](docs/POSTGRESQL_RETRIEVAL.md).

**Make recovery testable.** A frozen-Git importer recovers historical evidence and
reconciles each dataset. Terraform protects persistent EBS, backups and versioned
S3 state with native locking. SSM replaces public SSH/PostgreSQL access.
Hash/version-verified PostgreSQL archives have been restored into fresh targets,
including off-host. The owner-selected V1 policy uses twice-daily dumps to existing
versioned S3, approximately 30-day retention and CloudWatch/SNS email alerts.
Frequency is not a maximum RPO; missed runs, project lockout and regional failure
can cause greater loss. No external backup worker/provider is required.
[Backup policy and activation status](docs/AWS_BACKUP_POLICY.md).

## A synthetic research-to-follow-up workflow

Illustrative snippets for a **fresh disposable sandbox** containing synthetic
posting `greenhouse:test:1` and no application for it—not a live agent transcript.
See [sandbox setup](docs/POSTGRESQL_DEVELOPMENT.md) and the
[tested owner/calendar workflow](tests/postgres/test_owner_calendar.py).

**1. Research broadly and inspect freshness.** In a read-capable SQL session:

```sql
SELECT p.posting_key, p.title, p.verdict, p.status,
       p.last_seen_at, h.last_success_at, h.absence_safe
FROM jobwatch.research_postings p
JOIN jobwatch.source_health h ON h.source_id = p.source_id
ORDER BY p.last_seen_at DESC NULLS LAST, p.posting_key
LIMIT 5;
```

No verdict is filtered out. The optional lexical convenience is
`jobwatch-pg-ops search "Python" --method bm25`; vector/hybrid methods use the same
research path when the local model and derived rows are available.

**2. Inspect the retained source.** The existing read-tool request works through
the local JSON-lines harness or MCP, using the dedicated read identity:

```json
{"tool":"inspect_evidence","arguments":{"posting_key":"greenhouse:test:1","events_limit":5}}
```

The result includes provenance, normaliser identity, parsed evidence,
base64-encoded bytes with SHA-256, and material history. Source text is marked as
untrusted data.

**3. Record an authorised owner action.** Suppose the owner asks to bookmark this
synthetic role and review its requirements in one week. In a separate
`jobwatch_owner` SQL session, with psql `ON_ERROR_STOP` enabled:

```sql
BEGIN;
SET LOCAL jobwatch.actor = 'agent:synthetic-demo';
INSERT INTO jobwatch.applications
    (job_key, posting_key, company, role, url, stage, next_action, next_action_date)
SELECT 'manual:demo', posting_key, company, title, posting_url, 'bookmarked',
       'Review requirements', CURRENT_DATE + 7
FROM jobwatch.research_postings
WHERE posting_key = 'greenhouse:test:1';
COMMIT;

SELECT job_key, stage, next_action_date
FROM jobwatch.applications WHERE job_key = 'manual:demo';
```

The owner follow-up is not an employer deadline or application submission.
Audit attributes the change; collector evidence stays unchanged.
[SQL recipes](sql/postgresql/owner_workflows.sql) cover retries, stale guards,
explicit linking, rescheduling, clearing and rollback.

**4. Refresh the private calendar projection.** With the sandbox owner/read
connection configured:

```sh
jobwatch-pg-ops calendar snapshot \
  --output-dir .local-reports/calendar-demo --initialize
jobwatch-pg-ops calendar status --output-dir .local-reports/calendar-demo
```

Subsequent refreshes omit `--initialize`. `current/actions.ics` contains the dated
action, excluding notes, CV labels and free-form action text. Stable identities,
revision timestamps and atomic replacement prevent churn or partial publication.
Manual export is available; HTTPS/Proton subscriptions remain pending.
Subscription refresh is asynchronous, not guaranteed deadline alerting.

## Evidence and validation

Release-candidate checks, **25–26 October 2026**; hosted results remain dated
qualification evidence, not a fresh host inspection:

| Evidence | What it establishes |
|---|---|
| **252 offline tests** on Python 3.12 (prior 245-test baseline also passed on 3.11/3.13) | Provider/screening regressions, identity, file durability, privacy, calendar revisions, connection boundaries and backup-age/packaging checks. |
| **141 PostgreSQL tests**, including cached real-model inference | Actual migrations, constraints, grants, concurrent publication/rescreening, rollback, importer reconciliation, vector/hybrid queries, owner/audit workflows and local MCP interoperability. |
| **Hosted and independent synthetic recovery** | Real EC2-to-S3 backup, fresh-database restore, reboot/EBS persistence and ARM64-origin archive restoration into isolated local PostgreSQL 17, with row/security/sequence comparisons. |
| **Online ChatGPT synthetic connectivity** | An actual `sql_query` call traversed a private OpenAI tunnel to the restricted disposable database. Only this tool was exercised online; the remaining tools have local interoperability tests. |

[Connectivity test results](docs/AGENT_CONNECTIVITY_REPORT.md) ·
[Hosted qualification](docs/AWS_HOSTED_QUALIFICATION_REPORT.md) ·
[Independent recovery and its limits](docs/AWS_RECOVERY_READINESS_REPORT.md).

Provider regression fixtures include incident.io's formatting-only HTML changes
and a substantive Cambridge Consultants `Job Summary` update.

The real-model smoke test proves the implemented inference/retrieval path works;
it is not a relevance benchmark. Synthetic recovery does not establish
production-scale recovery time. Twice-daily AWS-only scheduling and email
monitoring still require authorised activation; no strict maximum-loss guarantee
or external custody requirement is claimed. The online test proves synthetic connectivity, not hosted
production access or a live Proton integration.

## Status and roadmap

| Area | Status at this README's 26 October 2026 review |
|---|---|
| **Live `main`** | GitHub Actions collection, tracked JSON/text observations, file-backed owner ledgers and CLI/reports. The release branch preserves the latest inspected observations without changing the live writer. |
| **PostgreSQL development** | Migrations 0001–0004, collection/history, importer, retrieval, owner SQL, calendar snapshots and read-only MCP locally qualified. |
| **Last qualified AWS deployment** | 26 October 2026: ARM64 `t4g.small`, PostgreSQL 17.11, pgvector 0.8.7 and migrations 0001–0004; native retrieval and synthetic S3/independent restore qualified. Intended production domain tables empty; collector/backup timers disabled. [Hosted results](docs/AWS_V1_MAINTENANCE_REPORT.md). |
| **Pending activation** | Production private agent-access qualification; Proton HTTPS subscription; twice-daily S3 scheduling, freshness checks, email delivery and final single-writer cutover. Synthetic online ChatGPT SQL qualification is complete. |

Cutover also requires nine genuine endpoint checks, historical-evidence acceptance,
integration preserving newer `main` data, a writer freeze, final-commit import and
reconciliation, independent restore and explicit single-writer approval.
[V1 roadmap](docs/V1_SCOPE_AND_ROADMAP.md) · [Cutover runbook](docs/AWS_CUTOVER.md).
[Current release checklist](docs/V1_RELEASE_CHECKLIST.md) records exact approvals
and the local integration/validation evidence. There is no production go decision.
ANN/rerankers and sophisticated autonomous actions are deferred.

## Quickstart

Python **3.11+**; the base file-backed package uses the standard library. Repository
access is required: this is private source, not a public release. From this
checkout, these commands make no employer requests and need no database:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/python -m jobwatch.cli capabilities --json
.venv/bin/python -m unittest discover -s tests -v
```

For the PostgreSQL path, Docker and the optional psycopg dependency are required:

```sh
.venv/bin/python -m pip install -e '.[postgres]'
PYTHON=.venv/bin/python bash scripts/test_postgres.sh
```

The harness creates and removes its own verified disposable PostgreSQL 17/pgvector
container; it accepts no production database URL. Optional MCP and real-model tests
need their documented dependencies/cache and otherwise skip. Nothing installs a
hosted database, migrates production or starts collection automatically.

## Documentation

| Guide | Read it for |
|---|---|
| [Development and local PostgreSQL](docs/POSTGRESQL_DEVELOPMENT.md) | Installation, roles, migrations and disposable testing |
| [Collection and material history](docs/POSTGRESQL_COLLECTION.md) | Publication transactions, absence contracts and evidence comparison |
| [Operational research](docs/POSTGRESQL_OPERATIONS.md) | SQL views, search, reports, health and source activation |
| [Retrieval](docs/POSTGRESQL_RETRIEVAL.md) | Model/profile provenance, indexing, exact cosine and hybrid SQL |
| [Owner SQL workflows](docs/POSTGRESQL_OWNER_WORKFLOWS.md) | Authorised writes, audit, concurrency guards and read-back |
| [Agent connectivity](docs/AGENT_CONNECTIVITY.md) | Five-tool contract, synthetic online qualification and pending production access |
| [Proton calendar path](docs/PROTON_CALENDAR.md) | Minimal feeds, stable identities, privacy and subscription limits |
| [AWS-only backup policy](docs/AWS_BACKUP_POLICY.md) | Twice-daily schedule, retention, missing-run alerts, costs and restore procedure |
| [AWS infrastructure](infra/aws/README.md) | Terraform protections, SSM, deployment and backup/restore procedures |

This repository includes operational and private data. A recruiter-facing README
does not make the full repository safe to publish or change its visibility.
