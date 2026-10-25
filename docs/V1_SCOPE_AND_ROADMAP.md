# V1 scope and delivery roadmap

Owner-agreed scope, recorded 25 October 2026. PostgreSQL-first, SQL-first and
agent-native are settled choices. This document records delivery work, not a
new architecture review or permission to deploy.

## V1 outcome

- Rich source evidence, immutable representations and material history;
  transparent ATS coverage/freshness; research leads, owner and application data.
- Composable ordinary SQL, schema inspection, BM25, PostgreSQL FTS, pgvector and
  simple hybrid retrieval. Agents choose their research strategy: no mandatory
  database-first/web-first policy, predefined query DSL or screening-only corpus.
- Appropriate agent reads and writes. Ordinary owner decisions and evidenced leads
  must not require collector publication or infrastructure-administration rights.
- A practical calendar and authorised follow-up workflow, reusing existing ICS
  rendering, stable identities and deadline uncertainty rather than rebuilding it.
- Secure agent connectivity: direct SQL/Codex remains available; online ChatGPT is
  included if account/platform support permits. MCP is an optional access protocol.
- Safe AWS operation, scheduling, backups, monitoring, recovery and a separately
  approved cutover to one authoritative store.

Coverage means bounded, disclosed coverage of monitored feeds, not the whole job
market. Embeddings are reproducible, evidence-linked derived data, never evidence
or eligibility judgments. No new framework, mandatory CRUD facade, microservice or
infrastructure is needed to define V1.

## Verified implementation and remaining work

| Area | Implemented | Remaining for usable V1 |
|---|---|---|
| Storage/collection | PostgreSQL 17, migrations 0001–0003, full parsed evidence, material events, locked publication/rescreening, absence safety and narrow audit | Final real-data import and reconciliation; genuine endpoint checks and transparent coverage |
| Migration/recovery | Frozen Git importer, repeatability, local/hosted synthetic dump/restore and independent off-host synthetic recovery | Owner-held durable encrypted custody, accepted recovery objectives and independent restore of the final imported database |
| Research/retrieval | Ordinary SQL/views, excluded postings, evidence/history, BM25/FTS/reports; local [EmbeddingGemma 2, exact vector/hybrid retrieval and recovery tests](POSTGRESQL_RETRIEVAL.md) | Separately approved hosted extension/package/inference qualification |
| Agent authority | Owner role supports audited owner/control/lead SQL writes separately from collector/migrator; Stage 4C supplies bounded read tools | Prove usable scoped connectivity and owner-authorised write workflows; keep read-only ChatGPT identity separate |
| Calendar/follow-ups | SQL calendar report reuses `calendar.py`; application follow-ups and explicit verified dates supported | Exercise/document the owner workflow and calendar-client use; add only missing basic authorised actions |
| Online ChatGPT | Local stdio/Streamable HTTP interoperability, separate proposed read login | Account eligibility, approved private-data scope, authenticated transport, packaging, credential custody and revocation; no public unauthenticated listener |
| Operations | AWS host, protected storage, SSM, logs/alarms and disabled systemd units qualified | Notification recipients, backup freshness/custody checks, secure access, scheduling and single-writer cutover approvals |

Baseline: Stage 4C `10d5f38` includes recovery `b3ba87f`. All nine worktrees were
clean; both feature tips matched the live remote. Remote main is `b6e0167`:
one observation commit absent from Stage 4C (local main is two commits behind).
Preserve that commit and every subsequent production update during integration.
No merge or data-tree replacement is part of this roadmap.

The last read-only host verification found installed release `1b72d0d`, three
migrations, zero intended-database domain records and both timers disabled/inactive.
Stage 4C is not hosted. Git remains authoritative. These are dated observations,
not guarantees of future state; see [recovery gates](AWS_RECOVERY_READINESS_REPORT.md).

## Delivery order and boundaries

1. Implement retrieval locally/disposably using the linked plan; preserve current
   normalisation/screening and lexical defaults. Do not couple collection to models.
2. Qualify scoped agent reads/writes, reuse calendar/follow-up behaviour and establish
   supported online connectivity without making MCP mandatory.
3. Close operating decisions: independent custody, recovery objectives, notifications,
   backup freshness, spend/credit checks and source validation. Resolve the known
   Terraform user-data replacement proposal without replacing the protected host.
4. Separately approve integration preserving newer main data, freeze the old writer,
   select the actual final commit, import/reconcile, independently restore and verify,
   then approve the single-writer switch and controlled first collection.

V1 is complete when these capabilities work together with correctness, security,
permission, integration and recovery tests. The £10–£20/month operating preference
remains; credits/spend limits do not replace that budget or grant deployment approval.
Neither this scope nor the retrieval plan authorises AWS changes, credentials,
live ATS checks, timer activation, real-data import, main merge or cutover.

## V2

Defer sophisticated autonomous actions, extensive natural-language evaluation and
retrieval optimisation: ANN indexes, rerankers, tuned fusion and large relevance
benchmarks. Keep ordinary regression/integration tests and a small real-model smoke
test in V1. Do not defer core correctness, access control or recovery as “evaluation”.
