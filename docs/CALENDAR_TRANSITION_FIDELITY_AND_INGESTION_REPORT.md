# Calendar Transition, Discovery Evidence Fidelity, and Hardened Agent Ingestion Report

**Date:** 25 November 2026  
**Deployed Production Change (Approved):** Explicit identity-based calendar inclusion (`/etc/jobwatch/calendar-deadlines.sql` → `SELECT NULL::text AS evidence_key WHERE false`, zero pre-seeded historical events).  
**Strictly Undeployed / Isolated Local Qualification Only:**
- Discovery evidence fidelity schema proposal ([`0011_discovery_evidence_fidelity.sql`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/sql/postgresql/proposals/0011_discovery_evidence_fidelity.sql))
- Hardened agent-contributed evidence ingestion & append-only corroboration ([`0012_agent_evidence_hardened.sql`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/sql/postgresql/proposals/0012_agent_evidence_hardened.sql), [`agent_evidence_roles.sql`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/sql/postgresql/proposals/agent_evidence_roles.sql), [`src/jobwatch/postgres/agent_evidence.py`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/src/jobwatch/postgres/agent_evidence.py))
- No new recurring external fetching services or remote MCP write servers enabled.

---

## 1. Verified Explicit-Calendar Production Transition

Executed on `i-0a21a9da641e3f22e` (`eu-north-1`) at `2026-11-25T12:16 UTC` reusing `jobwatch.calendar_selections` and the existing atomic ICS/S3/CloudFront publisher:

| Verification Check | Pre-Transition | Post-Transition (Verified Production) |
|---|---|---|
| `/etc/jobwatch/calendar-deadlines.sql` SHA-256 | `509950a7…` (all-evidence default) | `92792a74f9c7418ee9378949cf163e5aaf080a03b7beb81c270d16cf4871212b` (`SELECT NULL::text AS evidence_key WHERE false`) |
| `jobwatch.calendar_selections` rows | `0` | `0` (0 of 12 historical events pre-seeded) |
| `jobwatch-calendar.service` | `events: 12`, `warnings: 0` | `Result=success`, `events: 0`, `feeds: 3`, `fresh: true`, `healthy: 1`, `warnings: 0` (`4.373 s` CPU) |
| Snapshot manifest UIDs & `SEQUENCE` | `12` active UIDs (`sequence: 0`) | `snapshot-fd4bf47e6cc64ddab98bf1e6ff7fe712`: **`12` UIDs preserved**, `active_uids: 0`, `inactive_uids_retained: 12`, **`sequences_in_manifest: [1]`** |
| Published ICS files (`deadlines`/`actions`/`combined`) | `12` events | `0` events (`115 B`, `sha256: ede30ad0…`, `warnings: []`) |
| Research MCP `calendar_selection` diagnostic | `policy_selected: true` | `policy: "file"`, `recorded_approvals: 0`, `projected_deadline_events: 0`; both `greenhouse:monzo:8358652` and `greenhouse:riverlane:5000685101` report `status: "not_selected"`, `in_projection: false`, `in_published_feed: false`, `policy_selected: false` |
| Health & CloudWatch alarms | `6/6 OK` | `jobwatch-mcp-health` & `jobwatch-calendar`: `success`; all **`6/6` CloudWatch alarms `OK`** |

End-to-end explicit-inclusion lifecycle invariants (non-auto-inclusion of new vacancies, automatic updates on well-supported deadline changes preserving stable UID and advancing `SEQUENCE 0 → 1 → 2`, non-silent omission of conflicts while keeping the selected vacancy inspectable with `status = 'conflicting'`) were independently verified on PostgreSQL 17 in [`test_explicit_only_policy_updates_selected_and_preserves_uid_across_conflicts`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/tests/postgres/test_calendar_selection.py#L123-L177).

---

## 2. Discovery Evidence Fidelity Design & Measured Costs (Isolated Proposal)

### Why Not Reuse `posting_versions` Directly?

`posting_versions` carries strict composite foreign keys (`(posting_key, source_id) REFERENCES postings` and `(source_id, first_run_id) REFERENCES collection_runs`). Inserting discovered vacancies from unactivated or unverified boards into `posting_versions` would require fabricating `sources` and `collection_runs`, prematurely assigning authoritative native identities to unverified boards.

### Minimal Justified Schema Change ([`0011_discovery_evidence_fidelity.sql`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/sql/postgresql/proposals/0011_discovery_evidence_fidelity.sql))

1. **Extend `discovery_candidates`:**
   - `description_text text` (TOAST-compressed complete original description up to `262,144` chars / `256 KiB` safety ceiling — eliminating the arbitrary `2,500`-char truncation threshold while keeping ordinary SQL queryability!).
   - `evidence_completeness` (`complete` | `partial` | `truncated` | `unavailable`) and `evidence_retention_policy` (`complete_public_ats` | `excerpt_only_terms_restricted` | `metadata_only`).
   - Strict hash separation enforced by DB `CHECK`: `complete_description_sha256` is non-null and MUST equal `sha256(description_text)` **iff** `evidence_completeness = 'complete'`; `excerpt_sha256` hashes `description_excerpt` independently.
2. **Add append-only `discovery_candidate_snapshots`:**
   - Stores immutable historical snapshots `(candidate_key, observed_at, discovery_mechanism, source_url, evidence_completeness, description_text, complete_description_sha256, excerpt_sha256, evidence_sha256, raw_evidence_gz, provenance)`, deduplicated via `UNIQUE (candidate_key, evidence_sha256)`.
   - Retains evidence **independently of `priority`/`review`/`excluded` verdicts**, ensuring historical observations remain inspectable after source edits or disappearance.
3. **Avoid duplication once linked (`research_discovery_evidence` view):**
   - Resolves `effective_description = coalesce(pv.description_text, dc.description_text, nullif(dc.description_excerpt, ''))` and `effective_completeness = 'linked_native_posting_version'` once a candidate links to a monitored posting.

### Measured Storage, Compression, Backup, Retrieval & Ingestion Costs

Measured empirically on all **`2,463` live listed production postings** on EC2 (`t4g.small`):

| Metric | Measured Value |
|---|---|
| Average raw complete description | `6,882.7 B` (`16.17 MiB` across `2,463` postings) |
| Average gzip-compressed complete description (level 6) | **`2,565.0 B`** (`2.68×` compression — virtually identical to old `2,493.1 B` truncated excerpt!) |
| Average native JSON evidence blob (`posting_versions.evidence`) | `11,120.1 B` |
| Compression CPU cost (all `2,463` postings) | `503.9 ms` (`0.20 ms` / posting) |
| Projected storage for `5,000` discovery candidates (complete descriptions) | **`12.23 MiB` compressed** (`32.82 MiB` raw; `0.08%` of `16 GiB` PG volume) |
| Backup impact (with streaming `database_backup.py`) | `+~12 MiB` dump per `5,000` candidates, constant ~`120 MiB` peak RSS (`0` OOM risk) |
| Retrieval impact | Ordinary SQL `SELECT` / `ILIKE` / FTS works directly with zero application decompression; PostgreSQL TOAST fetches full text only when selected |

---

## 3. Revised Agent-Contributed Evidence Model (Isolated Proposal — NOT Deployed)

Implemented in [`0012_agent_evidence_hardened.sql`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/sql/postgresql/proposals/0012_agent_evidence_hardened.sql), [`agent_evidence_roles.sql`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/sql/postgresql/proposals/agent_evidence_roles.sql), and [`src/jobwatch/postgres/agent_evidence.py`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/src/jobwatch/postgres/agent_evidence.py):

### Authority, Attribution & Controlled Ingestion Boundary

- **Zero raw table write grants:** `jobwatch_agent_evidence` has **NO** `INSERT`, `UPDATE`, or `DELETE` privileges on any table (`agent_evidence_observations`, `discovery_candidates`, `discovery_candidate_snapshots`, `agent_evidence_corroborations`, or any owner/collector table).
- **Controlled `SECURITY DEFINER` boundary:** Every write must call `jobwatch.submit_agent_observation(...)` or `jobwatch.submit_agent_vacancy(...)` (`SET search_path = jobwatch, pg_catalog`), which:
  - Binds `db_login := session_user` from the authenticated PostgreSQL session (unforgeable), distinctly from `logical_contributor` (caller identity) and `claimed_actor` (untrusted agent-supplied label).
  - Enforces hourly and daily budgets transactionally per **`db_login`** (`agent_evidence_budgets`) under `pg_advisory_xact_lock`, defeating both arbitrary contributor-name spoofing and concurrent race bursts.
  - Validates semantic calendar dates in BOTH Python (`date.fromisoformat`) and SQL (`parse_strict_iso_date` via `make_date`, rejecting `2026-02-30`).
  - Computes `submission_sha256` across all material fields: identical retries return `duplicate_replayed`, while reusing an `idempotency_key` for materially different content raises `IdempotencyConflictError` (`23505`). Genuine repeat observations at later timestamps get distinct append-only rows.
  - Persists `discovery_candidates` + `discovery_candidate_snapshots` + `agent_evidence_observations` inside **one atomic transaction** for `submit_agent_vacancy`.

### Append-Only 6-State Corroboration Semantics (`agent_evidence_corroborations`)

Prior corroboration attempts are never overwritten; every independent check appends a new row with full provenance (`checked_at`, `checked_by`, `db_login`, `retrieval_status`, `wording_status`, `claim_status`, `overall_state`, `http_status`, `final_url`, `resolved_ip`, `body_sha256`, `bytes_read`, `response_truncated`, `evidence_details`) enforced by database `CHECK` constraints:

| State (`overall_state`) | Exact Meaning & Minimal Criteria |
|---|---|
| `submission_recorded` | Initial agent observation recorded; zero corroboration attempts yet. |
| `source_retrieved` | Official HTTPS source returned HTTP `200`, but quoted wording was not matched and no contrary claim was asserted. **HTTP 200 alone is never corroboration.** |
| `wording_matched` | Quoted wording was independently found in the retrieved source, **without** validating the agent's interpretation (e.g. vague deadline wording with no unambiguous date). |
| `claim_corroborated` | Minimal structured proof satisfied per kind: **`deadline_wording`** (wording matched + deterministic `parse_prose_deadline` extracts exact claimed date); **`source_identity`** (official employer site HTML bidirectionally references exact ATS board); **`new_vacancy`** (HTTP `200` + wording match + no closed banner). |
| `evidence_unavailable` | Retrieval failed or was inconclusive (`http_error`, `blocked_or_403`, `timeout`, `js_shell_or_empty`, `truncated_before_match`, `network_error`, `ssrf_refused`). **Never misclassified as contradiction.** |
| `contradictory_evidence` | Retrieved HTTP `200` official source explicitly asserts a different value/date or closed status contradicting the submission. |

### Network Security & SSRF Qualification

`safe_fetch_public_https` in [`src/jobwatch/postgres/agent_evidence.py`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/src/jobwatch/postgres/agent_evidence.py):
- **HTTPS-only & port allowlist (`443` only)**, no URL credentials, public FQDN required.
- **Strict global IP validation (`_is_public_global_ip`):** Rejects RFC1918 (`10/8`, `172.16/12`, `192.168/16`), loopback (`127/8`, `::1`), link-local / AWS IMDS (`169.254.0.0/16`, `fe80::/10`), CGNAT (`100.64.0.0/10`), multicast/reserved, and IPv4-mapped IPv6 (`::ffff:127.0.0.1`, `::ffff:169.254.169.254`) across **every** DNS answer.
- **Connection-time IP pinning (`_PinnedHTTPSConnection`):** Connects directly to the pre-validated IP (`socket.create_connection((pinned_ip, port))`) with TLS `server_hostname=host` and strict certificate verification — defeating DNS rebinding TOCTOU.
- **Hop-by-hop redirect validation (`max_redirects=3`):** Re-validates scheme, port, credentials, DNS, and pinned IP on every `301/302/303/307/308` hop.
- **Bounded response (`<= 512 KiB`) & `CorroborationBudget`:** Flags truncation explicitly so missing quotes on truncated pages become `evidence_unavailable`, not contradiction.
- **Isolation boundary:** Corroboration fetching must run ONLY inside an unprivileged, network-isolated worker (never inside a privileged DB/root/owner process or with IMDS access).

### Architectural Simplicity Review

Retaining `agent_evidence_observations` + `agent_evidence_corroborations` as dedicated append-only relations (rather than overloading `verifications` or `leads`) is strongly justified: `verifications` directly feeds `posting_deadline_claims` and calendar qualification, and `leads` represents owner-curated records. Keeping agent submissions separate guarantees an uncorroborated submission can never leak into authoritative verifications or owner decisions, while `submit_agent_vacancy` still reuses `discovery_candidates` + `discovery_candidate_snapshots` without duplicating vacancy storage.

---

## 4. Test & Qualification Results

- **Offline security & core unit suite (`315/315` PASSED):** Includes [`tests/test_agent_evidence_security.py`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/tests/test_agent_evidence_security.py) verifying static/DNS SSRF blocking (IMDS, RFC1918, CGNAT, IPv4-mapped IPv6, non-443 ports), connection-time IP pinning, redirect-to-IMDS/private-IP refusal, response truncation & request budgets, and semantic calendar date rejection (`2026-02-30`, `2026-04-31`, `2026-13-01`).
- **ATS discovery experiments (`7/7` PASSED).**
- **PostgreSQL 17 integration tests (`test_agent_evidence_pg` + `test_calendar_selection` = `28/28` PASSED):**
  - Complete `>10,000`-char description retained without truncation, distinct `complete_description_sha256` vs `excerpt_sha256`, excluded verdict snapshot retention, and ordinary SQL view access.
  - Zero raw table `INSERT`/`UPDATE`/`DELETE` bypass for `jobwatch_agent_evidence` (`InsufficientPrivilege` across all 8 tested attack statements, plus verified `jobwatch_collector` cannot invoke `submit_agent_observation`).
  - Transactional concurrency test: `6` concurrent threads using `6` forged `logical_contributor`/`claimed_actor` names against a `2`-slot `db_login` budget yielded **exact `2` recorded, `4` blocked**.
  - Idempotency replay vs `IdempotencyConflictError` on modified payload, genuine repeat observation preservation, atomic rollback on invalid vacancy, SQL `make_date` rejection of `'2026-02-30'`, and append-only 6-state corroboration history (`5` sequential checks preserved intact).

---

## 5. Recommended Deployment & MCP Integration Approach

1. **Stage A (Next recommended production candidate):** Deploy discovery evidence fidelity (`0011_discovery_evidence_fidelity.sql`) + the conservative Ashby `window.__appData` website extraction improvement (see §6 below), once approved.
2. **Stage B (Local-only agent evidence ingestion):** Deploy `0012_agent_evidence_hardened.sql` + `agent_evidence_roles.sql` and qualify local-CLI / peer-authenticated ingestion first under `jobwatch_agent_evidence` (no remote exposure, no unattended remote writes).
3. **Stage C (Optional future remote MCP):** Keep **Research** (`jobwatch_agent_readonly`) strictly read-only and **Owner Actions** (`jobwatch_agent_owner`) strictly for confirmed personal decisions. Only if remote agent writes are later desired, expose a separate narrow Unix-socket MCP server calling `submit_agent_observation`/`submit_agent_vacancy` (`ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)`) while respecting client confirmation without bypass.

---

## 6. Operational Follow-Up: Live Production Yield & Identity Verifier Diagnosis

### Live Production Performance (Distinct from Simulated Benchmarks)

- **12:00 UTC Scheduled Streaming Backup:** Succeeded cleanly (`backups/2026/11/25/dfa612d7f9c74958aa797f361af6fb72.dump`, `18 s`, `16.075 s` CPU, `exit_code: 0`, `VersionId: UO5jGRDo3.B0iQmoxGda42MmjqQK2nmk`).
- **Measured Discovery Yield (2 live probe cycles, `80` boards probed of `13,749`):**
  - `6` `active_uk_relevant` boards (`7.5%` empirical UK-relevant board yield), `66` `active_non_uk`, `8` `empty`, `13,669` `unprobed`.
  - `28` UK candidate vacancies recorded (`0` auto-activated; `26` monitored sources unchanged).
  - Latest prioritized probe (`11:09 UTC`): `40` boards (`24` exploration / `16` priority), `21 GH / 11 Lever / 8 Ashby`, `48` requests, `6.12 MiB`, `60 s`, `0` rate limits → `3` new UK-relevant boards (`ashby:anima`, `lever:margo-group`, `lever:wisdomtree-2`), `4` new candidate vacancies.

### Root-Cause Diagnosis: Why `0 / 6` Achieved `verified_official_link`

Direct read-only inspection of all `6` `active_uk_relevant` boards separated **appropriately conservative refusals (`5/6`)** from **one unnecessary parser limitation (`1/6`)**:

| Board | Observed Behaviour | Classification |
|---|---|---|
| **`ashby:anima`** (Anima Health, `10/11` UK roles) | Hosted page `jobs.ashbyhq.com/anima` is a React SPA (`0` `<a href>` tags in static HTML), but embeds `"publicWebsite": "https://www.animahealth.com"` in `window.__appData`. Running the **exact same strict bidirectional check** against `animahealth.com` immediately succeeded: **`verified_official_link` — `Official site references ashbyhq.com/anima at https://www.animahealth.com/company/careers`!** | **Unnecessary parser limitation** (reading Ashby `window.__appData` `"publicWebsite"` fixes this **without weakening** bidirectional verification). |
| `ashby:10xteam` (freelance AI trainers) | `window.__appData` links to `https://10x.team/`, whose static HTML did **not** corroborate `ashby:10xteam` (`hosted_link_only`). | **Appropriately conservative** (also freelance AI trainer noise filtered by `screen-2026-11-25.1`). |
| `ashby:3e` | `window.__appData` links to `https://www.3eco.com/`, whose static HTML did **not** corroborate `ashby:3e` (`hosted_link_only`). | **Appropriately conservative.** |
| `greenhouse:alphafmc` | Hosted Greenhouse board (`33.6 KiB`) genuinely contains **zero** links to `alphafmc.com`. | **Appropriately conservative** (must never guess domains). |
| `lever:margo-group` | Hosted Lever HTML exceeded `MAX_SITE_BYTES` (`default_http_get` returned `-1`), AND inspecting the first `256 KiB` header revealed **zero** external employer links. | **Minor size-handling quirk + appropriately conservative** (no official link in header). |
| `lever:wisdomtree-2` | Hosted Lever HTML exceeded `MAX_SITE_BYTES` / timed out on fetch. | **Size/timeout limit** (remains safely deferred in `discovered_boards`). |

---

## 7. Remaining Limitations & Decisions

1. **Approval needed before deploying `0011_discovery_evidence_fidelity.sql`:** Ready and qualified locally; would eliminate the 93.7% truncation loss on `discovery_candidates` at `~12.2 MiB` compressed per `5,000` candidates.
2. **Approval needed before deploying `0012_agent_evidence_hardened.sql` + `agent_evidence_roles.sql`:** Ready and qualified locally for Stage B local-only ingestion; remote MCP exposure remains deferred.
3. **Optional small identity-verifier improvement:** Adding read-only extraction of Ashby's `window.__appData` `"publicWebsite"` (and reading the bounded `<head>`/`<header>` prefix on oversized Lever HTML instead of returning `-1`) preserves strict bidirectional official-site verification while unblocking genuine Ashby UK employers like `ashby:anima`.
4. **External discovery sources:** Unattended Workable-wide polling remains disabled; Adzuna and Reed remain deferred.
