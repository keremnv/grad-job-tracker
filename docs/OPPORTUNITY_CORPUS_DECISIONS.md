# Opportunity corpus: architecture decisions and source contract

**Status:** Revised design baseline after independent review; proposed implementation, no runtime changes. See §10 for reviewed findings and exact corrections.  
**Recorded:** 2026-10-23  
**Scope:** Source discovery, ATS collection, common posting representation, source/version history, and agent retrieval.  
**Current deployment:** Schema-2 discovery index, nine monitored ATS boards, private Git-backed state, read-only JSON CLI, deterministic screening, standard-library BM25. The first schema-2 GitHub scan has completed.  
**Authority:** The preferences explicitly agreed with the owner are marked ACCEPTED. Detailed mechanics selected to make those preferences implementable are marked PROPOSED; invite an independent reviewer to challenge them before implementing.

## 1. Decisions register

| ID | Status | Decision | Rationale / boundary |
|---|---|---|---|
| D01 | ACCEPTED | **Coverage-first, attention-on-top.** Preserve all observed postings, including screened-out postings. | Retrieval/agent judgment can be selective without destroying discovery evidence. Coverage is relative to monitored sources, not all UK vacancies. |
| D02 | ACCEPTED | **Rich, source-grounded data.** Preserve useful source-native fields as structured data, and retain descriptions as source evidence. | Stronger queries and later CV analysis; no unsupported eligibility inference. |
| D03 | ACCEPTED | **External agent, session context.** The tracker stores no CV, GitHub-project profile or permanent fit context for this prototype. | User/agent supply context per session when needed. |
| D04 | ACCEPTED | **No Jev in initial prototype.** | No decision-model dependency or stored personal fit score. |
| D05 | ACCEPTED | **BM25 remains the current retrieval baseline.** Add real vector-database retrieval as an optional derived capability, not source authority. | Useful for semantic retrieval and genuine vector-database engineering experience; improvement claims require evidence. |
| D06 | ACCEPTED | **One agent-facing search capability.** Retrieval mode may later be lexical, vector or hybrid, with the same opportunity identities and provenance. | Avoid forcing agents to manage separate retrieval contracts. |
| D07 | ACCEPTED | **Curated plus agent-discovered sources.** Borrow Aboard's candidate/validation/monitoring distinction. | Expand source coverage without conflating a plausible ATS URL with a trusted monitored board. |
| D08 | ACCEPTED | **Agents may write new source candidates and evidenced manual leads through validated operations.** | Do not require manual JSON editing; do not allow agents to invent collector observations. |
| D09 | ACCEPTED | **Material history, not an unchanged-event log.** | Keep meaningful source and judgment changes, while board health captures successful unchanged scans. |
| D10 | ACCEPTED | **Separate source, normalization, interpretation, verification and owner-action semantics.** | A parser change cannot masquerade as an employer edit; a screening change is not an employer event. |
| D11 | ACCEPTED | **Schema.org JobPosting is a checklist, not the internal schema.** Open-jobs-data is a pragmatic normalisation reference. | Our observation, confidence and provenance concepts extend beyond public job-listing vocabulary. |
| D12 | REVISED / PROPOSED | **One selected-native-evidence and material-revision mechanism, plus a current normalized view.** | Keep attributable source evidence and representation fingerprints, without separate authoritative histories for normalized records and sections. |
| D13 | REVISED / PROPOSED | **Retain all referenced historical wording and selected-native evidence.** | References must remain readable across v1→v2→v3. Avoid retaining duplicate unchanged payloads and unnecessary independent section histories. |
| D14 | REVISED / PROPOSED | **A minimal, provider-grounded common posting contract.** | Preserve posting keys and CLI compatibility; storage schema may become 3 if needed for a clean, safe migration. |
| D15 | PROPOSED / OPTIONAL IMPLEMENTATION | **A local, rebuildable vector index (Qdrant currently favoured).** | Genuine vector database engineering is desired; index is a non-authoritative derivative, not a gate for prototype utility. |
| D16 | ACCEPTED | **Material history retrieval, not arbitrary as-of replay.** | Reconstructing a complete historical system state is costly and not required by the demonstrated use cases. |

These are design commitments, not a claim that the PROPOSED mechanics are implemented. Change this register via documented decisions; do not silently alter agreed semantics during implementation.

## 2. Information and responsibility boundaries

- **Sources:** Public ATS boards and separately researched official pages. Raw external text is untrusted data.
- **Collector observation:** The outcome of an attempted board collection; successful complete responses can establish absence, failed or degraded responses cannot.
- **Provider material:** What selected, source-native job fields say. Retain explicit source path, value and version where useful.
- **Normalized posting:** A deterministic adapter representation for cross-provider retrieval.
- **Deterministic screening:** A separately versioned judgment; may be wrong; never controls retention.
- **Verified research:** Bounded evidence, source URL, observation time, actor, and exact wording for consequential claims.
- **Owner state:** Triage and application ledger, authorised independently from any agent recommendation.
- **Derived retrieval:** BM25 and optional vector representations; never source facts.
- **Session context:** CV and personal priorities supplied to the agent at runtime, not stored in the tracker.

An opportunity can be listed, excluded by rules, matched semantically, and dismissed by its owner simultaneously without contradiction.

## 3. Common source-posting contract v1 (logical shape)

**Identity (required):** Stable existing posting_key = provider:board:native_post_id, provider, board, native_post_id, **stable employer ID**, employer label, official posting URL, native-ID basis (direct API ID or vetted hosted-path derivation). Application URL is a **separate optional field**. Never collapse postings on similar titles or guessed canonical URLs. Preserve the existing conservative URL-identity policy. For newly discovered boards, reject region/case-ambiguous namespace collisions until a provider-specific policy is defined; do not retroactively rewrite deployed posting keys.

**Position (required title, otherwise optional):** title, **department names as an array** with source-native IDs when present, team, employment type with raw value and normalized enum only when unambiguous, structured array of locations, workplace arrangement with original provider value, optional structured compensation. Retain the old flat location string as a compatibility/display projection, not the primary source for new filters.

**Location:** Each entry has source label, optional locality, region, country code, optional structured address, source field path and primary/secondary relation when available. Do not infer a country solely from an ambiguous city. Preserve multiple locations individually.

**Workplace:** optional on_site / hybrid / remote / unspecified when explicitly supplied; preserve provider spelling. An absent field is unknown, not on_site. An ATS-provided remote flag and an ATS-provided workplaceType may coexist; conflicting source values should be surfaced, not silently resolved.

**Compensation:** Keep source summary text and optional source-native tiers/components (salary, bonus, equity; min/max; currency; interval; location/zone when stated). Do not reduce a multi-component package or location-specific tiers to an invented universal salary.

**Content:** Current full description text reference and SHA-256; source-native named sections when supplied, preserving their order and labels; section text references/hashes; content extraction/normalizer version. Generic headings parsed from HTML must be marked derived, not native. Never treat embeddings as content.

**Source dates:** Named entries with original provider field path and exact raw value, plus a type when known (first published, last published, provider updated, created, application deadline). Do not rename Lever createdAt to datePosted or Ashby publishedAt to first-published. Date-only deadlines do not acquire an invented time zone.

**Observation:** Source first_seen, first_seen_basis, listed/not_seen, missing_since, last-successful board observation, board health. Initial schema-2 index backfills have unknown actual first-listing dates. Keep observations distinct from provider publication dates.

**Provenance/version:** Provider, material projection version and evidence reference/hash, normalizer revision, normalized fingerprint, description hash and explicitly exceptional field origins. These are attributes of **one posting revision/evidence history**, not independently managed histories. Record collection start/end and evidence observed-at timestamps distinctly from provider edit/publication dates.

**Missing-value semantics:** Do not conflate source missing, not collected, unsupported by adapter, unparseable, and source explicitly stating none. Preserve enough source-path presence/availability information for high-impact fields to avoid fictional values. Provide a small **provider_fields** escape hatch for meaningful provider-native fields. Use null/unknown rather than false certainty; avoid universal provenance wrappers for all optional fields.

**Not part of the source posting:** Owner decisions, applications, a model fit score, visa suitability conclusions, inferred eligibility, source-verification assertions, or search ranking.

**Other entity types:** A monitored board, an agent-discovered source candidate, a separately researched vacancy lead and a graduate programme/cycle are distinct concepts. A programme is not an ATS posting and shouldn't be assigned a fake posting ID. Cross-links may be recorded only with attributable evidence or as clearly tentative relationships.

### Existing adapter preservation priorities

| Provider | Fields to preserve / distinguish | Notes |
|---|---|---|
| Greenhouse | id vs internal_job_id; absolute_url (including vetted custom careers hosts); first_published vs updated_at; application_deadline; multiple department and office IDs/names/locations; exposed metadata when usable | Null internal_job_id indicates prospect posts; offices are not necessarily physical localities; don't treat the present hosted-path restriction as the entire API contract. Employer-provided metadata varies. |
| Lever | hostedUrl vs applyUrl; categories incl. allLocations and commitment; country; workplaceType; opening/body/lists **with list titles**/closing sections; salaryRange and salaryDescription; createdAt | descriptionPlain already combines opening/body in some feeds: do not duplicate it; allLocations includes primary location; country does not automatically apply to every secondary location; createdAt is not necessarily posting time. |
| Ashby | jobUrl vs applyUrl; nested primary and secondary addresses; isRemote and workplaceType; department/team/employmentType; publishedAt; isListed; compensation tiers/components | publishedAt denotes last publication. isListed=false means direct-link-only, **not closed**. Secondary-address shape differs from primary. Compensation tiers must not become a universal base salary. |

Source references: https://docs.greenhouse.io/job-board ; https://github.com/lever/postings-api ; https://developers.ashbyhq.com/docs/public-job-posting-api ; https://schema.org/JobPosting ; https://github.com/ConorsCode/open-jobs-data .

## 4. Material history and version semantics

### 4.1 Exactly what generates an event

1. **First observed on a complete monitored board:** new posting event. If first indexed during migration, use backfilled/index_backfill rather than claiming a new employer opening.
2. **Absent on a later successful complete board observation:** not_seen event. This never means verified closed.
3. **Listed again after not_seen:** reappeared event.
4. **Provider material revision:** record changed source fields with before/after version references and observation time, not merely a changed normalized text hash.
5. **Normalizer/adapter revision:** record a derived-representation revision when the same comparable provider material yields different normalized output. This is not an employer edit.
6. **Screening revision:** record changed verdict/codes with the rule version and source basis; don't mix it into source history.
7. **Evidence refresh / owner edit:** handled in their respective authoritative stores, not appended as employer source edits.
8. **An unchanged successful scan:** no per-posting material event; update board-level latest-success and failure/coverage information.

A provider-side update timestamp that changes while substantive fields are identical should be described as a **source metadata update**, not automatically a description edit. A source field being added to a new adapter whitelist is a new information baseline, not evidence that the employer changed it.

### 4.2 One evidence-and-revision mechanism (REVISED)

**Do not build independently authoritative histories of native records, normalized records, descriptions and sections.** Keep one current normalized posting and one chronological sequence of material revisions/events referencing durable evidence. The content-addressed store may hold several types of immutable evidence, but the history is one logical mechanism. Section references are part of a posting's material evidence, not a separate event stream.

For each relevant collection or reprocessing boundary, retain:
- A **selected provider-native material projection** with exact source values needed to reprocess and compare important fields. Save it at material revisions (and at initial native-evidence baseline), not for every unchanged scan. Its field-selection/canonicalization version and exact evidence digest are recorded.
- The **current normalized posting**, with a normalizer revision and normalized fingerprint. Retain normalized description text versions wherever a material revision/event refers to them; do not separately version every structured field or section by default.
- A **material event** referencing old/new native evidence and appropriate normalized description/evidence where available, carrying the observed interval and a precise event category: first-observed/backfill, provider-response revision, representation revision, metadata-only update, non-comparable baseline or absence/reappearance.
- A separate **screening transition** when a judgment changes, with old/new rules version, input fingerprint, evaluation time and provenance references; this is not a provider event.

**Provenance claims have limits.** A changed comparable native projection proves *the selected provider response fields differ*, not that an employer personally edited the page. A collection interval bounds when we noticed it; it does not provide the exact edit time. Rename UI explanations accordingly.

**Field-group comparability:** A newly captured field such as compensation starts its own baseline without erasing a detectable description edit in that same scan. Compare previously covered fields within compatible material-projection semantics, and baseline only newly available groups. A version number alone does not prove two fingerprints are comparable. A native-projection builder change may need reprojecting the retained older native evidence; if reprocessing is impossible, attribute cautiously and mark the relationship unknown.

**Simultaneous source and adapter changes:** If retained old native evidence can be normalised under the new adapter, use that comparison to isolate representation effects. Otherwise show both changed bases and explicitly uncertain causation; never claim a certain employer edit.

**Array and whitespace semantics:** Preserve exact evidence. Document per-field comparisons: ordered job sections must retain order; true sets of locations may be compared order-insensitively without altering their original observed ordering; superficial HTML/formatting differences must not be mislabeled as meaningful wording edits. Provider updated_at-only changes are metadata updates, not proof of changed responsibilities.

**Legacy boundary:** Existing schema-2 normalized records without selected native material become a declared **native-evidence baseline** at the first qualifying new collection. Never fabricate historical native payloads by reversing normalized fields. New adapters or material-projection rules must treat newly available fields as baselines, not employer changes.

**Retention:** An event referencing a historical description/evidence version requires that exact version to stay readable. The existing implementation currently keeps current and latest predecessor text and caps screening events; it does **not** yet fulfill this guarantee. Garbage collect only evidence unreferenced by current records and retained revisions. Git history alone is not an acceptable domain read API.

**Durability:** Write immutable evidence first, then the index and finally board health; test interruption at each publication point, including a retry against a different feed. Baseline state must be recoverable from the committed index even if health is behind. No database-wide transaction or bitemporal replay is required.

### 4.3 Historical retrieval contract

Provide current state, material events, source-version references, field-level diffs, and when/what was observed. Examples:
- What changed in this vacancy?
- When did the graduate eligibility wording change?
- Which postings disappeared from a complete scan?
- Did our parser change the description representation?
- Which exclusions changed after a screening-rule update?

**No promise of arbitrary point-in-time system reconstruction** in v1. This avoids building a replay engine, bitemporal database or exhaustive stream of unchanged observations.

Do not silently truncate history needed to explain a material change. The current code's bounded screening event list and most-recent-description reference are existing limitations, not proof that the new policy is already implemented. A migration must explicitly address legacy missing versions and any historical evidence we cannot recover.

### 4.4 Retention and operational cost

Retain current evidence and all **referenced material versions** until an explicit archival policy is decided. There is no arbitrary age-based 180-day expiry. This is not a promise to retain every raw HTTP response, every ignored field, or every unchanged daily observation.

Track size and per-provider change frequency. If storage becomes problematic, propose archival/compaction with recoverable evidence and clear semantics. Git history is a useful audit/backup, but the agent should not need to parse Git commits to retrieve a posting's material change history. Public-facing publication must exclude private owner data, and public availability of a vacancy does not automatically grant unrestricted redistribution rights.

## 5. Source discovery and agent writes

Borrow **behavioral patterns**, not infrastructure, from Aboard: https://github.com/danki1337/startups-board .

Source registration progresses from **candidate** to eligible for **monitoring** through independent evidence checks. Do not model validation outcome, monitoring policy and latest source health as one linear enum: keep an explicit monitored flag, a latest validation record (active/empty/invalid/error), separate employer-association evidence, and latest collection health. A valid *empty* board is not invalid. Invalid/unsupported payload differs from temporary network error and throttling. Aboard's actual production scheduler does not require a validation gate; our validation-and-association gate is intentionally stricter.

Agents may:
- Register a source candidate with discoverer, exact URL, evidence and timestamp.
- Validate a provider URL and board payload through bounded read-only checks.
- Record source-backed manual vacancy and programme observations through validated commands.
- Propose a new monitored board, or activate it under a documented standing permission after both **endpoint validation and employer-association validation**, with collision checks and an atomic promotion. Accept only application-constructed supported API endpoints; arbitrary agent-supplied HTTPS URLs cannot become fetch targets.

Agents must not:
- Write synthetic successful collector observations into jobs.json.
- Treat a plausible ATS hostname as proof that it belongs to the claimed employer.
- Convert "listed" into "application form verified open" without a qualifying check and authority.
- Override existing owner triage or application decisions simply because a source or model disagrees.

The user wants flexible agent writes; the contract should expose safe write capabilities rather than require raw JSON edits. Candidate writes should be idempotent, actor/time attributed, and independent of authorisation to activate monitoring. Add stable manual-lead IDs and duplicate/match checks; attach evidence to a known ATS posting rather than inventing a second vacancy. Programme identity must include cycle where appropriate. Resolve region/case namespace collisions and the single-employer-entry config constraint before expanding source registration.

## 6. Search contract and optional embeddings

One agent-facing search/query capability should eventually support structured filters plus lexical, vector and hybrid modes. **BM25 is the current functioning default.** A real vector database is an explicit desired engineering capability, even if the evaluation does not show a quality win. **Research search should default to all currently listed postings including excluded verdicts;** attention queues remain screened. Historical/not_seen content should be an explicit inclusion filter. Description matching, matched passages, corpus coverage and board-health warnings must be easy for agents to obtain. The existing search currently excludes excluded postings by default, includes not_seen, requires --text for descriptions and suppresses board warnings; fix these semantics before evaluating a better retriever.

A local Qdrant-derived index is the current candidate, not part of canonical persistence. It should use deterministic integer/UUID point IDs (with canonical posting key in payload), posting-level result grouping, exact embedding-input hash, embedding-model revision, input/chunking revision, dimensions, and indexing timestamp. Section identity need only persist within its source version. Compare indexed payload versions against canonical state; explicitly remove stale/superseded points. Index builds must be reproducible, idempotent and non-destructive to source data. The vector DB may be rebuilt; no CV, personal context, or private query-derived embeddings are stored for this prototype.

Hybrid rankings, if implemented, must label methods and not sum incomparable raw lexical and vector scores blindly. Every matched section links back to original source evidence and a stable posting key. When the vector index is unavailable, BM25 should work and the missing mode must be reported accurately.

No Jev, decision models, permanent candidate context or embedded autonomous agent is required.

## 7. Migration and acceptance conditions (no implementation authorised by this document)

Prefer additive changes, but **allow a justified on-disk schema 3** if it materially simplifies correctness, baseline markers or safe historical references. Protect stable posting keys, existing CLI contracts, collector integrity and owner data—not the schema version number.

Suggested order:
1. Expand realistic ATS fixtures for native descriptions, missing/malformed fields, sections, location/address variants, compensation and custom posting URLs. Define material groups/comparability, history baseline, retention, source key namespace and activation authority **before** touching the production collector.
2. Prototype the single evidence/revision mechanism in scratch state and test cross-version, mixed-change and interruption cases; correct the existing screening timestamp/version chronology.
3. Integrate native evidence with collection and enrich structured locations, workplace, apply URL, compensation, dates and sections, retaining compatible old projections; rehearse migration and inspect events.
4. Fix broad **research search** defaults, description coverage, stale/failed-board warnings, snippets and structured filters; expose historical evidence comparison through existing agent read commands.
5. Add validated agent write operations for source candidates and manual leads, explicit employer association evidence and atomic promotion authority.
6. Add optional derived Qdrant index and lexical/vector/hybrid modes; measure realistic retrieval queries, latency and freshness.

Minimum acceptance:
- Unknown is never represented as explicitly on-site, ineligible, closed or zero salary.
- A posting's current and previous retained material description can be inspected, with attributable change cause where evidence permits.
- Pure parser changes don't create employer-edit events.
- Failed/partial boards don't create false not_seen or closure assertions.
- New provider fields can be introduced without falsely reporting old postings as employer-changed.
- Source discoveries do not become monitored until validations and authorisation are satisfied.
- Current production identity, application and triage joins continue to work.
- Unit tests run offline; live data migration is rehearsed on scratch copies; scheduled collector is verified after deployment.

## 7a. Concrete current-code findings from the independent review

The external reviewer reported 170 passing tests; this record **does not claim the author independently reran the suite**. The following are evidenced current-code limitations and need distinct regression tests:
- state.merge compares normalized source fields and can call adapter-only representation changes source changes.
- The current text retention helper saves current plus the latest predecessor; v1 is removed after v1→v2→v3, despite surviving historical event references.
- The current screening history is capped at 20 transitions.
- Lever description assembly drops section/list titles and collapses different source fields into one description string.
- The current optional-field adapters collapse missing, malformed and unsupported information.
- The current general search hides excluded postings by default, doesn't search descriptions without --text, can include not_seen by default, and does not carry board-health warnings.
- The current screening's unchanged-verdict path can preserve an obsolete screened_at across new rule/input fingerprints.
- Config/lead validation currently assumes manually maintained trust: no verified employer-to-board mapping enforcement, lead stable ID, duplication control or actor attribution.

These are targets for correctness/utility changes, **not** evidence that the currently running scheduled collector has already failed. Preserve today's successful operational path until tests demonstrate a safe migration.

## 8. Questions for independent design review

A reviewer should challenge rather than affirm this document:
1. Is a versioned native-field projection sufficient to distinguish provider edits from parser changes? Are snapshot references worth their operational/storage cost compared with only normalized historical versions?
2. Which fields are truly material, and how do we avoid noise from provider timestamps, location list order or changing compensation display strings?
3. What invariant would make the schema safe when native projection versions change and historical payloads do not exist?
4. What minimum historical guarantees must we expose in the agent contract, and which should explicitly remain unsupported?
5. Does the proposed logical schema overfit three providers? Which fields need stable normalization versus native escape hatches?
6. How should a user-authorised agent promote a validated discovered board while avoiding bad employer mapping, race conditions and false absence claims?
7. Do we really need both section-level normalized text and native projections in versioned storage? Can the evidence model be simpler?
8. Which parts can be deferred without harming the future BM25/vector interface?

Review expected: concrete failure scenarios, a minimal alternative design, trade-off ranking (must fix / optional / overbuilt), and a testable implementation recommendation. **Do not implement during the independent review.**

## 9. Explicitly deferred decisions

- Which embedding model, dimensions and vector collection layout to use; selection should follow a small benchmark.
- Whether source validation alone permits auto-activation or whether a policy flag is needed.
- Additional ATS providers, Common Crawl discovery and comprehensive UK market coverage.
- Fine-grained graduate programme relationships, deduplication across ATS vendors, or comprehensive career-cycle modeling.
- Permanent personal context, CV storage, automatic personal-fit scoring, Jev, MCP or autonomous application submission.
- Historical full-state replay and automated retention/archival thresholds.
- Public/private code split implementation until the code and data boundaries are audited.

This design record expresses requirements and intended interfaces. It does not claim any new behavior has been deployed.

## 10. Second-opinion review disposition (2026-10-23)

An independent senior engineer examined this document at 52bd87b, the deployed main code at dc8a336, Aboard, provider documentation, relevant tests and example native responses. Its recommendation was **approve direction, revise before implementation**. This revised document accepts the core critique and supersedes contradictory wording in earlier sections.

**Accepted:** D01–D11 and D16; all observed-job retention; external session-context agents; no Jev; evidence-grounded source schema; BM25 plus optional vector DB; material history; inspected source-discovery writes. The user's earlier stated preferences remain binding.

**Narrowed:** D12–D13 to one posting evidence/revision mechanism with a native material baseline and retrievable old wording; no independent histories for every representation/section. D14 permits schema 3 when correctness justifies it. D15 remains an optional derived retrieval capability, but is a consciously desired portfolio/engineering skill.

**Correctness before features:** fix search defaults and stale-source warnings; timestamp chronology; version reference retention; normalization attribution. Extend native evidence via scratch migration first, then widen adapter fields, then source discovery/writes, then vector indexing.

**Not claimed:** ATS response difference proves employer edit time or intent; provider API completeness implies a consistent atomic job-board snapshot; a syntactically valid ATS board belongs to the claimed employer; an embedding improves retrieval by default; older schema-2 data can be backfilled with unavailable native payloads.

**Still needs explicit operational policy before activation:** when validated sources can be monitored automatically under a standing agent authorisation; how to treat provider+region+board identity collisions; how collection should represent a valid empty source versus a degraded/partial response; which changed provider fields are material versus metadata-only. These policies must be encoded as tests rather than emergent from a general framework.

**Pre-implementation decision:** Choose the **small native-evidence extension** over the simpler normalized-only adapter-epoch option because source/parser attribution and exact historical wording are user-valued. If a proof-of-concept demonstrates that native evidence is disproportionately complex, the normalized-only alternative remains valid, but must explicitly disclaim attribution across normalizer epochs.

**Implementation request:** Do not implement from this branch automatically. Review and approve this revised design, then start in a new implementation branch with fixture expansion and scratch evidence/version tests. No merge to main here.
