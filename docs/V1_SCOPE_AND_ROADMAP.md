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
| Storage/collection | PostgreSQL 17, migrations 0001–0004, full parsed evidence, material events, locked publication/rescreening, absence safety and narrow audit | Hosted extension/0004 qualification, final real-data import and reconciliation; genuine endpoint checks and transparent coverage |
| Migration/recovery | Frozen Git importer, repeatability, local/hosted synthetic dump/restore and independent off-host synthetic recovery; accepted maximum loss tolerance **12 hours** | Qualified six-hour schedule, independent encrypted custody, freshness/notifications and independent restore of the final imported database; old daily/26-hour proposal is superseded |
| Research/retrieval | Ordinary SQL/views, excluded postings, evidence/history, BM25/FTS/reports; local [EmbeddingGemma 2, exact vector/hybrid retrieval and recovery tests](POSTGRESQL_RETRIEVAL.md) | Separately approved hosted extension/package/inference qualification |
| Agent authority | Owner role supports audited owner/control/lead SQL writes separately from collector/migrator; Stage 4C supplies bounded read tools; [Stage 4E tests ordinary owner SQL](POSTGRESQL_OWNER_WORKFLOWS.md) end to end | Qualify usable scoped hosted connectivity; keep read-only ChatGPT identity separate |
| Calendar/follow-ups | Shared ICS projection, programme deadlines, triage revisits and [minimal atomic subscription snapshots](PROTON_CALENDAR.md), with stable linkage IDs and revisions | Approve private HTTPS hosting/publication and credential custody; test a real Proton subscription and notification defaults; polling is not guaranteed alerting |
| Online ChatGPT | Local five-tool interoperability and **real synthetic `sql_query` call** through the private tunnel; [Stage 4F](AGENT_CONNECTIVITY_REPORT.md) records scope and shutdown | Approved real-data scope, hosted read identity/authentication, trusted gateway availability and cost/terms; no public unauthenticated listener or inferred approval from the synthetic test |
| Operations | AWS host, protected storage, SSM, logs/alarms and disabled systemd units qualified | Notification recipients, backup freshness/custody checks, secure access, scheduling and single-writer cutover approvals |

Baseline: Stage 4C `10d5f38` includes recovery `b3ba87f`. All nine worktrees were
clean; both feature tips matched the live remote. Remote main is `b6e0167`:
one observation commit absent from Stage 4C (local main is two commits behind).
Preserve that commit and every subsequent production update during integration.
This paragraph records the earlier baseline, not current release status.

Stage 4E branch is based on retrieval `598a64b`. At its read-only remote check,
main had advanced to observation commit `9bbcb0149d41250cc2e56b2cbb25779bd5fafc16`;
it and every newer production update must be preserved.

Release preparation now integrates `9bbcb01` and its predecessor `b6e0167` into
`feat/v1-release-readiness`, based on portfolio `2cc24c4`. Data/config/owner files
and the active workflow match that inspected main exactly; main itself is not
merged or changed. [One current checklist](V1_RELEASE_CHECKLIST.md) records the
integration, hosted differences, accepted 12-hour recovery constraint and approvals.
The final authoritative data commit is still selected only after the future freeze.

The last read-only host verification found installed release `1b72d0d`, three
migrations, zero intended-database domain records and both timers disabled/inactive.
Stage 4C is not hosted. Git remains authoritative. These are dated observations,
not guarantees of future state; see [recovery gates](AWS_RECOVERY_READINESS_REPORT.md).

## Delivery order and boundaries

1. Completed locally: retrieval, preserving normalisation/screening and lexical
   defaults without coupling collection to models.
2. Completed locally/synthetically: scoped owner SQL, reused calendar/follow-ups and
   actual online ChatGPT SQL connectivity. Hosted access and Proton remain open.
3. Close operating requirements: independent custody, accepted 12-hour loss control,
   RTO/response, notifications, freshness, spend/credit checks and source validation. Resolve the known
   Terraform user-data replacement proposal without replacing the protected host.
4. The release candidate locally preserves inspected main. Separately approve main
   integration preserving any newer data, freeze the old writer,
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
