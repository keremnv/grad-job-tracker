# Opportunity corpus: architecture decisions and source contract

**Status:** Proposed design baseline for independent review; no runtime changes in this document.  
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
| D12 | PROPOSED | **Versioned provider-native material projection and normalized representation.** | Independently distinguish provider-side information changes and our adapter behavior. |
| D13 | PROPOSED | **Retain referenced versions of material source evidence in content-addressed storage.** | Agents should compare actual before/after wording, not only hashes. |
| D14 | PROPOSED | **An additive logical source-posting contract v1 above today's schema-2 storage.** | Avoid a gratuitous second production migration; retain existing posting keys and working read commands. |
| D15 | PROPOSED | **A local, rebuildable vector index (Qdrant is the current candidate).** | Source records are authoritative; embeddings and indexes are disposable, versioned derivatives. |
| D16 | PROPOSED | **Material history retrieval, not arbitrary as-of replay.** | Reconstructing a complete historical system state is costly and not required by the demonstrated use cases. |

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

**Identity (required):** Stable posting_key = provider:board:native_post_id, provider, board, native_post_id, employer identifier/label, official posting URL. Application URL is a **separate optional field**. Never collapse postings on similar titles or guessed canonical URLs. Preserve the existing conservative URL-identity policy.

**Position (required title, otherwise optional):** title, department, team, employment type with raw value and normalized enum when unambiguous, structured array of locations, workplace arrangement with original provider value, optional structured compensation. Retain the old flat location string as a compatibility/display projection, not the primary source for new filters.

**Location:** Each entry has source label, optional locality, region, country code, optional structured address, source field path and primary/secondary relation when available. Do not infer a country solely from an ambiguous city. Preserve multiple locations individually.

**Workplace:** optional on_site / hybrid / remote / unspecified when explicitly supplied; preserve provider spelling. An absent field is unknown, not on_site. An ATS-provided remote flag and an ATS-provided workplaceType may coexist; conflicting source values should be surfaced, not silently resolved.

**Compensation:** Keep source summary text and optional source-native tiers/components (salary, bonus, equity; min/max; currency; interval; location/zone when stated). Do not reduce a multi-component package or location-specific tiers to an invented universal salary.

**Content:** Current full description text reference and SHA-256; source-native named sections when supplied, preserving their order and labels; section text references/hashes; content extraction/normalizer version. Generic headings parsed from HTML must be marked derived, not native. Never treat embeddings as content.

**Source dates:** Named entries with original provider field path and exact raw value, plus a type when known (first published, last published, provider updated, created, application deadline). Do not rename Lever createdAt to datePosted or Ashby publishedAt to first-published. Date-only deadlines do not acquire an invented time zone.

**Observation:** Source first_seen, first_seen_basis, listed/not_seen, missing_since, last-successful board observation, board health. Initial schema-2 index backfills have unknown actual first-listing dates. Keep observations distinct from provider publication dates.

**Provenance/version:** Provider; adapter version; material projection schema version and digest; normalized-record schema version and digest; source description and section digests; explicit field origin where nontrivial. Timestamp every actual fetch/check with timezone.

**Missing-value semantics:** Do not conflate source missing, not collected, unsupported by adapter, unparseable, and source explicitly stating none. At minimum preserve source-path presence (or field provenance/availability flags) for high-impact fields. Use null/unknown instead of false certainty. Do not add dozens of unused optional flags simply for theoretical completeness.

**Not part of the source posting:** Owner decisions, applications, a model fit score, visa suitability conclusions, inferred eligibility, source-verification assertions, or search ranking.

**Other entity types:** A monitored board, an agent-discovered source candidate, a separately researched vacancy lead and a graduate programme/cycle are distinct concepts. A programme is not an ATS posting and shouldn't be assigned a fake posting ID. Cross-links may be recorded only with attributable evidence or as clearly tentative relationships.

### Existing adapter preservation priorities

| Provider | Fields to preserve / distinguish | Notes |
|---|---|---|
| Greenhouse | id vs internal_job_id; absolute_url; first_published vs updated_at; application_deadline; department IDs/names; office IDs/names/locations; exposed metadata when usable | Null internal_job_id indicates prospect posts; do not count them as actual vacancies. Field availability depends on employer configuration. |
| Lever | hostedUrl vs applyUrl; categories incl. allLocations and commitment; country; workplaceType; opening/body/lists with list titles/additional sections; salaryRange and salaryDescription; createdAt | Source-native section boundaries matter. createdAt is creation time, not necessarily publication time. |
| Ashby | jobUrl vs applyUrl; secondaryLocations and addresses; isRemote and workplaceType; department/team/employmentType; publishedAt; isListed; compensation tiers/components | publishedAt denotes last publication; isListed=false needs an explicit non-listing accounting rule. |

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

### 4.2 Two representations, separately versioned

**Native material projection (PROPOSED):** Select the provider-native fields required by the common contract, keeping their original values and relevant section boundaries. Use deterministic, schema-versioned canonical JSON to calculate a SHA-256 fingerprint. Exclude request timestamps, collector bookkeeping and non-material provider noise. Ensure canonicalization is defined and tested, including order-sensitive arrays and semantically unordered arrays. Keep an exact versioned copy (or content-addressed references to exact native content) for each material version we choose to retain. This is a whitelisted projection, not a dump of arbitrary provider payloads.

**Normalized record (PROPOSED):** Deterministic mapping of native material into the common schema, with a separate adapter version and fingerprint. Its description text, sections, structured locations, etc. are the search-facing representation.

Interpretation/screening has a third independent rules version and input fingerprint.

If native material digests are comparable and differ, a provider-field change occurred. If comparable native material is unchanged but normalized output differs, the change was caused by our adapter or its normalization rules. If the projection schemas are not comparable, **do not confidently attribute the difference to the employer**: record a baseline/compatibility event until source material can be reprojected. When both change in one collection, retain both facts and avoid an unjustified single-cause explanation.

A hash alone is insufficient to reconstruct an earlier wording. Retain retrievable material versions for meaningful comparisons. Source-native and normalized text can be stored content-addressably; do not duplicate a large description into every event.

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

Source state: discovered_candidate → validated → monitored, with checked_at, source URL(s), provider/board identity, evidence of employer-to-board association, validation outcome, and last error. A successful *empty* validated board is not invalid. Invalid/unsupported payload differs from temporary network error and throttling.

Agents may:
- Register a source candidate with discoverer, exact URL, evidence and timestamp.
- Validate a provider URL and board payload through bounded read-only checks.
- Record source-backed manual vacancy and programme observations through validated commands.
- Propose a new monitored board, or activate it under a documented standing permission if policy allows.

Agents must not:
- Write synthetic successful collector observations into jobs.json.
- Treat a plausible ATS hostname as proof that it belongs to the claimed employer.
- Convert "listed" into "application form verified open" without a qualifying check and authority.
- Override existing owner triage or application decisions simply because a source or model disagrees.

The user wants flexible agent writes; the contract should expose safe write capabilities rather than require raw JSON edits. The exact promotion/activation policy is an open operational setting, not an excuse to block candidate registration.

## 6. Search contract and optional embeddings

One agent-facing search/query capability should eventually support structured filters plus lexical, vector and hybrid modes. **BM25 is the current functioning default.** A real vector database is an explicit desired engineering capability, even if the evaluation does not show a quality win.

A local Qdrant-derived index is the current candidate, not part of canonical persistence. It should use stable posting/section IDs, exact content fingerprints, embedding-model identifier/version, dimensions, and indexing timestamp. Index builds must be reproducible, idempotent and non-destructive to source data. The vector DB may be rebuilt; no CV or personal context is stored there for this prototype.

Hybrid rankings, if implemented, must label methods and not sum incomparable raw lexical and vector scores blindly. Every matched section links back to original source evidence and a stable posting key. When the vector index is unavailable, BM25 should work and the missing mode must be reported accurately.

No Jev, decision models, permanent candidate context or embedded autonomous agent is required.

## 7. Migration and acceptance conditions (no implementation authorised by this document)

Implement **additively** on top of the currently deployed schema-2 index, preserving provider posting keys and existing working JSON commands. A logical contract version does not imply that the on-disk schema must immediately become schema 3.

Suggested order:
1. Expand realistic ATS fixtures to cover present, missing, malformed and provider-specific fields.
2. Implement native-material projections, normalized field provenance and version comparability; rehearse migration without false employer-edit events.
3. Add structured location/workplace/apply URL/compensation/date fields and native description sections; keep compatibility projections for existing screening.
4. Expose structured query filters, section snippets and provenance through existing agent read capabilities.
5. Add validated source candidate / manual-lead writes, with explicit promotion to monitored state and coverage health.
6. Add derived vector index and lexical/vector/hybrid query mode; evaluate on the same realistic queries.

Minimum acceptance:
- Unknown is never represented as explicitly on-site, ineligible, closed or zero salary.
- A posting's current and previous retained material description can be inspected, with attributable change cause where evidence permits.
- Pure parser changes don't create employer-edit events.
- Failed/partial boards don't create false not_seen or closure assertions.
- New provider fields can be introduced without falsely reporting old postings as employer-changed.
- Source discoveries do not become monitored until validations and authorisation are satisfied.
- Current production identity, application and triage joins continue to work.
- Unit tests run offline; live data migration is rehearsed on scratch copies; scheduled collector is verified after deployment.

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
