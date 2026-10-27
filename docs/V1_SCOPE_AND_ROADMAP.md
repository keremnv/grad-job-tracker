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

**Core PostgreSQL production V1 activated 26 October 2026** under explicit owner GO.
[The handover](AWS_PRODUCTION_HANDOVER.md) and [current ledger](V1_RELEASE_CHECKLIST.md)
record the frozen source, reconciled import, actual collection and real recovery.
Main preserves both the full feature chain and newer observation commits; archived
observation files remain exactly C, not a second authority.

| Area | Implemented / qualified | Remaining boundary |
|---|---|---|
| Storage/collection | PG17.11, vector0.8.7, migrations0001–0004, immutable evidence/history, narrow audit; nine genuine sources validated/activated, controlled publication passed, daily systemd collection enabled | [First unattended run verified](V1_RELEASE_REPORT.md); monitor subsequent source failures; bounded feed coverage, not market completeness |
| Migration/recovery | Final durable Git imported/reconciled; baseline and post-collection real-data S3 archives independently restored with exact data/security/sequences | Operate 00:00/12:00 UTC backups, health/email and restore practice; frequency is not a strict RPO, AWS-only risks accepted |
| Research/retrieval | All-verdict SQL/views, BM25/FTS and actual owner-role reports/search; local EmbeddingGemma2 exact vector/hybrid implementation and tests | Production embeddings currently zero; inference/indexing on suitable local hardware, not the 2 GiB host |
| Agent authority | Existing audited owner SQL through authenticated SSM/peer, separately scoped collector/migrator | Optional SQL-only remote credentials require their own access setup; no mandatory API or write MCP |
| Calendar/follow-ups | Shared ICS, stable identities/uncertainty, privacy-minimised atomic feeds and ordinary owner follow-ups | Approve HTTPS sharing/custody and test actual Proton subscription; polling is not guaranteed alerting |
| Online ChatGPT | Five read tools and real synthetic tunnel SQL call passed; test stopped, owner-reported disconnection/revocation not independently verified | Real-data scope, dedicated read identity/authentication and gateway approval; no production connection activated |
| Operations | Protected existing EC2/EBS/S3/SSM, backups, logs, four healthy alarms and confirmed email delivery; Git writer retired | Known protected-instance Terraform replacement proposal remains blocked; no infra apply or guard removal |

No new infrastructure or model service was added for cutover. Preserve the
£10–£20/month operating preference; credits/spend limits are not deployment
approval. Agent/calendar real-data sharing remains a separate decision and does
not block the now-active core database/collector.

## V2

Defer sophisticated autonomous actions, extensive natural-language evaluation and
retrieval optimisation: ANN indexes, rerankers, tuned fusion and large relevance
benchmarks. Keep ordinary regression/integration tests and a small real-model smoke
test in V1. Do not defer core correctness, access control or recovery as “evaluation”.
