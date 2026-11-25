# Schema Inspectability Audit & Agent Evidence Ingestion Architecture

**Date:** 25 November 2026  
**Scope:** Complete audit of all `15` base tables and `10` views in schema `jobwatch` against the enduring architectural principles in [`docs/ARCHITECTURE_PRINCIPLES.md`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/docs/ARCHITECTURE_PRINCIPLES.md), plus the isolated authority model for agent-contributed public evidence.

---

## 1. Schema & Inspectability Audit (`15` Tables, `10` Views)

### Coverage Matrix Across the Nine Core Agent Research Questions

| Agent Research Question | Current Schema Support | Gaps / Recommendations (Non-Destructive) |
|---|---|---|
| **1. What was observed?** | `posting_versions` (immutable verbatim `evidence` bytea JSON + full `description_text` + normalized fields + `deadlines` JSONB), `leads`, `verifications`, `discovery_candidates`, `discovered_boards`. | **Critical finding on `discovery_candidates`:** `description_excerpt` is hard-capped at `2,500` chars (`CHECK (char_length(description_excerpt) <= 2500)`). Live measurement across all `2,463` listed postings shows **`93.7%` (`2,308/2,463`) exceed `2,500` chars** (`p50 = 5,062`, `p90 = 7,646`, `max = 13,404`); **`46` postings** place deadline cues after char `2,500` and **`484` (`19.7%`)** place eligibility/clearance/visa/2027 clauses after char `2,500`. If a discovered board's page changes or disappears before source activation, truncated excerpts + hashes alone cannot answer downstream eligibility/deadline questions. **Recommendation:** In a future non-destructive migration, raise `description_excerpt` bound (e.g. `16,000` chars or gzip-compressed `evidence_snapshot bytea`) for UK-relevant candidates (`verdict IN ('priority','review')`) while keeping `description_sha256`/`evidence_sha256` intact. |
| **2. Where did it originate?** | `sources` (`provider`, `board`, `region`, `validation_url`, `mapping_evidence`, `discovered_by`), `posting_versions.origin` (`collector`/`git_history`/`legacy_index`), `posting_versions.provenance`, `discovery_candidates.discovery_mechanisms` & `provenance`, `discovered_boards.directory_sources` & `directory_provenance`, `leads.official` / `source_note`. | Strong provenance separation. `research_postings` omits `sources.discovered_by` and `sources.mapping_evidence`; agents can join `jobwatch.sources` directly in SQL when needed. |
| **3. When was it observed?** | `postings.first_seen_at` (`first_seen_basis`), `last_seen_at`, `missing_since`, `posting_versions.observed_at`, `collection_runs.started_at`/`fetched_at`/`finished_at`, `verifications.checked_at`/`recorded_at`, `discovered_boards.first_discovered_at`/`last_directory_seen_at`/`last_probed_at`. | Excellent separation between collector observation time vs employer `source_dates` (`published_at`, `updated_at`). |
| **4. What has changed?** | `posting_events` (`category`: `observation`/`provider`/`normalizer`/`screening`/`import`, `before_version_id`, `after_version_id`, `changes` JSONB, `comparison_version`), immutable `posting_versions`, `owner_audit` (`agent_audit_summary`). | Clear separation between employer content changes (`provider/changed`), parser upgrades (`normalizer/renormalized`), and heuristic changes (`screening/rescreened`). |
| **5. Which values are normalised or derived?** | `posting_versions.normalizer_version`, `normalized_sha256`, `nul_replaced`; `postings.verdict`, `family`, `codes`, `rules_version`, `screening_basis`, `screening_input_sha256`; `embedding_chunks` (`model_name`, `chunk_sha256`). | Upgraded `screen-2026-11-25.1` adds explicit non-blocking atomic signal codes (`year.2027_explicit`, `level.inclusive_degree_ladder`, `level.benign_tenure_or_cap_only`, `level.desirable_experience_only`, `level.entry_description_only`, `role.software_evidenced_adjacent`) directly queryable via `= ANY(codes)` in SQL without opaque aggregate scores. |
| **6. What remains unknown?** | Explicit `codes` (`level.unknown`, `location.unknown`, `eligibility.unverified`, `evidence.source_text_unavailable`), `collection_runs.absence_safe`, `postings.status = 'not_seen'`, `verifications.status = 'unknown'`, `discovered_boards.identity_status` (`not_checked`/`hosted_link_only`/`unverified_js_or_blocked`). | Well represented. `parse_prose_deadline` explicitly surfaces `missing_year`, `rolling`, and `early_closure_warning` rather than inventing dates. |
| **7. Which observations conflict?** | `posting_deadline_claims` + `posting_deadline_state` (with explicit `superseded_by` DAG, never silently overwriting), `opportunity_workflow` (`triage_application_conflict`, `possible_application_match`, `ambiguous_posting_url`). | Good. For agent submissions, the proposed `research_agent_evidence` view adds explicit `has_conflict` detection across agent observations and active `posting_deadline_claims`. |
| **8. What may be missing due to limited coverage?** | `source_health` (`status`, `absence_safe`, `last_success_mode`, `vacancies`), `research_discovered_boards` (`13,749` total, `13,709` `unprobed`), `embedding_chunks` coverage vs `postings`. | Directly answerable in SQL (`SELECT probe_status, count(*) FROM research_discovered_boards GROUP BY 1`). Missing coverage outside Greenhouse/Ashby/Lever/Workable remains explicit (Workday/Taleo/proprietary portals are only covered via manual leads or future permitted feeders). |
| **9. Which records represent owner decisions?** | Distinct owner tables: `applications`, `triage`, `calendar_selections`, manual `leads`, and `owner_audit` (`actor`, `db_login`). Shared read views (`agent_leads`, `agent_audit_summary`) strip private notes/CV labels/audit images. | Clean authority separation. No wholesale redesign required. |

---

## 2. Isolated Agent-Contributed Evidence Ingestion Authority Model

**Status:** Implemented and verified in disposable PostgreSQL 17 ([`test_agent_evidence_pg.py`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/tests/postgres/test_agent_evidence_pg.py)) — **NOT deployed or enabled in production.**

### Design & Reuse of Existing Infrastructure

- **Newly discovered public vacancies (`kind = 'new_vacancy'`):** Reuses `jobwatch.discovery_candidates` (`discovery_mechanisms = ARRAY['agent_submission']`, `state = 'new'`, `provenance` flagged `unverified_agent_submission: true`) AND appends a provenance record in `jobwatch.agent_evidence_observations`.
- **Other public observations (`official_source_link`, `deadline_wording`, `posting_evidence`, `source_identity`):** Stored in ONE minimal append-only table `jobwatch.agent_evidence_observations` ([`sql/postgresql/proposals/0011_agent_evidence_observations.sql`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/sql/postgresql/proposals/0011_agent_evidence_observations.sql)).
- **Why NOT write directly to `jobwatch.verifications` or `jobwatch.leads`?**
  - Writing to `verifications` (`kind='deadline'`) would immediately promote an uncorroborated agent claim into `posting_deadline_claims` as `provenance='independent_verification'`, violating the requirement that an agent submission must **never** automatically become an authoritative verification.
  - Writing to `leads` would conflate automated/session observations with owner-curated leads.

### Least-Privilege Database Role (`jobwatch_agent_evidence`)

Defined in [`sql/postgresql/proposals/agent_evidence_roles.sql`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/sql/postgresql/proposals/agent_evidence_roles.sql):

| Capability | `jobwatch_agent_evidence` | `jobwatch_collector` / `jobwatch_owner` |
|---|---|---|
| Append observations (`agent_evidence_observations`) | `INSERT` only (corroboration columns **excluded** from grant — cannot self-corroborate!) | `SELECT, INSERT` |
| Update corroboration state (`recorded → corroborated / unresolved_conflict`) | **DENIED** | `UPDATE` on corroboration columns only |
| Modify or delete observations | **DENIED** (append-only) | **DENIED** (no `DELETE`) |
| Insert new discovery candidates (`discovery_candidates`) | Column-scoped `INSERT` only (excludes `source_id`, `linked_posting_key`, `promoted_lead_key`; no `UPDATE`/`DELETE`) | Collector/Owner normal grants |
| Owner tables (`applications`, `triage`, `calendar_selections`, `leads`, `verifications`, `owner_audit`) | **DENIED** | Owner/Agent-Owner boundaries |
| Collector tables (`sources`, `postings`, `posting_versions`, `collection_runs`) | **DENIED** (read-only views only) | Collector normal grants |

### Security Controls (`src/jobwatch/postgres/agent_evidence.py`)

- **SSRF & malicious URL blocking (`validate_public_evidence_url`):** Requires `https://` public FQDN, forbids URL credentials (`user:pass@`), blocks `localhost`, `.local`, `.internal`, loopback (`127.0.0.0/8`, `::1`), RFC1918 private ranges (`10/8`, `172.16/12`, `192.168/16`), link-local / AWS IMDS (`169.254.0.0/16`, `fe80::/10`), and resolves DNS before corroboration fetches to reject DNS-rebinding to private IPs.
- **Bounded payloads & anti-fabrication:** `observed_wording <= 4,000` chars, `claim_summary <= 500` chars, `payload <= 8,192` bytes, `observed_at <= clock_timestamp() + 5 min`, explicit `idempotency_key` deduplication, per-contributor rolling hourly submission budget (`DEFAULT_HOURLY_BUDGET = 60`), and `wording_is_untrusted = true` on `research_agent_evidence`.
- **Contradiction preservation & independent corroboration:** Conflicting `deadline_wording` observations remain append-only and surface `has_conflict = true` in `research_agent_evidence`. Independent corroboration (`corroborate_observation`, collector/owner role only) fetches the official URL through the SSRF-guarded client (`<= 512 KiB`, `15 s` timeout) and records `corroborated` (exact wording match + `body_sha256`) or `unresolved_conflict`.

### MCP Deployment Architecture Review

- **Do NOT add evidence writes to `read_mcp` (`jobwatch_agent_readonly`):** Preserves its strict read-only security invariant (`default_transaction_read_only = on`) and avoids triggering write confirmation on read research tools.
- **Do NOT mix unattended evidence writes into `owner_mcp` (`jobwatch_agent_owner`):** Preserves strict separation between personal owner decisions and untrusted public observations.
- **Recommended production deployment path (pending separate owner approval):**
  1. Start with **local CLI / authorised operator ingestion** (`jobwatch_agent_evidence` peer/tunnel auth) before exposing remote unattended writes.
  2. If remote ChatGPT ingestion is later desired, expose a **third narrow Unix-socket MCP server** (`jobwatch-agent-evidence.service`, `User=jobwatch-agent-evidence`, `jobwatch_agent_evidence` DB role, strict rate caps, fixed structured submission tools — **no arbitrary SQL**, `ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)`, respecting ChatGPT's client confirmation without attempting any bypass).
