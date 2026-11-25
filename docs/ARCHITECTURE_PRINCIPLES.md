# Architectural Philosophy — Graduate Job Watch

**Status:** Enduring operating and design principles (established 25 November 2026)

## 1. Core Identity: Inspectable Evidence Infrastructure

Graduate Job Watch is an **inspectable evidence infrastructure for agents**, not
an autonomous personal decision-making system.

Its primary engineering objectives are:

1. **Information acquisition and recall:** Systematically discover, refresh, and retain public UK graduate and entry-level technical vacancies across monitored ATS boards and bounded discovery pipelines.
2. **Fidelity to original source evidence:** Preserve verbatim raw payloads (`posting_versions.evidence`), exact observed wording, and verbatim deadline sentences without silently rewriting or inventing employer claims.
3. **Freshness:** Keep monitored feeds, discovered-board probes, and source health receipts current within bounded operational budgets.
4. **Provenance and historical preservation:** Record origin, normalizer/rules version, observation timestamps, actor/database-login attribution, and immutable version/event histories. Deactivation or pausing must never delete historical vacancy evidence.
5. **Explicit representation of uncertainty:** Represent missing years, ambiguous cities, rolling recruitment, conflicting deadlines, non-absence-safe runs, and uncorroborated claims explicitly rather than guessing or silently overwriting.
6. **Efficient, expressive retrieval:** Support composable read-only SQL alongside symbol-preserving FTS (`fts_symbol`), two-stage reranking (`bm25_prefiltered`), stemmed FTS (`fts`), full-corpus BM25, and optional off-host vector/hybrid search.
7. **Coherent, inspectable schema design:** Prefer straightforward SQL over existing normalized relations and transparent views rather than opaque abstractions, parallel pipelines, or black-box scoring layers.

## 2. Boundary Between Operational Automation and Personal Judgment

| Layer | Examples | Persistence & Authority |
|---|---|---|
| **Deterministic operational automation** | Scheduled ATS collection, prioritised directory probing with exploration reserve, official-link identity verification, bounded/audited automatic source activation & deactivation, deterministic screening heuristics, prose deadline parsing | **Persisted automatically** by bounded, audited, deterministic code within explicit resource caps. Fully reversible; never deletes historical evidence; manually selected sources remain protected. |
| **Public evidence ingestion** | Newly discovered public vacancies, official source links, observed deadline wording, source identity/relationship evidence | **Separately permissioned from owner actions.** Append-only or non-destructive bounded writes with explicit provenance, timestamps, contributor identity, and contradiction preservation. Recorded agent observations are **not** automatically authoritative verifications. |
| **Personal decisions** | `applications`, stage updates, follow-ups, `triage` decisions, `calendar_selections`, manual source curation | **Owner-instructed only** (explicit confirmation). Separate authority, narrow audit trail, private notes excluded from shared read views and calendars. |
| **Session-based agent reasoning** | Personal suitability assessment, CV fit, application prioritisation, cover-letter/interview advice, ephemeral recommendations | **Ephemeral only.** Never persisted into PostgreSQL; never introduce a persistent LLM judgment or agent suitability layer. |

## 3. Screening, Derived Signals, and Evidence Representation

- Deterministic screening verdicts (`priority`, `review`, `excluded`) are **non-destructive retrieval heuristics**, never authoritative personal suitability judgments.
- Excluded postings remain indexed, searchable, and inspectable in PostgreSQL.
- Prefer exposing **inspectable atomic signals** (structured reason `codes`, explicit `family`, `rules_version`, `screening_basis`, parsed deadline attributes, and source-level metadata) over inventing opaque composite scores.
- Any screening rule change must be versioned (`rules_version`), qualified against an independent held-out sample (including excluded and ambiguous vacancies), and applied via non-destructive rescreening that preserves all retained `posting_versions` and `evidence`.

## 4. Calendar Authority and Conflict Handling

- Calendar membership is an explicit owner/agent selection on vacancy identity (`calendar_selections`), not silent default inclusion of every new posting that happens to expose a deadline.
- Once a vacancy is selected, the deterministic publisher automatically maintains its event when well-supported deadline evidence changes, preserving stable UIDs and advancing `SEQUENCE`.
- Unresolved conflicts must remain inspectable and must never be silently resolved by picking the newest claim; uncertain or unsupported dates are safely omitted while retaining the selection row and manifest UID history.
