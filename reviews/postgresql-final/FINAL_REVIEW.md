# Final PostgreSQL design decision

**Review only, 23 October 2026. No production migrations or application changes.**

Architecture base: `design/opportunity-schema-history`, `0cf1c42d2397c9ae9bbdf81aa6e6e1b150ce1b58`.
Production snapshot examined: `main`, `90434ea6d9a3457f9c8ba40f01ad25fa2ceffca3`, fetched during this review.
The previous review examined `dc8a336`; its counts are not today's counts.

## A. Assessment and decision

**Approve the final design in this directory for implementation. Do not implement the old scratch DDL unchanged.** PostgreSQL, SQL-first clients, material history and pgvector remain the decisions. No additional service is needed.

I would use eleven initial tables, three convenience views, two runtime login roles, one small audit function attached to five mutable tables, and ordinary source-row transactions. The count is incidental. Immutable versions contain both native evidence and that version's derived source fields; a posting points to its current version. Current screening fits on the posting row. Embeddings come later.

The blocking defects in the earlier executable proposal are resolved here:

1. **It calls versions immutable but grants UPDATE, and overwrites their normalizer/text.** Two normalizers over the same raw hash cannot coexist under its unique key. Events with before=after cannot recover the old derived wording. Use immutable `(posting, evidence hash, normalizer, normalized hash)` versions, with distinct IDs for distinct representations.
2. **JSONB plus replacement-before-hashing destroys the evidence it claims to preserve.** Store the complete parsed native record in NUL-safe bytes, hash those bytes, and sanitize only derived searchable values.
3. **Several relationships are incompletely constrained.** Old events/verification evidence can reference another posting; a source can point to another source's run; a posting key need not agree with its source/native ID. Composite FKs and an identity CHECK fix these without triggers.
4. **The old timestamp-only order is not total, and deployment concurrency is unspecified.** Register before fetching, retain database-issued time, and use a database-issued sequence as the strict observation-order token. A source processing revision prevents an older worker from undoing a newer rescreen/normalizer deployment.
5. **The audit records only the shared login, omits employer edits and source creation, and copies whole source rows for publication-pointer updates.** Require a transaction-local actor claim and record the actual session login separately. Audit mutable controls only.

Important revisions: retain manual application keys when later linking them; fix `:443` URL identity; use an actual positive-observation time on each posting; distinguish safe absence inference from protocol success; require endpoint validation AND employer-association evidence for source activation.

**Implementation can start. Production cutover still requires the full importer, production publication code and restore/reconciliation acceptance below.** The review probes are evidence for a design, not a completed application migration.

## B. Relational schema and identity

The complete fresh-database replacement is [001_proposed_schema.sql](001_proposed_schema.sql). It is deliberately under `reviews/`, outside the production migration directory. It was applied only to disposable databases. The optional [002_deferred_embeddings.sql](002_deferred_embeddings.sql) is a later design example, with an explicitly provisional dimension.

| Initial table | Invariant / actual query | Keys, relationship and decision |
|---|---|---|
| `employers` | Canonical employer across boards, plus the existing watchlist | Numeric PK, unique stable slug; employer name is a label. Preserve all 44 employers, including those without a board. Keep notes here; collector gets SELECT of public columns only. |
| `sources` | One configured ATS endpoint, mapping/activation controls, last publication | Numeric PK; unique `(provider,lower(board))`. Region selects the endpoint, never a second namespace for deployed keys. Endpoint check time is distinct from employer mapping evidence. Keep these dated facts inline; no `source_checks` table. |
| `collection_runs` | Attempt/health record, strict order and idempotency | Caller UUID PK, database identity `run_order` UNIQUE, DB `started_at` before fetch. `(source_id,id)` supports same-source FKs. Includes valid empty, failed, skipped, superseded, validation and explicitly labelled imported health baselines. Import `details` preserve unknown counters and original outcome. |
| `postings` | Stable posting identity, feed observation and current judgment | Stable text PK; unique `(source_id,native_id)`. Composite source FK plus key CHECK tie provider/board/native ID together. Source identity and first-seen are immutable to runtime grants. Current screening columns replace `screenings`. |
| `posting_versions` | Retrieve exact retained source wording and its historical derived representation | UUID PK, same-posting current/event FKs. Full evidence bytes, typed immutable normalized columns, normalizer ID and normalized hash. Deduplicate identical evidence/output combinations; never UPDATE or DELETE through either runtime role. |
| `posting_events` | One chronological material history | Identity PK ordered by ID per posting, source/run FKs, same-posting before/after FKs. Category separates observation/provider/normalizer/screening/import. Unique `(run,posting,category,kind)` for collection events. No unchanged-posting events. |
| `verifications` | Independently observed official-page claims; latest by check time | Append-only UUID PK, stable caller UUID on retry, same-posting evidence FK. Preserve legacy hashes if unavailable text prevents binding. Date wording and optional exact value remain distinct; no invented timezone. No generic audit duplication. |
| `leads` | Manual vacancy research and programme cycles that are not ATS postings | Stable lead key; kind-specific state CHECKs. Vacancy exact URL unique. Programme identity is a stable employer/programme key plus nullable cycle, UNIQUE NULLS NOT DISTINCT; a reused careers hub URL does not merge cycles. Optional vacancy-to-posting FK; programme rows cannot pretend to be a posting. |
| `triage` | Current private owner decision | Posting PK/FK, known decision/reason CHECKs, revisit date only with `later`. Scans cannot access it. Audit supplies recovery history. |
| `applications` | Private workflow, including manual applications | Stable existing `job_key` PK, nullable UNIQUE posting FK. Remove `job_key=posting_key`: a later link must preserve a manual key. Unique loose URL refuses near duplicates, while only an exact URL can establish an automatic fallback link. |
| `owner_audit` | Recover committed mistakes in mutable owner/research controls after Git stops recording them | Identity PK, transaction ID, row time, session login, actor claim, operation and before/after images. Private, appendable only by the trigger. No event sourcing or replay contract. |

`postings.provider/board` are deliberately redundant identity attributes: the composite FK makes the key equality enforceable without a cross-table trigger. They cannot drift. Provider-native numeric job/internal IDs are not employer IDs or posting IDs interchangeably. In particular Greenhouse `internal_job_id` does not replace the job-post ID. An ATS move or repost with a new provider ID creates a new posting, never an automatic merge. Source identity/region is not editable with runtime credentials; pause and deliberately register/review a new namespace if that is ever necessary. A genuine same-slug second region remains a namespace-design change for new keys, not permission to rewrite deployed ones.

`posting_versions` holds the typed fields already used by the running adapters, including raw country names/arrays, salary string, source dates and deadline evidence. Do not silently turn these into ISO codes or structured locations in the persistence migration. JSONB is for small source-date/deadline structures, import provenance, change details and later optional source projections. Keys, FKs, status, judgment, observation times, common text and URLs remain relational columns. JSONB is not a duplicate native authority.

There is no independently writable current copy of normalized fields on `postings`. Joining its current version gives current facts and avoids a "snapshot says A/current columns say B" invariant. Version text/FTS are stored once per evidence/normalizer/output combination. UUIDs permit choosing the initial version ID before inserting a posting; a deferred current-version FK permits the posting→version insert cycle within one transaction. Empty descriptions and unavailable legacy text remain distinct.

Indexes are limited to identity/duplicate enforcement, latest source attempt, source/status scans, per-posting event chronology, latest verification, and GIN over version text. The version unique key also supports per-posting version lookup. GIN includes historical versions; current search joins the current pointer. At this scale that is simpler than another current search-document table. Add no JSONB GIN, country GIN, event-time index or ANN index without an actual query need. Restrictive FKs and absence of DELETE grants retain evidence. There is no orphan-text cleanup job.

Three views earn their place: `source_health`, public-corpus `research_postings` (all verdicts), and owner-context `opportunity_workflow`. Applications outrank triage; revisit dates use Europe/London. The workflow view supports explicit links first and unambiguous exact-URL fallback for unlinked manual applications. Loose URL matches remain questions. The queue is a WHERE clause; change history is a normal query on events. Additional views can be added safely when repeated queries justify them.

## C. Evidence and material history

### Storage: source values, not an HTTP archive

**Keep full parsed provider records as deterministic ASCII JSON bytes in `bytea`.** Encode with `json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('ascii')`. Compute `evidence_sha256` over these stored bytes (the SQL generates it). Decoding reproduces the parsed record, including exact strings, HTML, entities, line breaks and U+0000. Serialization syntax, key order, number spelling, duplicate upstream keys, HTTP headers and original response-body bytes are not promised evidence. We do not need those to retrieve observed wording or compare job facts. Normal JSON feed validation should reject malformed/ambiguous input rather than manufacture evidence for discarded values.

`jsonb` cannot retain original JSON syntax and rejects decoded U+0000. `json` can retain an escaped NUL, but extraction/conversion can still fail. Neither a JSONB-only payload nor destructive sanitization is the evidence authority. Binary serialization avoids that ingestion failure without a second native cache. This matches PostgreSQL's documented distinctions between [JSON types](https://www.postgresql.org/docs/17/datatype-json.html).

For ordinary payloads, SQL can inspect native fields with `convert_from(evidence,'UTF8')::jsonb`; filter `origin` first, and do not cast a NUL-bearing payload. For the exceptional payload use `encode(evidence,'hex')`/bytea retrieval in psycopg and `json.loads(bytes(...))`. Typed source columns support ordinary research without any decoding. Add a sanitized native JSONB cache only if a measured SQL workload later warrants its extra invariant.

Normalize source content for PostgreSQL text/JSONB by replacing U+0000 with U+FFFD in DERIVED values only, and record `nul_replaced`. Never replace IDs or URLs to force validation: invalid identity fails the board. The unmodified source string remains recoverable from evidence; a UI must label the searchable rendering as sanitized when applicable. Current imported descriptions contain no NUL, as measured. Reject invalid derived JSON shapes/ambiguous sanitized object keys rather than silently merging values. Other Unicode validation failures are failures, never empty feeds.

**A version is an immutable evidence + representation tuple.** A new normalizer can reuse the native bytes in another row with its own normalizer ID and normalized hash. The previous row is untouched. Some byte duplication across normalizers is acceptable at this scale and avoids a separate blob table. A→B→A can reuse the original tuple, while events retain all transitions. Do not update a version's text to save one row. Do not garbage-collect referenced or unreferenced versions in the initial system.

### Comparisons and attribution

Use a small provider-specific Python comparison function. Compute material values from BOTH retained native records using the SAME current comparison code. Record that code's revision on an emitted event. This is comparison-code versioning, not a stored native projection, projection epoch, section history or replay engine.

Initial comparison policy:

| Provider | Material values |
|---|---|
| Greenhouse | `title`, `absolute_url`, `location.name`, offices/departments including meaningful IDs/names/locations, semantically rendered `content`, explicit deadline, exposed employer `metadata` values keyed by native ID/name. |
| Lever | `text`, hosted/apply URLs, categories including allLocations/commitment/department/team, country/workplace, opening/body/additional text and ordered lists WITH labels, salary range and salary wording. Choose documented available plain fields without duplicating opening/body content. Use HTML semantic text where no plain equivalent exists. |
| Ashby | Title, job/apply URLs, primary/secondary addresses and location labels, remote/workplace flags, employment type, department/team, `descriptionPlain`, stated compensation components/tiers and listing flag. Preserve conflicting values instead of inferring one fact. |

Exclude volatile provider timestamps and HTML styles from material comparisons; retain them in the native record. Sort set-like arrays by stable native identity; keep section/list order. HTML comparison removes markup/style and collapses whitespace/entities, while preserving words, case and punctuation. This comparison renderer does not alter the persisted v1 adapter output. Unknown provider fields are retained but create no events until deliberately selected. No universal field-provenance wrapper is needed.

The regression cases settle the policy: the two actual incident.io HTML `margin-top:0`↔`0px` changes preserve identical `descriptionPlain`, so retain the native variants and emit no material event. The actual Cambridge Consultants pair has unchanged substantive description text and changed `metadata` Job Summary (`tbc`→a paragraph), so emit a metadata change, not a description edit. The complete pairs and Git commit provenance are in [tests/source_pairs.json](tests/source_pairs.json). The executable comparison probe covers these demonstrated subsets; production must complete the remaining provider field policy above.

Presence is not value equality: absent, explicit null and empty remain distinguishable in native evidence. A newly requested field absent from old evidence is an **evidence baseline**, even if the new value is null. A field already present in old full evidence is comparable when newly selected by our code; merely exposing it in normalized output is our representation change. Removing a previously present field under the same request contract is an observed loss/change of source information, not an inferred negative fact. If a request stops collecting a field, mark it unavailable/baseline under the new request contract; do not call that an employer removal. `request_version` records URL/parameter coverage. Keep this logic in the provider comparison code, not a field-availability schema.

| Situation | Version/history action |
|---|---|
| Same bytes and same normalizer/output | Reuse version; no material event. Update positive observation time. |
| Native bytes differ, material equal | Retain/reuse the evidence tuple and move current pointer; no material event. Formatting remains retrievable. |
| Comparable material differs | One `provider/changed` event with before/after version IDs; changed values/hashes and baseline fields distinguished inside `changes`. |
| Only newly available fields | `provider/baseline`, with before/after refs and precisely named new evidence. |
| Normalizer changes over identical native input | New immutable representation tuple. `normalizer/renormalized` if representation differs; no provider-change claim. Record a version even if output stays equal, retaining its normalizer ID. |
| Normalizer AND new source information arrive together | First reprocess old native evidence with the new normalizer, recording its representation transition; then compare old/new native under the same material code and record provider transition if needed. Both events are in the one stream and transaction. |
| Rules/input reevaluated | Always refresh current rules/input/basis/evaluation time. Emit `screening/rescreened` for changed verdict/family/codes, including before/after rules/input and the version evaluated. No source-change claim. |
| Absence/reappearance | Observation event with retained current version refs. Never a verified closure. |

Material events carry **our observation** receipt time and recording time. Where useful include the prior positive-observation time and the new receipt time in `changes.observed_interval`. Provider date fields stay under their own names in evidence/source_dates. A provider-response difference proves neither employer authorship nor an employer edit timestamp. Version first-observed time is not the last time its value was seen.

Legacy rows get `origin='legacy_index'`: evidence contains the original index row, exact retained description and migration provenance, explicitly labelled a legacy snapshot. Recover surviving referenced historical text from Git and attach it with its original hash/path, either in that legacy snapshot's evidence map or additional honestly labelled snapshots when full old records exist. Preserve mixed `legacy_history` as `import/legacy_event` with original JSON; never recategorize old mixed changes as newly proven provider edits. Recover real schema-1 native records as `origin='git_history'`, with original commit/capture provenance, not reconstructed provider JSON. They do not automatically become today's current evidence.

The first modern native observation of a legacy-only posting is a baseline. Compare compatible old normalized fields under the unchanged v1 adapter and retain those differences as labelled legacy comparisons; unknown old fields stay unknown. Do not claim full raw-to-raw attribution. Legacy description verification hashes may bind to the legacy evidence map; unavailable old text retains its hash plus an explicit unavailable marker. New/future history always references durable version rows with readable wording. Import cannot recover material that was never retained anywhere. Renormalization of a legacy-only row without native input is deferred; do not run a new native parser on an invented record.

## D. Audit and SQL access

**Keep one AFTER ROW audit function.** Explicit audit statements would be easier to forget in psql/psycopg edits and would require wrapper-only writes to make equivalent guarantees. This trigger is justified by the loss of Git recovery for mutable controls; it is not a second posting history.

| Table | Audit scope |
|---|---|
| Employers | INSERT/UPDATE/DELETE: identity label/watchlist/notes edits are not elsewhere recoverable. |
| Sources | INSERT/UPDATE/DELETE of registry, association and activation controls. Strip `last_published_run_id` and `processing_revision`; publication/rescreening already have their domain records. Pointer-only updates generate no audit. |
| Leads | INSERT/UPDATE/DELETE: preserve research corrections, wording and cycle decisions. |
| Triage, applications | INSERT/UPDATE/DELETE: recover private decisions, stages and notes. |
| Postings, versions, events, runs, verifications | No generic audit. Posting domain history/run health or append-only evidence already supplies the useful history. |

Before owner/control writes, use `BEGIN; SET LOCAL jobwatch.actor='agent:session-id'; ...; COMMIT;`. The trigger rejects an omitted/blank actor even for direct SQL. Append-only verification/run/event rows also require an actor. `session_user` records the actual database login; `current_user` inside SECURITY DEFINER would record the function owner and is unsuitable. Actor labels are **self-asserted provenance**, not proof of identity or an owner's instruction. Two shared runtime logins cannot provide authenticated owner-versus-agent attribution. If that becomes necessary, issue separate conventional login credentials; do not build an agent identity service.

The audit stores no SQL statement, bind parameters, credentials, provider descriptions or CV content. Full application/triage note images are intentionally retained for recovery, with the same privacy boundary as their source tables. That duplication is the cost of restoring an accidentally overwritten note. Clearing a note does not erase its audit/backup history. Do not copy this table to public research exports. A metadata-only audit would not replace the Git recovery being lost.

One changed row gives one audit row. Multi-row statements share a transaction ID. Failed statements/transactions roll back both row changes and audit rows; attempted failures are not a successful-change audit. No-op updates produce no log. Application modification time comes from this audit; remove the draft's redundant application `updated_at` default, which did not automatically change on UPDATE. The fixed `pg_catalog` function search path and qualified audit target avoid object-shadowing. Runtime roles neither own tables/functions nor have audit INSERT/UPDATE/DELETE/TRUNCATE, trigger-disabling, schema CREATE or role-management rights. An administrative owner can bypass it, as with ordinary PostgreSQL audit triggers; this is not tamper-proof compliance logging.

The two runtime roles are sufficient for this owner's current use:

- `jobwatch_collector`: source registry/public employer reads; corpus/run/evidence/history reads and the specific publication/rescreen writes. No applications, triage, verifications, leads, owner audit or employer notes. No version/event mutation or posting deletion/key rewriting.
- `jobwatch_owner`: broad research reads including excluded, historical evidence and owner context; direct SQL for employer/source controls, leads, triage, applications and append-only verifications. Source INSERT is column-scoped so it cannot seed a collector pointer. No schema migrations or collector observations.

The migration object owner is a separate provisioning/admin credential, never a routine login supplied to an agent. DDL contains no passwords, no membership in the owner role and no blanket future-table grants. If a research-only credential is later needed, add ordinary SELECT grants over the public-corpus tables/view; do not give that session the owner credential. A collector is not a substitute research role: it can write observations.

Direct SQL is first-class. Constraints suffice for ordinary single-row owner/research writes; no mandatory API/MCP or extra permission flow. Source validation requires network/code: an owner-side validator can fetch externally, then update the source check facts with owner credentials; its audit records the change. It need not forge a collector run. Collector-operated validation can additionally record a `mode='validate'` attempt. Collection, rescreening and renormalization require the multi-row contract below. A status-authority label CHECK does not itself prove authorization. Existing owner-instruction semantics remain operational instructions, not a fictitious database authentication claim. PostgreSQL's [column/table grants](https://www.postgresql.org/docs/17/sql-grant.html) provide this boundary.

For two owner sessions editing the same record, use a conditional UPDATE against the previously read value and check affected-row count, or read it FOR UPDATE before deciding inside a short transaction. Ordinary SQL serializes conflicting writes but does not automatically detect stale read/modify/write intent. No row-version framework or generic mutation API is necessary. Audit preserves both committed decisions; scans never write those records.

## E. Collection transaction contract

### Register, fetch, validate

1. Read the source's configured identity, endpoint and processing revision. Construct fetch URLs using supported adapters, never arbitrary candidate URLs.
2. In a short committed transaction, INSERT the run UUID with source, mode, request contract, actor and processing revision; RETURN `run_order,started_at`. `started_at=clock_timestamp()` is issued by the database before any HTTP fetch. The identity sequence supplies a strict order despite timestamp ties or clock adjustments; its gaps are harmless. Sequence allocation order is the defined run order, not proof of an atomic upstream snapshot. On uncertain registration acknowledgement, re-read the same UUID and validate source/mode/revisions; never change an existing run's metadata.
3. Fetch all requests, validate shapes/identity/coverage/counts and construct v1 normalized values in memory, with **no transaction or database locks held**. Preserve complete native records before sanitizing derived text. Record receipt time, source/vacancy/skipped counts and whether absence inference is safe. A retry of the fetch is a NEW run UUID/order. Do not reuse one run UUID for two different fetch attempts.
4. On network/partial/malformed/identity/accounting failure: finalize that running attempt as failed, with a bounded sanitized error and finish time, using source→run lock order. Do not publish partial postings or modify the success pointer. If the database is down, nothing can be published; leave the registered running attempt visible for later cleanup. Only mark a crashed attempt failed once its worker is known dead; age is a warning, not sufficient proof.

### Publish once, atomically, per source

At READ COMMITTED:

```text
BEGIN;
SET LOCAL jobwatch.actor = ...;
SELECT source ... FOR NO KEY UPDATE;       -- first corpus/control row lock
SELECT run ... FOR UPDATE;                -- must belong to that source
if terminal: return stored outcome;       -- lost-COMMIT-ack replay
if source not monitored: finish skipped; COMMIT; return;
if run.processing_revision != source.processing_revision: finish superseded; COMMIT; return;
if last_published.run_order >= run.run_order: finish superseded; COMMIT; return;

read current posting/version state under this source lock;
for each validated identity in stable key order:
    preserve identity and first_seen; insert initial posting if absent;
    retain/deduplicate immutable evidence/representation tuple;
    perform attribution/comparison and append material events;
    move current pointer; record last_seen_at = this fetch receipt;
    update current screening and evaluation metadata;
if absence_safe:
    mark only this source's previously listed, unobserved keys not_seen;
    freeze their prior last_seen_at; missing_since = this receipt;
    append one absence event each;
assert observed identity set and run accounting match the validated feed;
if absence_safe: assert final listed set equals the observed set;
finish run published with fetched/finished/counts/absence_safe;
move source.last_published_run_id;
COMMIT;
```

The preliminary nonlocking run lookup used to locate its immutable source is harmless; the first row LOCK is the source. `FOR NO KEY UPDATE` serializes publishers/rescreen/control changes just as needed while permitting FK key-share checks. Identities never change, so `FOR UPDATE` is unnecessarily strong. All source-owning operations take the same mode. The waiter's READ COMMITTED statement sees the committed winner's source state. These are ordinary PostgreSQL [row lock semantics](https://www.postgresql.org/docs/17/explicit-locking.html).

Every publication/rescreen/renormalize transaction touches **one source only**, then its run (when applicable), then its postings/versions/events in stable posting-key order. Rescreen-all loops through sources, committing each. Do not first lock a set of screenings/postings across sources and later lock a source. This removes the demonstrated opposite-order deadlock. No network or model inference occurs under these locks. Owner source-control edits also acquire that source row first when combined with related mutations.

The small `processing_revision` token represents the pinned normalizer/comparison/rules release. A deliberate processing deployment/rescreen updates it under the source lock together with the corresponding per-source work. A worker built for the old revision skips publication when it eventually acquires the lock. Merely starting a newer binary does not change the token. An old rescreen worker must also check its expected revision before writing. This avoids downgrade races without a new policy registry table. Ordinary daily scans never change the token.

Version select-before-insert is safe under the source lock. If using ON CONFLICT DO NOTHING RETURNING, fetch the existing ID separately when RETURNING is empty. Do not use a dummy UPDATE on immutable evidence just to obtain its ID. Event uniqueness is a backstop; it does not replace atomic run finalization and the terminal-run check.

Retry SQLSTATE `40P01`/`40001` and bounded lock/connection interruptions by rerunning the WHOLE publication transaction with the same run UUID, validated payload AND original fetch receipt time. After uncertain COMMIT, reconnect and check the run before retrying. Do not refetch under that UUID. A failed transaction leaves the previously committed running registration and no partial corpus/history/pointer changes. A terminal failed attempt is not republished: a new fetch gets a new run. Unknown run IDs are errors, never synthesized successes.

### Required outcomes

| Case | Result |
|---|---|
| Older scan finishes after newer published scan | Older order is superseded; no posting or absence changes. A newer failed attempt does not block an older successful result. |
| Concurrent publishers | Source lock serializes them. Newer order wins regardless of completion order. If older commits first, both observations can legitimately appear in history. |
| Already published retry | Return its stored outcome, even if current source state has since advanced. No duplicate versions/events or changed payload publication. |
| Network failure | Failed health attempt only; last good corpus stays. |
| Valid complete empty board | Published with zero vacancies. If absence-safe, mark previously listed keys not_seen, retaining their evidence and owner relationships. |
| Partial/malformed feed | Failure; never publish its subset, never infer absence. |
| Valid response with unproven pagination snapshot | Positive observations can publish with `absence_safe=false`; no missing keys change state. Source health exposes that limitation. |
| Concurrent rescreen | Wait on same source lock, read current versions AFTER acquiring it, evaluate current pinned rules; no stale input overwrite. Deployment token rejects an older collector. |
| Concurrent owner/agent writes | Distinct owner tables remain unchanged by publication. Ordinary row locking/conditional updates handle competing owner edits; audit records committed changes. |
| Failure halfway through updates | Entire publication rolls back, including versions, material events, judgments, absence, health success and pointer. Registration remains retryable. |

**Lever decision:** do not certify the existing offset pagination as absence-safe. Its current loop detects duplicates and bounded-page errors but cannot detect a removal shifting later pages. A recheck of page zero is only a heuristic. Until a supported single-response/snapshot collection contract is established and tested, use positive-only publication for multi-page Lever boards. A validated empty FIRST page can establish an empty response; a later empty page after earlier pages does not prove snapshot completeness. Do not infer absence merely because offset paging terminates. This trades delayed `not_seen` for avoiding the known false-absence mechanism; it adds one run boolean, not a two-miss state machine. The [Lever API documentation](https://github.com/lever/postings-api) documents skip/limit without an atomic snapshot guarantee.

`last_seen_at` is therefore stored per posting after each positive observation. Deriving it from the latest board success would invent positive sightings on incomplete-snapshot/positive-only runs. This small daily write cost is preferable to that incorrect optimization. Historical imports keep recorded positive times or NULL, never a synthetic later sighting.

The database constrains relationships and role boundaries; the trusted collector code still owns completeness certification, event classification, legal terminal-state transitions and this transaction discipline. An arbitrary malicious SQL session using collector credentials can violate domain history by omitting the protocol. No claim otherwise is made. The owner's ordinary SQL role cannot do those writes. A large SECURITY DEFINER publication API would hide Python normalization/rules behind a second implementation without removing that trust boundary.

## F. Search and vectors

Native PostgreSQL FTS works immediately through version `search_tsv`, its GIN index, `websearch_to_tsquery`, and `ts_rank_cd`. Join current versions for current search, default to all verdicts among listed postings, and include source health. Historical/not_seen inclusion is explicit. FTS is not BM25. Keep `search.py`'s Python BM25/tokenization as the reference ranker reading SQL rows. The executed SQL check confirms English FTS still collapses C++ to C. Add shared document/query aliases for C++/C# later if needed; do not promise language-token precision from vanilla FTS. See PostgreSQL's [text search documentation](https://www.postgresql.org/docs/17/textsearch.html).

**Defer the embedding table AND extension installation from the first migration.** Adding a derived FK table later is safe and does not change stable posting/version IDs or collection. `002_deferred_embeddings.sql` shows the required shape: immutable version FK, exact pinned model revision, input recipe, chunker revision/ordinal, complete input SHA and chunk text, fixed vector dimension. Pick the real model/dimension first; 384 in that file is a tested example, not a model selection. Recompute the input SHA from the immutable version plus the declared recipe; a duplicate worker verifies existing text/hash before treating ON CONFLICT as success.

Use exact cosine search first. Filter the selected model/input/chunker revisions, join `embedding.version_id = postings.current_version_id`, and filter listed/source policy. This prevents outdated vectors from being returned as current; no stale boolean or publication pointer on vectors is needed. An async job can finish after a source change: its result is historical and the current join excludes it. A formatting-only version can invalidate otherwise reusable input vectors; accepting that small recomputation cost keeps the guarantee simple. Never compare vectors from different models merely because dimensions match. Reject zero vectors for cosine search. No source facts depend on successful embedding generation.

The local optional DDL/test establishes pgvector loading, fixed dimensions, exact distance queries, ordinary posting joins, and exclusion of a previously stored vector after the current version changes. It uses synthetic vectors and proves no retrieval-quality result. [pgvector](https://github.com/pgvector/pgvector) supports exact search without an ANN index. Do not repeat the previous HNSW benchmark as a cutover requirement. Later hybrid retrieval can combine SQL lexical/vector ranks with reciprocal rank fusion; retain method/rank and source-version references, not an uncalibrated sum of raw scores. No separate vector service, queue, model registry, private CV embedding store or retrieval framework is necessary.

## G. Implementation sequence, deletion and cutover

### Implement in this order

1. **Persistence:** numbered SQL migration derived from this proposal; conventional psycopg connections; source-locked publication and import tooling. Keep `feeds.collect_board` output and the running screening rules exactly as they are. Retain all source fields, including existing country spelling, salary strings, source dates and deadline evidence. Material comparison code can recognize HTML noise without changing stored v1 descriptions.
2. **Historical correctness:** immutable full native evidence, legacy snapshots/recovery, one event stream, comparison fixtures, baseline handling, NUL policy, correct evaluation times and transaction tests. These are required before the first authoritative DB scan, not a later history repair project.
3. **Import/read/cutover:** reconcile every dataset, SQL views/queries/report parity, roles, backup and restore drill. Keep the importer one-shot and deterministic; no JSON-backed compatibility adapter or dual writes.
4. **Deliberate normalization changes:** after cutover, v2 fixes double escaping, Lever list labels, explicit apply URLs/workplace/country projections and compensation. Test and apply each as an attributable normalizer change over retained native evidence; evaluate screening changes separately. Do not disguise them as migration details. Legacy-only rows wait for native evidence.
5. **Source discovery:** expose ordinary candidate/lead SQL writes, bounded validation, evidenced association and atomic activation. DDL allows it from day one, but do not expand coverage during persistence parity work. Employer mapping and endpoint validity remain distinct checks.
6. **Retrieval:** native FTS smoke test/read availability first; measure BM25/FTS after normalization cleanup. Then choose a model, apply derived embedding DDL, exact search; hybrid/ANN only after useful measurements.

### Delete rather than port

| Existing code/mechanism inspected | Action and benefit |
|---|---|
| `state.py`: load/save/serialise/atomic_write, health/text paths, hash-file layout, referenced_texts/latest-predecessor retention, schema-1 upgrade | Move only necessary import decoding into a one-time importer. Delete runtime file persistence/GC and the text→index→health ordering protocol. Transactions/FKs replace cross-file durability. Never retain the 20-event cap. |
| `storage.py` and locks/snapshots/replacement in `applications.py` | Delete entire file-lock/mtime/fsync/rename persistence machinery. PostgreSQL handles concurrency and durability. |
| CSV ledger read/write/import CLI | Retain CSV parsing only in the importer or an explicitly requested export, not as another authority. Spreadsheet formula rules apply to a future CSV export, not database storage. Preserve job keys/stages/notes/date-only semantics. |
| `triage.py`, `verifications.py` file writers and JSON mutation/conflict plumbing | Delete writers; preserve authorization meaning, evidence timestamps, quoted wording and idempotent append observation semantics in SQL usage/application validation. |
| `opportunities.py` Python cross-file joins | Replace repeated joins/action-state rules with the SQL views. Preserve application precedence, exact-vs-loose matching and conflict warnings; a small renderer may still expose warnings. Do not implement the same join independently in several CLIs. |
| `commands.py`/`contract.py` read wrappers, capabilities and `jobwatch.cli/1` compatibility | Retire wrappers that only simulate SELECT/filter/join. Keep network/rules/model operations and useful report/weekly/calendar/BM25 renderers; no compatibility mandate. |
| `datacheck.py` file layout/format/durability checks | Replace runtime file checks with constraints and focused DB health/reconciliation checks. Keep domain/source validation and evaluation cases; do not delete semantic validation just because its present caller is a file validator. |
| `config.py` file loading and provider identity assumptions | DB source registry replaces file/config iteration. Keep source slug/region/provider/URL validation; support many sources per employer. |
| `.github/workflows/scan.yml` commit/push/rebase and contents-write access | Remove when implementing cutover; collector commits DB transactions. Keep scheduling, useful summaries and concurrency grouping as convenience, not correctness machinery. |
| `test_durability.py` and lock/concurrent-file-edit tests | Retire file-ordering assertions with their code. Preserve fail-closed feed, identity, screening, owner privacy and report semantics; replace persistence assertions with database integration checks. |

These are recommendations for the later implementation PR. This branch changes none of those files.

### Reconcile against a frozen commit

The executed current-posting round-trip used main `90434ea`: **629 postings (627 listed, 2 not_seen), 572 excluded / 49 review / 8 priority; 536 retained text files; 571 index_backfill / 54 legacy_retained / 4 observed.** All 629 current descriptions are present, hash-verified and NUL-free. All current source fields and judgments round-tripped through the proposed schema; re-screening DB-held inputs reproduced verdict/codes. This was a current-posting probe, **not the complete production importer**.

The current inventory also has 44 employers, nine health sources, 21 vacancy leads, 13 programmes, 11 verified postings, zero application rows and no triage file. It retains four source events, 54 screening events and 49 mixed legacy-history entries. Counts alone are not reconciliation; [results/reconciliation.json](results/reconciliation.json) pins source hashes and explicitly lists work not executed.

The real importer must cover:

- Every posting key, source/native identity, status, original first-seen/basis, positive last-seen/missing time and current screening fields/timestamp. No verdict-based retention filter. Import every description file, including older retained text, not just those the current `state.load_state` happens to load.
- Every event and original JSON detail. Traverse description references beyond `_row_texts`' latest predecessor; recover available text/native records from Git history with verified hashes and commit provenance. Emit an unresolved-evidence manifest for genuinely absent data. Preserve the old limitation honestly; all new events have readable evidence references.
- All employers/watchlist fields and source configuration/mapping evidence, including notes and original check times. `employers.details.legacy_config` retains original unprojected configuration/check fields; it is an import snapshot, not a second current source configuration. Do not invent a historical endpoint check from a mapping timestamp: either validate it at migration time or leave the source candidate pending a real check. Schema-1 native payload recovery is worthwhile, but never replaces a newer legacy-only snapshot without proof they match.
- Health successes/failures and known counts. Synthetic health-baseline runs use `mode='import',status='imported'`, request/processing labels, original known observation times and `details.legacy_health` holding the original record. Missing counters/times remain NULL, without satisfying the stricter published-feed accounting rule by invention. A known old success can seed the same-source last-success pointer, explicitly labelled imported; `source_health.last_success_at` then reflects that retained legacy time. If no old success time is known, leave the pointer unset. Old failure/attempt details remain queryable in the import snapshot until new real attempts populate current attempt health. Import timestamps record import activity separately from legacy observation times in details; no new fetch is claimed.
- All leads/programmes including `intake_wording`, cycle wording, deadline wording/URL, window, uncertainty and unknown/unspecified markers. Map `open_2027` to `open` + 2027 without changing evidence. Generate deterministic stable lead keys and an explicit programme identity; retain unmapped fields in `details`. Check duplicates, never silently drop a row to satisfy uniqueness.
- Each independent verification status/description/deadline observation with original checked-at, URL, method, actor marker and description hash. Missing historical actor is `unrecorded_legacy`. A record that only says unknown and contains no observation stays unknown: retain its original JSON in the posting's labelled legacy import snapshot instead of fabricating a verification time/URL/method to insert a row. Do not invent a current evidence binding. `legacy_details` retains otherwise unmapped data on real observations.
- Applications and triage from the frozen cutover input even if still empty; preserve all fields, notes, timestamps, manual IDs, explicit posting links and unambiguous exact URL relationships. A fixture with nonempty private owner records is required despite today's empty files.

Import into an empty target in one transaction, with deterministic IDs derived from frozen commit/entity identifiers. Validate key sets and field equality, original payload hashes and evidence readability, not merely totals. Compare per-source listed sets and valid health counts, all verdicts/codes/bases, legacy event counts and recovered/missing-reference manifests. Re-run the importer into a second empty DB and compare logical sorted contents; do not require binary pg_dump equality because generated transaction/audit timestamps are naturally different. Restore a dump to another empty disposable database and run the same reconciliation. The migration owner performs the import with `actor='import:<commit>'`; no need to disable audit triggers.

### Cutover and rollback

Freeze owner writes and scheduled publication; wait for in-flight runs to stop. Pin final commit C and keep a pre-cutover tag/snapshot. Rehearse the full importer/reconciliation/restore beforehand on scratch databases. At cutover import C once, reconcile every dataset above, and take a recoverable backup. A remote host/credentials/region is a separate deployment setup task; nothing in this review provisions one or relies on a vendor price/plan.

Deploy the PostgreSQL implementation and the single writer-target/workflow change together. Remove old mutable data paths and file writers from active HEAD, update SQL-first instructions, retain Git/tag history as migration provenance, and stop daily observation commits. Do not modify historical Git commits to simulate deletion. Keep exported reports disposable/local or explicitly generated, never an alternate writable database. Run one controlled collection, inspect publication/absence safety, history attribution, owner-state equality and queue/report parity, then resume the schedule. Positive-only Lever sources must visibly report their coverage limitation; that is not a failed cutover.

Before ANY DB-authoritative write, rollback can discard the candidate DB and retain/re-enable C. Once DB scans or owner writes have committed, reverting the code and pointing at C loses data. Freeze writers, preserve a fresh dump, and repair/roll forward or restore to a SEPARATE database and reconcile valid later changes before promotion. Do not casually overwrite later owner/evidence records with PITR. No general DB→JSON compatibility exporter or event replay platform is required. Backups, restore procedure and retained import inputs replace Git's implicit recovery; the audit handles small mutable-row mistakes.

## H. Implementation acceptance criteria

- [ ] Production publication follows the registered-before-fetch, READ COMMITTED, source→run lock contract; same-run replay, out-of-order results, empty/failing feeds, pipeline changes and halfway rollback pass integration tests. Neither network fetch nor model inference holds database locks.
- [ ] Full provider material functions pass the actual incident.io and Job Summary fixtures, missing/null/new-field baselines, formatting-only noise and mixed normalizer+source transitions. NUL evidence round-trips exactly. Three consecutive material versions and immutable normalizer outputs remain readable.
- [ ] Real importer reconciles ALL frozen datasets and every field/reference above, including nonempty owner fixtures; unknown legacy evidence remains explicit. v1 outputs/screening match before any intentional normalization change. Two clean imports and dump/restore reconciliation pass.
- [ ] Runtime privilege tests pass for permitted direct SQL and denied schema/evidence/private reads. Omitted owner actor fails, multi-row attribution works, and rollback removes audit rows. Collector cannot read private owner records.
- [ ] SQL research includes excluded/current/historical evidence; exact/manual application links and owner action precedence match. Source health distinguishes failure, valid empty and unsafe absence inference. FTS/BM25 semantics are labelled accurately; vectors are not a cutover dependency.
- [ ] One writer per entity, old writers/workflow commit step retired, approved hosted credentials configured, backup restored successfully, and a controlled scan preserves owner data with no unexplained source/normalizer history.

## I. What to remove or postpone

Remove native projection/epoch machinery, separate native/normalized/section evidence identities, current normalized duplicate columns, standalone current screenings, generic audit of collector rows, arbitrary historical replay, GitHub-specific idempotency identity, exact-head CLI compatibility and file-era recovery choreography. Each either duplicates an authority or handles a problem ordinary transactions/FKs already solve.

Postpone source-check/location/section child tables, embedding/extension migration, model registry, embedding job queue, ANN indexes, hybrid retrieval framework, repost merging, reader-only login until needed, and broad historical-as-of views. Keep three simple views; add queue/change aliases only for demonstrated reuse. Do not port the old HNSW experiment as a first-migration dependency.

The additions that remain are narrowly justified: immutable derivations preserve old wording; composite FKs prevent wrong-entity evidence; run_order removes timestamp ambiguity; processing_revision stops stale worker downgrades; absence_safe stops offset-pagination false absences; the private audit replaces useful Git recovery. This is the design I would implement.

## J. Executed evidence and reproducibility

See [TEST_RESULTS.md](TEST_RESULTS.md) for exact commands, versions, logs, scope and unexecuted acceptance tests. The four requested scratch artifacts were all found and read; byte-identical copies are under `original/`. The original scripts were rerun against their original DDL in a separate disposable container, with only container/output-path substitutions in temporary execution copies. They are not represented as tests of the replacement schema.

No production service was contacted. No workflow, runtime source, existing data file, original design document or main branch was modified. This directory and its dedicated review commit are proposed design/test artifacts only.
