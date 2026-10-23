# PostgreSQL authoritative schema and cutover — design proposal

**Status:** Proposed, documentation only; requires independent focused review before implementation.  
**Date:** 2026-10-23  
**Companion:** [OPPORTUNITY_CORPUS_DECISIONS.md](OPPORTUNITY_CORPUS_DECISIONS.md) (D01–D20, especially D17–D20).  
**Base system inspected:** main `dc8a336`; current Git-tracked JSON/CSV/text state, nine monitored ATS boards, successful Actions collector, 170-test suite reported by independent reviewer.  
**Decision:** PostgreSQL is authoritative and reachable independently by GitHub Actions and external coding agents. SQL-first reads; pgvector optional derived embeddings. No Jev, stored CV, personal fit score, MCP dependency or automated applications.

## 1. What follows from the existing requirements

1. All successfully observed ATS vacancies survive screening; rejected-for-attention != discarded.
2. Employer/native information, our observation, normalized data, screening, independent verification and owner decisions have separate semantics.
3. Stable posting identities and conservative URL comparison continue. A lead or graduate programme is **not** a fake ATS posting.
4. Original and material historical wording must be retrievable. No daily per-posting event for unchanged observations; no historical system-state replay.
5. Agents use direct SQL (`psql`, psycopg, arbitrary read queries). High-level commands are optional conveniences, except validated multi-record mutations where domain invariants require one.
6. FTS ranking and pgvector embeddings derive from source evidence; neither asserts relevance/eligibility or becomes canonical job content.
7. Scheduled collector publishes qualifying board observations atomically, independently per board. A failed or partial collection cannot mark previously observed postings absent.

## 2. Proposed minimal schema (conceptual, not applied DDL)

Use conventional PostgreSQL tables, not a generic event-sourcing platform. Columns shown are key fields; exact SQL types and constraints need a migration file and tests.

| Table | Key / representative columns | Responsibility |
|---|---|---|
| `employers` | `id` PK, stable `slug` UNIQUE, `name`, `careers_url` | One canonical employer across possibly several sources; do not assume name labels are unique IDs |
| `sources` | `id` PK, `employer_id` nullable FK, `provider`, `region`, `board`, `monitored` boolean, discovery URL/actor, association evidence | Discovered or active ATS source; unique provider+region+board *with explicitly reviewed case policy* |
| `source_checks` | `id`, `source_id` FK, `kind`, `outcome`, `checked_at`, `evidence` JSONB | Endpoint validity and employer association are **separate** facts; track their dated checks |
| `collection_runs` | `id` PK, `source_id` FK, external idempotency key, `started_at`, `finished_at`, `status`, `record_count`, `skipped_count`, `error` | Board attempt log. Only `complete` runs can justify `not_seen`. Independent source health can be a view over runs |
| `postings` | `posting_key` TEXT PK, `source_id` FK, `native_id`, `title`, `posting_url`, `apply_url` nullable, `workplace_raw`, `workplace` nullable, `position_fields` JSONB, `compensation` JSONB, `source_dates` JSONB, `first_seen`, `first_seen_basis`, `last_seen`, `feed_status`, `missing_since`, `native_evidence_id`, `description_evidence_id`, `normalizer_version`, `normalized_hash`, `search_document` TSVECTOR | One current row per source-native posting; all verdicts remain included |
| `posting_locations` | `posting_key` FK, `ordinal`, `label`, `source_path`, `is_primary`, nullable locality/region/country/address | Structured current locations; keep label even when structured address is unknown; exact original source fields remain in evidence |
| `evidence_versions` | `id` PK, `kind` (`selected_native`, `normalized_description`), `content_sha256`, `projection_version`, one JSONB/text content value, `created_at` | Immutable, content-addressed selected-native projection or text evidence. One shared evidence mechanism, not a history per section |
| `posting_revisions` | `id` PK, `posting_key` FK, `collection_run_id` nullable FK, `observed_at`, `category`, `before_native_id`, `after_native_id`, `before_description_id`, `after_description_id`, `details` JSONB | Material evidence transitions: first_seen/backfill, provider-response changed, representation changed, metadata update, new-field baseline, not_seen/reappeared, screening transition. Retain referenced evidence |
| `screening` | `posting_key` PK/FK, verdict, family, reason codes TEXT[], rules version, input hash, basis, `evaluated_at` | Current rule judgment, independent of the posting's source facts |
| `leads` | Stable `lead_id` PK, kind (vacancy/programme), employer reference, cycle year nullable, source URL, observed_at, actor, state, exact wording/evidence JSONB, `matched_posting_key` nullable | Individually researched leads; avoid duplicates with known ATS postings; cycle-aware programme identity |
| `verification_observations` | `id`, `posting_key` FK, kind, observed_at, actor, source URL, method, exact wording/value, description evidence ref where applicable | Independently checked claims; latest valid evidence is queryable without overwriting past contradictory observations |
| `triage` | `posting_key` PK/FK, owner decision, reason, note, revisit date, decided_at, recorded_by | Personal attention decision; scans never write it |
| `applications` | `job_key` PK, `posting_key` nullable FK, employer/role/URL, stage, date_applied, cv_version label, next action/date, notes | Owner workflow, including manual applications not in the ATS index. Preserve existing manual IDs and conservative URL matching |
| **Later: `embedding_chunks`** | evidence ref, posting_key FK, ordinal, model/revision, chunking revision, input SHA, embedding VECTOR, indexed_at | Derived semantic search. May be rebuilt; no CV or session-private embeddings |

**Minimisation:** This table is a *target logical model*, not a mandate to implement all rows in one migration. In particular `source_checks`, separate current locations, `embedding_chunks` and current section rows can be deferred if the preceding application doesn't yet need them. No universal field-provenance tables, no independent version streams for descriptions/sections, no generic write framework.

**Schema namespace:** A single application schema (`jobwatch` or `public`) is enough; there is no business reason for microservice-like schema separation. Do not confuse DB schema with `schema=2` in today's JSON file.

**Keys and namespace:** Preserve deployed posting keys (`provider:board:native_id`). Include region in a source's unique identity, but before enabling a new region/case mapping check that it cannot map to a pre-existing posting key. A separate bigint internal `source_id` is not permission to silently change posting keys. All references to a posting use the stable external key where meaningful.

### 2.1 How strict to be with typing

- Use `timestamptz` for *observation/check times*, `date` for truly date-only owner follow-ups, and exact provider raw dates in JSONB or text when source semantics are uncertain. Do not invent midnight/time zones.
- Use `CHECK` constraints for known states, `NOT NULL` for genuine requirements, `UNIQUE` for source/record identity, foreign keys for referential integrity. Validate provider-specific details in ingestion code and fixtures rather than over-normalising JSON.
- Keep normalized role/location/workplace fields queryable but make the original value and source-field path available. If source absent, use `NULL` or an explicit data-availability marker; don't map unknown to `false`.
- Keep ordered native sections in selected-native evidence and a useful current normalized description; add a separate `posting_sections` table only when segment-specific retrieval/filters justify it.
- Screening is a judgment: it must not participate in the collector's posting-retention predicate. An application is a user workflow: it must not be deleted because the posting becomes `not_seen`.
- `evidence_versions` stores selected *provider values*, not necessarily exact upstream HTTP-response bytes. Canonicalise projection data deterministically with a versioned rule before hashing. Do not claim pixel-perfect employer HTML or exact upstream edit timestamp.

## 3. Query contract: ordinary SQL

Agents should freely use `psql`, psycopg and other SQL tools. Avoid a custom query DSL.

Useful discoverable views, **not duplicated authorities**:

- `research_postings`: posting facts + current screening verdict, plus source status, observed timestamps and freshness; includes **excluded** postings.
- `source_health`: monitoring setting, last complete/failed collection, source freshness and outcome.
- `opportunity_workflow`: posting joined to triage/application without guessing URL equality, for owner-context queries.
- `posting_change_log`: category, observed interval and linked evidence IDs; do not call response changes confirmed employer edits.

Example research SQL (illustrative; `research_postings` view must be implemented):

```sql
SELECT posting_key, company, title, screening_verdict, feed_status
FROM research_postings
WHERE feed_status = 'listed'
ORDER BY first_seen DESC
LIMIT 30;
```

PostgreSQL native FTS: maintain a **derived `tsvector`** (for example `postings.search_document`) combining current title, metadata, description text and section names. Index with GIN; update it within the same posting transaction when normalized material changes. Query via `websearch_to_tsquery`/ `ts_rank_cd`/`ts_headline` as appropriate. PostgreSQL native `ts_rank` is **not BM25**; keep current Python BM25 for comparative evaluation until validated. Use snippets linked to actual evidence. Research search defaults to all verdicts among currently listed postings, with explicit options to include `not_seen` data and warn about unhealthy sources.

**No row-level access layer is needed merely because the client is an agent.** Conventional role privileges distinguish read-only research, owner-authorized mutation, collector publication and schema migrations. Agent SQL reads remain unrestricted within the datasets intentionally exposed to that connection; private owner notes require deliberate credential selection rather than being indiscriminately copied into third-party services.

## 4. Atomic collection and history algorithm

**Per-board collection boundary** (a failure in one board cannot erase or invalidate another board's result):

1. Read source identity and monitoring status. Fetch public API through existing bounded provider adapter. Record started/finished timestamps and validate completeness before beginning a database transaction. A successful empty board is a valid observation only if the provider's completeness checks succeeded; suspicious mass disappearance may warrant a separate explicit policy.
2. Start transaction. Lock the **source row** with `SELECT ... FOR UPDATE` to serialize publication for that source. Reject publishing an older/out-of-order completed observation over a newer committed one. Enforce an external per-source run idempotency key. Do not hold locks across network calls.
3. Insert collection-run outcome; for complete observations, upsert each source-native posting. Preserve stable posting keys and any owner state. Save new immutable evidence only when new or necessary; classify comparable source-group changes, new-field baselines and normalizer changes precisely.
4. Append a **single logical posting-revision stream** only for first observation, material revision, representation/baseline transition, absence or reappearance; optionally separate provider metadata-only changes from content. Maintain current `screening` state and record distinct interpretation transitions, correctly setting evaluation timestamps.
5. Only for a validated **complete** run, find previously listed postings for that source not in the observed identity set and mark them `not_seen`. Absence means missing from that successful feed, *not* confirmed closure. A network failure/partial/malformed response records health but never marks absent.
6. Update source last-success/reference state and commit. Publication is atomic; failure rolls back. Failed runs may be recorded independently as observation-health records without modifying postings.
7. Embedding generation can run later against immutable description evidence, with an evidence fingerprint check before publishing. It must not block collection and must not alter posting facts.

**Remote-feed limitation:** A paginated ATS response may change while paging. Complete means the protocol finished and passed its accounting rules, not a globally atomic upstream snapshot. Do not make stronger freshness/closure claims.

**Retries:** Same idempotency key + source returns an unchanged outcome; old fetches cannot publish over newer runs. A first scan after migration or source activation is a discovery baseline, not proof that the employer opened all jobs at that moment. If material-fingerprint projection versions are not comparable, new fields baseline while old comparable groups remain comparable.

## 5. SQL writes, permissions and ordinary safety

The owner explicitly wants **powerful low-level tools and no artificial agent restrictions**. This design does not require calling a jobwatch API for SELECTs and does not assume agents are incompetent.

- **Research role:** SELECT over intentionally exposed tables and views, including excluded postings and current/historical evidence. Prefer ordinary `psql` and psycopg over wrapping every useful SQL query.
- **Collector role:** transactional changes to its owned source/posting/evidence/history tables, not owner triage or application rows.
- **Owner-agent role:** permitted owner-state changes; may record evidenced leads and source candidates. Database constraints protect duplicate identities, valid states and foreign keys. For operations such as source activation or verified status recording that require multiple checks, use a small transaction/function or the existing application CLI, not scattered ad hoc updates.
- **Migration role:** schema changes and extension operations only; do not give routine agents schema-owner credentials.
- All clients need authenticated encrypted DB connections and sensible secret handling; reachable independently does not mean publicly readable.
- PostgreSQL roles are ordinary least privilege, not a special agent-authority system. Extra gates should require a specific domain/permission reason. The current scan/owner write boundary remains meaningful independent of caller type.

## 6. pgvector and hybrid search

- Enable `vector` extension when vendor support is verified. Store vectors in **derived** `embedding_chunks`, referencing posting key and immutable description evidence, with model identifier/revision, chunking version, input hash and timestamp. Current source facts remain in postings/evidence tables.
- Start with **exact cosine distance** queries without ANN index. At hundreds of posts, HNSW or IVFFlat is not automatically needed. Benchmark before indexing approximate ANN.
- For custom/model dimension: choose a pinned model and then enforce vector dimensions. During model experiments use a consistent model/version+dimension contract so cross-model comparisons do not mix embeddings.
- A query may be embedded **in the agent's current session** and used as a parameter; do not persist private CV/context text, query text or their embeddings in production retrieval tables. No permanent personal profile.
- Hybrid query = FTS candidate ranking plus vector candidate ranking, optionally RRF fused in SQL/Python; return the actually used method, matched text/evidence and current/freshness check. Do not sum uncalibrated cosine and text-rank scores.
- Embedding jobs should be idempotent by (`source_evidence_id`, `model_revision`, `chunking_revision`, `ordinal`, `input_hash`). Old vectors may be retained for explicitly historical inspection or removed from the **current** retrieval set; never silently serve outdated vectors as current postings.
- Do not change screening verdicts based on vectors. No Jev or recommendation-model layer in the prototype.

Reference: https://github.com/pgvector/pgvector ; https://www.postgresql.org/docs/current/textsearch.html .

## 7. Migration and cutover (design; no execution authorised)

### Preparation

1. Pin minimum PostgreSQL/pgvector versions once hosting options are checked. Create disposable test database and explicit numbered SQL migrations; verify restore procedure.
2. Expand ATS fixtures; add transactional integration tests for foreign keys, exact source identity, change attribution, source locks, idempotency, missed-posting semantics, and older evidence references.
3. Create **one-time importer** from current Git-backed state: configuration, complete postings including excluded, current descriptions, retained history references where available, health, leads/programmes, verified claims, triage and applications. Compute deterministic import run IDs and preserve original posting keys and `first_seen_basis`. Import missing/legacy native material as **unknown**, not reconstructed provider payloads.
4. Reconcile counts and content: current postings by key/source/status/verdict, retained text hashes, current/legacy history, manual leads, notes, triage, application stages, verification dates and evidence, board last-success. Fail before cutover on unacceptable mismatch.
5. Validate new reports, SQL queries and CLI conveniences against the imported database; compare the same result sets with the old code. Validate owner privacy/credentials and backup/restore.

### Cutover

1. Freeze owner writes briefly and disable scheduled scan publication, then obtain final source snapshot. Import/reconcile final changes and back up both sides.
2. Point agents and scheduled collection to PostgreSQL using distinct credentials. Ensure each planned writer has exactly one authoritative target. Change workflow to stop committing mutable job observations/health into Git. Preserve existing report output through the workflow if useful.
3. Run a controlled production read validation and **one** qualifying scan under observation; verify commits, source health, history events and unchanged owner state.
4. Archive or retire old writable JSON/CSV paths clearly as non-authoritative. Protect private history; deleting files from HEAD does not delete them from older Git commits.
5. Document restore and emergency response. If new writes have occurred in PostgreSQL, rollback cannot simply switch to stale Git data without explicit reconciliation/export.

**No dual-write period** with independent Git files and PostgreSQL accepted as equal authorities. Do not create a remote DB, select a billing tier, migrate private data or change workflows without separate owner approval.

## 8. What still needs focused decisions / review

These choices do **not** follow mechanically from requirements and should be reviewed before implementation:

1. **Host/provider, region and price:** must support independent authenticated GitHub Actions and agent connections, pgvector, backup/restore and private data requirements. Neon/Supabase are candidates, not selected.
2. **Minimal authoritative schema boundary:** whether owner workflow (triage/applications) migrates in the first cutover or shortly afterwards. Recommendation: one cutover to avoid two contemporaneous canonical stores, but staging implementation is fine.
3. **Provider material comparability:** exact native fields, projection schema epochs, metadata-only rules, partial schema field baselines and legacy evidence limitations.
4. **Source identity/activation policy:** provider+region+board collisions against legacy keys, evidence for employer association and standing authority to enable automated monitoring.
5. **Document-level vs passage embeddings:** start with one or a few sensible passage vectors per description, evaluate first. Do not overbuild section persistence.
6. **Backup/restore and migration failure policy:** durable off-provider or provider-supported recovery at an acceptable cost; a tested restore is part of deployment readiness.
7. **Direct SQL owner writes:** which ordinary constraint-safe updates are supported, and which small multi-record operations are safer to keep as domain functions. This should reflect actual invariants, not a blanket prohibition.

## 9. Independent second opinion checklist

Ask the reviewer to challenge:
- Is this actually the smallest relational model that preserves existing semantic distinctions? Which proposed tables can be merged/deferred?
- Which source/posting keys should be immutable, and which joins can genuinely use FKs versus conservative matching?
- Do selected-native evidence + posting_revisions suffice for accurate description history and normalization-attribution, including first migration and source schema changes?
- Can the per-source transaction avoid stale writers/out-of-order runs? Does the proposed locking/idempotency scheme work under at-least-once GitHub Actions?
- Does the schema distinguish a failed fetch from a successful empty board, and `not_seen` from closure?
- Will normal PostgreSQL SELECT privileges accidentally expose private owner notes in a setting where the agent should not see them? What is the least complicated solution?
- Are full-text vectors and pgvector derivatives correctly tied to the current source-evidence versions? Is a separate retrieval service actually unnecessary?
- Is the Git-to-Postgres cutover safe, recoverable and appropriately scoped for a personal system?

**No code migrations, live scans, repository data edits or hosted service provisioning are authorised by this design file.**
