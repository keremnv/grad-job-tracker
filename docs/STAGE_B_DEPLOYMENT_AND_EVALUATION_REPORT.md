# Graduate Job Watch — Stage B Deployment, Security Verification, Remote Ingestion Proposal & Empirical Agent Evaluation

**Date:** 2026-11-25 (UTC)  
**Deployed Commit:** `3ff4cd7c7af5a327163798effa0a02f281626989` (`origin/main`)  
**Production Host:** EC2 `i-0a21a9da641e3f22e` (`t4g.small`, `eu-north-1`, PostgreSQL 17 + `pgvector 0.8.7`)  
**Scope:** Local-only Stage B agent evidence ingestion and isolated corroboration deployed. **Remote ingestion authority is NOT exposed** (proposal only in Section 6). Unattended Workable-wide polling remains disabled. No existing owner records (`applications`, `triage`, `leads`, `verifications`, `calendar_selections`) were modified.

---

## 1. Evidence Lifecycle Defect Fixes & Regression Tests

### Defect 1A — Legacy discovery snapshot collision (`truncated` → `complete`)
- **Root cause:** `jobwatch.discovery_candidate_snapshots` enforced `UNIQUE (candidate_key, evidence_sha256)`. Because `evidence_sha256` hashes the raw upstream payload, re-observing an unchanged source payload after Stage A (`0011`) with greater retained completeness (`truncated` → `complete`) collided with the legacy truncated backfill row (`ON CONFLICT DO UPDATE` bumped `last_observed_at` without inserting a complete snapshot), leaving 6 production candidates whose `discovery_candidates.evidence_completeness = 'complete'` still pointed to a `truncated` `current_snapshot_id`.
- **Fix ([0012_agent_evidence_and_snapshot_fidelity.sql](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/src/jobwatch/postgres/migrations/0012_agent_evidence_and_snapshot_fidelity.sql), [discovery.py](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/src/jobwatch/postgres/discovery.py)):**
  - Replaced `UNIQUE (candidate_key, evidence_sha256)` with PostgreSQL 17 native `CONSTRAINT discovery_snapshots_retained_identity_key UNIQUE NULLS NOT DISTINCT (candidate_key, evidence_sha256, evidence_completeness, evidence_retention_policy, complete_description_sha256, excerpt_sha256)`.
  - Updated `_upsert_snapshot()` to conflict on `discovery_snapshots_retained_identity_key`.
  - Healed all 6 existing mismatched production candidates in migration `0012` while preserving their historical `truncated` backfill snapshots intact and appending complete snapshots as `current_snapshot_id`.
- **Live production verification:** `snapshot_mismatch_count = 0`, `total_snapshots = 36` (`8 complete`, `28 truncated`). All 6 healed candidates (`ashby:3e`, `ashby:anima`, `greenhouse:alphafmc`, two `lever:margo-group` roles, `lever:wisdomtree-2`) retain both their legacy `truncated` (~2,499–2,500 chars) and upgraded `complete` (4,538–7,580 chars) snapshots.

### Defect 1B — Post-activation candidate linking lag
- **Root cause:** Newly activated employer boards collected native postings via `Collector._publish_once()`, but previously discovered rows in `discovery_candidates` waited for a future unrelated discovery probe to populate `linked_posting_key`.
- **Fix ([discovery.py](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/src/jobwatch/postgres/discovery.py), [publisher.py](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/src/jobwatch/postgres/publisher.py)):**
  - Implemented `link_candidates_for_source(conn, source_id, run_id=..., linked_at=...)` and invoked it inside `Collector._publish_once()` after publishing a source's native postings.
  - Links deterministically via: (1) exact native ATS identity (`provider`, `board`, `native_id`), or (2) unambiguous 1-to-1 exact normalized URL match for search-only candidates (`native_id IS NULL`).
  - Refuses speculative linking when multiple postings share a URL, preserves owner-curated candidate states (`promoted_lead`, `dismissed`), and appends `linked_on_collection` provenance (`run_id`, `linked_at`, `method`).
- **Live production verification:** During deployment, `jobwatch-collect.service` immediately linked all 3 pre-existing Anima Health and Evervault candidates (`ashby:anima:785441b7-54ea-4840-bf27-b5e8bf71430c`, `ashby:evervault:101bdb4d-0dd3-4c42-b904-2253238cae27`, `ashby:evervault:da95abe6-9799-4684-bc0e-e0f81225b8e6`) via `exact_native_identity`. `research_discovery_evidence` immediately transitioned them to `effective_evidence_source = 'native_posting_version'` (`native_posting_version: 3`, `discovery_candidate_snapshot: 27`).

### Regression & Readiness Test Coverage
- **Offline suite:** `317 passed` (including `tests/test_release_readiness.py` verifying migration `0012`, `nftables` unit/rule artifacts, `jobwatch-corroborator.service`, backup role list, and `ReadTools.sql_schema` compaction).
- **PostgreSQL 17 integration suite:** `220 passed` across `tests/postgres/` including:
  - [test_discovery_lifecycle.py](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/tests/postgres/test_discovery_lifecycle.py): truncated→complete upgrade with identical `evidence_sha256`, identical retained-evidence deduplication, immediate post-collection linking, ambiguous-URL refusal, and owner-state preservation (`promoted_lead`/`dismissed`).
  - [test_agent_evidence_pg.py](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/tests/postgres/test_agent_evidence_pg.py): `SECURITY DEFINER` boundaries, SQL-side hash & `candidate_key` derivation, idempotency, transactional budgets, negative privilege checks for `jobwatch_agent_evidence` and `jobwatch_corroborator`, anti-contamination of collector candidates, `posting_deadline_claims` admission & conflict detection, and `admit_corroborated_vacancies()`.
  - [test_schema.py](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/tests/postgres/test_schema.py): full schema + migration idempotency up to version 12.

---

## 2. Stage B Production Deployment Evidence

### Release Artifacts & Deployment Actions (`SSM_COMMAND_ID=c28ff339-26f8-4403-a076-28a6e173c009`, `Status=Success`)
| Artifact / Action | Identifier / Digest | Result |
|---|---|---|
| Pre-deploy S3 backup | `sha256: 7ba70dd68f06184bca46ff5a60f9765f5a6cc11960eef55addff902e11be4197` (`38,411,538` B / `36.63 MiB`) | Verified & uploaded |
| App release (`app.tar.gz`) | `sha256: f48441b36a802e116ca870e11c59ac26c8444a9c4e949a6c3025d8d6f05c617f` · S3 VersionId `p4fN76EFEL9mGKk0OCKwFlU9VlXjOils` | Installed to `/opt/jobwatch/current` |
| Agent release (`agent.tar.gz`) | `sha256: 763ce91f879b7134afe06a71ae5da703e8bb4da0663f4e593e2cb62922b1134d` · S3 VersionId `tEYu7VLkM9a6vwTum75jQH9HwR8ypRRR` | Installed to `/opt/jobwatch-agent-runtime` |
| Schema migration | `0012_agent_evidence_and_snapshot_fidelity.sql` | `migrations_count=12`, `migrations_max=12`, idempotent re-run verified |
| OS & DB identities | `jobwatch-agent-evidence` ↔ `jobwatch_agent_evidence`; `jobwatch-corroborator` (`uid 984`) ↔ `jobwatch_corroborator` | Peer auth in `/etc/jobwatch/pg_ident.conf` & `pg_hba.conf`; roles provisioned via [agent_evidence_roles.sql](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/sql/postgresql/agent_evidence_roles.sql) |
| Kernel egress isolation | `/etc/nftables.d/jobwatch-corroborator.nft` included from `/etc/nftables.conf` | `nftables.service` enabled & active (`inet jobwatch_corroborator_egress`) |
| Corroborator unit | `/etc/systemd/system/jobwatch-corroborator.service` | Installed as manual `oneshot` (**no automatic timer** enabled) |
| Read MCP `sql_schema` fix | Compact `sql_schema()` (`83,640` B across 33 relations vs `131,072` B cap); full constraints/indexes/view SQL on `sql_schema(table=...)` | Verified live (`130.7 ms` full, `12.2 ms` per-table) |

### Post-Stage-B Backup & Real Off-Host `--network none` PG 17 Restore Verification
- **Post-Stage-B streaming S3 backup (`SSM_COMMAND_ID=a963ac37-c233-42f5-a3ae-f4b28bc73653`):**
  - Archive: `backups/2026/11/25/81f0302ab82d4e3aa959fa14b5ea4720.dump` (`39,046,459` B / `37.24 MiB`), SHA-256 `128d8c7cdade9244fa2ade30ac773acf5560cd968bac20effd30986768603a40`, S3 VersionId `uznKKt9X40fmaMtkce.nQ4PAgg6kIdbn`, `s3_checksum_and_version_confirmed = true`.
  - Manifest: `backups/2026/11/25/81f0302ab82d4e3aa959fa14b5ea4720.json`, SHA-256 `902a6fad6617875e051ba25cbc445e708d2c226c4983ab491ac76e3a223bec09`, S3 VersionId `M2G_RtA_HoJ9miEvGdjVHRRCM05BtBlB`.
- **Off-host restore verification:** Downloaded both versioned objects into a `0700` temporary directory (deleted on exit), launched `jobwatch-postgres-vector:17.11-0.8.7` with `--network none`, executed `database_backup.prepare_recovery_roles()` as `postgres` and `database_backup.restore()` as `jobwatch_migrator`:
  - Result: `{"schema": "jobwatch.aws-restore/1", "archive_sha256": "128d8c7cdade9244fa2ade30ac773acf5560cd968bac20effd30986768603a40", "exact_rows_schema_and_sequences": true, "ready_for_cutover": false}`.
  - Verified **20/20** fingerprinted tables (`agent_evidence_observations = 2`, `agent_evidence_corroborations = 2`, `discovery_candidates = 30`, `discovery_candidate_snapshots = 36`), **5/5** sequences, **12/12** migrations, `schema_sha256`, `security_sha256`, and all **8/8** roles including `jobwatch_agent_evidence` (`EXECUTE` only on the two `SECURITY DEFINER` functions, `INSERT` on observations `false`) and `jobwatch_corroborator` (`INSERT` on corroborations `true`, `UPDATE` `false`, `INSERT` on observations `false`).

### Production Resource Consumption, Health & Rollback Procedure
- **Host memory (`free -m`):** `725 MiB` used, `1,109 MiB` available out of `1,834 MiB` total (0 swap).
- **Disk usage:** `/` at `5.3G / 11G` (`50%`), PostgreSQL volume `/var/lib/jobwatch-postgresql` at `464M / 16G` (`4%`).
- **PostgreSQL footprint:** Total database `240,678,579` B (`229.5 MiB`). New/affected tables: `agent_evidence_budgets` `16 KiB`, `agent_evidence_corroborations` `64 KiB`, `agent_evidence_observations` `104 KiB`, `discovery_candidate_snapshots` `296 KiB`, `discovery_candidates` `928 KiB`.
- **Service & Alarm status:** `systemctl --failed`: `0` failed units. All 6 CloudWatch alarms (`grad-jobwatch-backup-health`, `grad-jobwatch-calendar-health`, `grad-jobwatch-cpu-credit-balance`, `grad-jobwatch-host-status`, `grad-jobwatch-mcp-health`, `grad-jobwatch-operation-failure`) are `OK`.
- **Rollback procedure:** Because Stage B is strictly additive and has no automatic timer, immediate operational disablement requires only revoking `LOGIN` on `jobwatch_agent_evidence` and `jobwatch_corroborator` (`ALTER ROLE ... NOLOGIN`) and stopping/masking `jobwatch-corroborator.service` while preserving all evidence rows. Full schema/data rollback uses the pre-deploy S3 archive (`sha256: 7ba70dd6...`) restored to a fresh target and reconciled per `docs/AWS_BACKUP_POLICY.md` (never restarting legacy Git).

---

## 3. Actual Network-Isolation & Permission Verification (Live EC2)

### Kernel-Level `nftables` Egress Isolation (`SSM_COMMAND_ID=3a2733ae-531b-4dcf-8fc9-cafee23ea26b`)
Raw Python sockets and `urllib` executed directly as `jobwatch-corroborator` (`uid 984`), **completely bypassing application-level SSRF checks**, against the active `inet jobwatch_corroborator_egress` table:

| Target (raw socket / HTTPS bypassing app SSRF) | Expected | Actual Result |
|---|---|---|
| AWS IMDS `169.254.169.254:80` | Blocked | `connected=false` (kernel blocked) |
| Local PostgreSQL TCP `127.0.0.1:5432` | Blocked | `connected=false` (kernel blocked) |
| Localhost MCP/admin port `127.0.0.1:8765` | Blocked | `connected=false` (kernel blocked) |
| IPv6 loopback `[::1]:80` | Blocked | `connected=false` (kernel blocked) |
| VPC resolver HTTP `172.31.0.2:80` | Blocked | `connected=false` (kernel blocked) |
| RFC1918 `10.0.0.1:443` | Blocked | `connected=false` (kernel blocked) |
| RFC1918 `192.168.1.1:443` | Blocked | `connected=false` (kernel blocked) |
| Public non-443 HTTP `93.184.216.34:80` | Blocked | `connected=false` (kernel blocked) |
| Public HTTPS `https://www.animahealth.com/company/careers` (443 via `127.0.0.53:53` DNS) | Allowed | `connected=true`, `http_status=200`, `2048` B preview read |

### OS Filesystem, Peer-Auth Impersonation & Live DB Privilege Audits
- **OS secret & socket denial (`SSM_COMMAND_ID=97028938-1f40-4e44-805d-931af5e70aa7`):** Both `jobwatch-corroborator` and `jobwatch-agent-evidence` received `denied_EACCES` on **all 8** tested sensitive paths (`/etc/jobwatch/tunnel-read.env`, `/etc/jobwatch/tunnel-owner.env`, `/var/lib/jobwatch-backup/latest.json`, `/var/lib/jobwatch-calendar`, `/var/lib/jobwatch-agent-owner`, `/run/jobwatch-agent-read`, `/run/jobwatch-agent-owner`, `/run/jobwatch-tunnel-read`). Note: `/etc/jobwatch/aws.json` and `/etc/jobwatch/calendar.json` are `0644 root:root` and hold only non-secret metadata (`bucket`, `log_group`, `log_stream`, `region`, `deadline_policy`, `output`).
- **PostgreSQL peer-auth impersonation:** Both OS users were refused (`denied_peer_auth`) when attempting to connect as `jobwatch_owner`, `jobwatch_migrator`, `jobwatch_collector`, or `postgres`.
- **Live PostgreSQL privilege boundaries (`SSM_COMMAND_ID=11ed41ef-...` & `927a3446-...`):**
  - `jobwatch_agent_evidence` (`NOINHERIT`, `CONNECTION LIMIT 5`, `statement_timeout=5s`): **9/9** direct table operations refused with `InsufficientPrivilege` (`INSERT` into `agent_evidence_observations`, `agent_evidence_corroborations`, `agent_evidence_budgets`; `DELETE`/`UPDATE` on `postings`, `sources`, `applications`, `calendar_selections`; `SELECT` on `applications`, `verifications`). Can ONLY execute the two `SECURITY DEFINER` ingestion functions.
  - `jobwatch_corroborator` (`NOINHERIT`, `CONNECTION LIMIT 2`, `statement_timeout=10s`): **10/10** disallowed operations refused with `InsufficientPrivilege` (`UPDATE`/`DELETE` on `agent_evidence_observations` and `agent_evidence_corroborations`; writes to `postings`, `sources`, `applications`, `calendar_selections`; `SELECT` on `applications`; `EXECUTE` on `submit_agent_observation`). Can ONLY `SELECT` allowed public tables and `INSERT` append-only rows into `agent_evidence_corroborations`.

---

## 4. End-to-End Evidence-Ingestion Examples

### Live Production Example A — `source_identity` + Idempotent Replay (`source_retrieved`)
1. **Submission (`jobwatch-agent-evidence` via `jobwatch-evidence submit-observation`):**
   - `idempotency_key`: `stageb-live-verify-anima-001`, `source_id`: `37` (`ashby:anima`), `url`: `https://www.animahealth.com/company/careers`, `observed_wording`: `jobs.ashbyhq.com/anima`.
   - Result: `observation_id = aa9e27cf-f4fb-4ae9-9601-52dd9ab400bf`, `status = "recorded"`, `overall_state = "submission_recorded"`, `submission_sha256 = b241b848457b3129fdde3c116bd6c92a76bc4522c7fe53bc1e6d29ff573efbf8`.
2. **Idempotent replay:** Exact same command returned `status = "duplicate_replayed"` with identical `observation_id` and no extra row.
3. **Isolated corroboration (`systemctl start jobwatch-corroborator.service`):**
   - `corroboration_id = 9b617320-0bd3-47ba-ba58-c4e861ddeb85`, `http_status = 200`, `resolved_ip = 216.150.16.193`, `bytes_read = 38207`, `body_sha256 = 7b780574d5cc9f363374d369b522f4d6f008678b67f98e547cc9de6cfb68db17`.
   - Because Anima Health's Astro careers page did not contain literal `jobs.ashbyhq.com/anima` in stripped HTML text (only in link/island attributes), the corroborator **honestly** recorded `wording_status = "not_found"`, `claim_status = "unverified"`, `overall_state = "source_retrieved"` instead of falsely asserting corroboration.

### Live Production Example B — `posting_evidence` (`bound_exact_url` + `wording_matched`)
1. **Submission:** `idempotency_key = stageb-live-verify-posting-002`, `posting_key = greenhouse:canonical:8416556`, `url = https://job-boards.greenhouse.io/canonical/jobs/8416556`, `observed_wording = "Enterprise Account Executive - Federal"` → `observation_id = c4a35596-01a7-4d32-bfd8-c2b8f54d561b`.
2. **Isolated corroboration:** `corroboration_id = da871b66-48e1-4127-b3f1-5a7522de59eb`, `http_status = 200`, `resolved_ip = 65.9.62.106`, `bytes_read = 146845`, `target_binding_status = "bound_exact_url"`, `wording_status = "matched"`, `overall_state = "wording_matched"`.
3. **Research MCP readback (`jobwatch_agent_readonly`):** Both observations (`19.6 ms`) and both corroborations (`9.3 ms`) are immediately queryable via `jobwatch.research_agent_evidence` and `jobwatch.research_agent_corroborations`.

### Off-Host Production-Clone Example C — `deadline_wording` Admission, Conflict Exposure & `new_vacancy` Admission
Executed against the restored `--network none` PG 17 production clone (zero mutation to live production):
1. **Uncorroborated deadline (`demo-deadline-001` on `ashby:anima:785441b7-54ea-4840-bf27-b5e8bf71430c`, date `2026-12-15`):** `posting_deadline_claims` count = `0` prior to corroboration.
2. **After bound `claim_corroborated`:** Immediately visible in `posting_deadline_claims` with `provenance = 'corroborated_agent_observation'`, `value = '2026-12-15'`, `superseded_by = []`, `has_conflict = false` — **without owner approval** — while `calendar_selections` remained unchanged.
3. **Conflicting corroborated deadline (`demo-deadline-002`, date `2026-12-20`):** Both claims preserved side-by-side; `research_agent_evidence.has_conflict` immediately flipped to `true` on both observations with zero silent supersession.
4. **Corroborated `new_vacancy` (`demo-vacancy-001` → `agent:7e4eabd8d25da38735f4f4ac`):** Stored initially as `rules_version = 'unscreened-agent-submission'`, `verdict = 'review'`, `screening_basis = 'title_metadata_only'`. After bound `claim_corroborated`, deterministic `admit_corroborated_vacancies()` promoted it to `verdict = 'priority'`, `family = 'software'`, `rules_version = 'screen-2026-11-25.1'`, `screening_basis = 'full'`.

---

## 5. Evidence Admission & Corroboration Semantics

Stage B separates **public evidence acquisition/admission** from **personal owner decisions** across six explicit, non-conflated states:

| Layer | Storage / View Representation | Enforced Semantics |
|---|---|---|
| **1. Agent-reported observation** | `agent_evidence_observations` (`overall_state = 'submission_recorded'`) | Untrusted claim with authenticated `db_login`, `logical_contributor`, `observed_at`, SQL-derived hashes, and `wording_is_untrusted = true`. **Excluded** from `posting_deadline_claims` and deterministic screening. |
| **2. Retrieved source evidence** | `agent_evidence_corroborations` (`overall_state = 'source_retrieved'`) | Isolated `jobwatch-corroborator` fetched public HTTPS 443 (`http_status`, `final_url`, `resolved_ip`, `body_sha256`, `evidence_completeness`, `evidence_excerpt`), but wording did not match or target was unbound. |
| **3. Corroborated wording** | `agent_evidence_corroborations` (`overall_state = 'wording_matched'`) | Target vacancy bound (`bound_exact_url` or `bound_host_and_identity`) and claimed wording matched retrieved source text, without asserting a structured deadline/vacancy claim. |
| **4. Admitted corroborated claim** | `posting_deadline_claims` (`provenance = 'corroborated_agent_observation'`) and `admit_corroborated_vacancies()` | Requires `overall_state = 'claim_corroborated'` **AND** `target_binding_status IN ('bound_exact_url', 'bound_host_and_identity')`. Participates automatically in ordinary research without individual owner approval. |
| **5. Conflicting / unresolved claim** | `research_agent_evidence.has_conflict = true`; multiple unsuperseded rows in `posting_deadline_claims` | Additive only: never silently overwrites or supersedes feed observations, independent verifications, or other corroborated claims. Calendar projection omits conflicting dates with explicit conflict reason. |
| **6. Authorized personal decision** | `applications`, `triage`, `leads`, `verifications` (supersession), `calendar_selections` | Strictly owned by `jobwatch_owner` / `jobwatch_agent_owner` under explicit owner instruction. Selected calendar identities automatically reflect subsequent non-conflicting corroborated deadline updates, while unselected vacancies never auto-enter the calendar. |

---

## 6. Remote Ingestion Integration Proposal (Proposal Only — NOT Deployed)

### Why NOT Reuse Research MCP or Owner MCP
- **`jobwatch-agent-read` (Research MCP):** Runs under `jobwatch_agent_readonly` with `default_transaction_read_only = on` and `SET TRANSACTION READ ONLY`. Adding write tools would break the structural read-only invariant that allows safe, unprompted SQL/search exploration during research sessions.
- **`jobwatch-agent-owner` (Owner MCP):** Holds authority over personal decisions (`applications`, `triage`, `calendar_selections`, `verifications`). Mixing untrusted public evidence submission into Owner MCP would violate the core boundary separating public evidence ingestion from personal decision authority.

### Recommended Architecture: Dedicated Narrow Evidence-Ingestion MCP Connection
If approved in a future stage, expose a **third, strictly scoped MCP service** (`jobwatch-agent-evidence.service`) over its own isolated Cloudflare Tunnel (`jobwatch-tunnel-evidence.service`):
1. **Database identity:** Connects exclusively as `jobwatch_agent_evidence` (`NOINHERIT`, `CONNECTION LIMIT 5`, zero direct table write privileges, `EXECUTE` only on `submit_agent_observation` and `submit_agent_vacancy`).
2. **Exposed tools (exactly two, narrow, typed):**
   - `submit_public_observation` (`ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)`) — accepts bounded public HTTPS URLs, `kind`, `claim_summary`, `observed_wording`, optional `candidate_deadline_date`, target keys, and `idempotency_key`. Never accepts screening verdicts or owner-table fields.
   - `submit_public_vacancy` (same annotations) — records an externally discovered public vacancy into `discovery_candidates` marked `unscreened-agent-submission` + linked `agent_evidence_observations` row.
   - Optional bounded batch variant (`submit_public_observations_batch`, max 5 items) only if per-item confirmation friction proves excessive in practice.
3. **Client confirmation requirements (never bypassed):**
   - Because both tools declare `readOnlyHint=False`, ChatGPT requires explicit user confirmation before each write call. **Never disguise write tools as read-only or cache blanket approval.**
   - UX trade-off: Confirming 1–3 high-value submissions per research session (e.g., an external Workday/custom-ATS vacancy or an employer-confirmed closing date) is low-friction and keeps the human visibly in the loop on what external URLs enter the corroboration queue.
4. **Auth, isolation, and operational footprint on `t4g.small`:**
   - Independent tunnel token (`/etc/jobwatch/tunnel-evidence.env`, `0600 root:root`), dedicated Unix socket (`/run/jobwatch-agent-evidence/mcp.sock`), systemd `MemoryMax=96M`, `ProtectSystem=strict`, separate per-login row in `agent_evidence_budgets` (e.g., `hourly_limit = 20`, `daily_limit = 60` for the remote login, distinct from local operator CLI).
   - **Corroboration remains decoupled & asynchronous:** The remote MCP server performs **zero outbound HTTP fetches** (`RestrictAddressFamilies=AF_UNIX`). A bounded systemd timer for `jobwatch-corroborator.service` (e.g., every 30 min, `--limit 10`, under the verified `nftables` UID 984 jail) processes queued submissions independently.
   - **Resource & AWS cost:** ~25–35 MiB RSS for the MCP process + ~20 MiB for the tunnel daemon (`~55 MiB` total out of `1,109 MiB` currently available RAM); **$0 incremental AWS cost**.
   - **Revocation / rollback:** Stopping `jobwatch-tunnel-evidence.service` and `jobwatch-agent-evidence.service` (or `ALTER ROLE jobwatch_agent_evidence NOLOGIN`) revokes remote ingestion immediately without affecting Research MCP, Owner MCP, collection, calendar publication, or backups.

### Measurable Acceptance Criteria (Instead of an Arbitrary Calendar Delay)
Promote remote ingestion to a reviewed implementation/deployment **only when all criteria below are satisfied**:
1. **Corroborator extraction quality:** Fix HTML `<script>`/`<style>` stripping and `<a href>` link extraction (Section 8.4) and verify ≥ 90% accurate corroboration on a benchmark of ≥ 10 real public UK employer careers/posting URLs across Ashby, Greenhouse, Lever, Workable, and custom sites, with **0 false `claim_corroborated`** outcomes.
2. **Isolation & security parity:** Zero regressions on all kernel `nftables` raw-socket tests (8/8 blocked, 1/1 HTTPS 443 allowed), OS secret denial, and negative DB privilege tests, plus a dedicated remote-login budget test verifying hard transactional refusal above quota.
3. **Corroboration backlog & alerting:** Add CloudWatch health/backlog monitoring for queued uncorroborated observations and corroborator failures so stuck or failing fetches cannot accumulate silently.
4. **End-to-end backup & restore parity:** Confirm `database_backup.restore()` passes `exact_rows_schema_and_sequences = true` with the remote login and budget rows present.
5. **Owner approval of the separate ChatGPT connector** after reviewing tool schemas and confirmation prompts.

---

## 7. Initial Empirical Agent Evaluation (7 Realistic Tasks)

Executed against live production using the deployed Research MCP runtime (`/opt/jobwatch-agent-runtime/bin/python`, `ReadTools(shared=True)` as `jobwatch_agent_readonly`) and read-only Owner MCP dry-run (`OwnerTools` as `jobwatch_agent_owner`, **zero commits**):

| # | Evaluation Task | Measured Production Evidence & Latency | Observed Friction / Root Cause |
|---|---|---|---|
| **1** | **Discover newly advertised relevant vacancies** | `run_report(daily)`: `2,027.4 ms`; `search(fts_symbol)`: `93.8 ms` (10 hits); `search(bm25_prefiltered)`: `691.2 ms` (10 hits). Live corpus: **2,478** listed postings (**30** `priority`, **194** `review`, **2,254** `excluded`); **172** relevant added in last 7 days (including newly activated Anima, Evervault, Faculty, Ocado Group). Discovery view: **30** candidates (`3` linked to native, `27` unlinked; `8` complete, `22` truncated). | Fast and reliable retrieval; newly activated boards appear immediately. However, discovery candidates (`30` rows) are not searched by `search()` (which indexes `posting_versions` only), so agents must query `research_discovery_evidence` separately. |
| **2** | **Find vacancies absent from DB** | Coverage: **28/28** healthy monitored sources; **13,749** `discovered_boards`, of which only **100** (`0.73%`) have been probed (**13,649 / 99.27% unprobed**), `38` with UK vacancies, `6` with relevant vacancies. Across 16 prominent UK grad tech/AI/quant employers: **3/16** monitored (`Faculty AI`, `Monzo`, `Palantir`), **9/16** already in `discovered_boards` yet **0 probes** (`Wayve`, `Synthesia`, `Quantexa`, `Mistral AI`, `Improbable`, `Revolut`, `Wise`, `Bloomberg`, `Citadel`), and **3/16** absent from both (`DeepMind / Google`, `Hudson River Trading`, `Robin AI`; `Arm` hits substring false positives in `%arm%`). | Two distinct root causes: (a) daily 40-board rotation leaves 99.3% of directory boards unprobed, including high-signal UK AI employers already in `discovered_boards`; (b) Workday/custom-ATS employers never appear in ATS directories, making agent-contributed public evidence genuinely necessary. |
| **3** | **Investigate eligibility using retained evidence** | Native relevant listed (`224` roles): **224/224** (`100%`) have full text (avg `7,466` chars). Explicit mention rates: degree `135/224` (`60.3%`), grad year/cohort `107/224` (`47.8%`), visa/right-to-work `38/224` (`17.0%`), deadline/rolling `29/224` (`12.9%`), security clearance/citizenship `27/224` (`12.1%`). **Discovery candidates:** `complete` (`8` rows, avg `5,160` chars) → `5/8` (`62.5%`) visa/RTW, `3/8` (`37.5%`) degree, `1/8` (`12.5%`) deadline/rolling; **`truncated` (`22` legacy rows, avg `2,500` chars) → `0/22` (`0%`) visa/RTW, `0/22` (`0%`) degree, `0/22` (`0%`) deadline/rolling!** | **Major empirical finding:** Eligibility clauses almost always sit beyond character 2,500; legacy truncation destroyed 100% of visa/degree/deadline recall on the 22 unrefreshed discovery rows. On native postings, employers themselves omit visa sponsorship in 83% of ads (genuine external ambiguity, not a DB defect). |
| **4** | **Compare current vs historical descriptions** | Native (`posting_versions`, `245.5 ms`): **2,599** multi-version postings, **245** `changed` events, **192** description-text diffs, **22** title diffs, **7** location diffs. Discovery (`discovery_candidate_snapshots`): all **6** healed candidates cleanly expose both legacy `truncated` (~2,500 chars) and upgraded `complete` (4,538–7,580 chars) snapshots. | Full historical text is preserved and inspectable. However, agents must write window/subquery SQL to diff `posting_versions` (no pre-joined `previous_version` view), and inspecting two long `description_text` blobs in one `sql_query` can approach the `128 KiB` response cap if not sliced. |
| **5** | **Resolve / expose deadline uncertainty** | `run_report(calendar)`: `2,008.6 ms`. Across **2,478** listed postings (**224** relevant): **0** currently have a structured/dated deadline claim! **96** listed postings (**25** relevant) have **301** active `feed_observation` claims with `structured = false, value = NULL` (e.g., Cambridge Consultants rolling wording). Only `2` non-listed historical postings have `structured = true` feed claims and `2` have `independent_verification` claims. `calendar_selections` = `0`. | **Major empirical finding:** (a) Almost no ATS feed emits structured ISO closing dates; external employer pages/FAQs are the primary source of actual dates. (b) Greenhouse `deadlines->>'wording'` contains raw HTML entities (`&lt;p&gt;&lt;strong&gt;...`) and duplicate historical rows per posting across versions (`posting_deadline_claims` runs in `~2.2 s`). |
| **6** | **Contribute externally discovered public evidence** | Local CLI (`jobwatch-evidence`) + isolated `jobwatch-corroborator.service` succeeded cleanly on both live examples; Research MCP readback took `19.6 ms` (`research_agent_evidence`) and `9.3 ms` (`research_agent_corroborations`), and `sql_schema()` returned `83,640 B` (`130.7 ms`). | ChatGPT Research sessions currently cannot submit externally discovered vacancies or deadlines directly (read-only MCP); they must hand off evidence for manual local CLI invocation until Section 6 is approved. |
| **7** | **Select deadlines via authorized owner workflow (dry-run only)** | Tested `OwnerTools.call('prepare_owner_change', {'recipe': 'select_calendar_deadlines', ...})` on `greenhouse:ocadogroup:5036245101` (**never committed**): (a) without deadline → cleanly returned `blocked: [{"reasons": ["missing"]}]`, `feed_effect: {before: 0, after: 0}`; (b) with inline `verify_posting_deadline` evidence → cleanly previewed `change: "add"`, `deadline: "2026-12-15"`, `feed_effect: {before: 0, after: 1, added: 1}`, `expires_in_seconds: 600`, explicit user-confirmation notice. Confirmed `calendar_selections: 0 -> 0` and `verifications: 24 -> 24`. | Dry-run rollback simulation works accurately and enforces explicit inclusion + missing-deadline blocking. Observed a minor upstream URL defect in the preview: Ocado's custom Greenhouse URL contained a duplicated query parameter (`?gh_jid=5036245101&gh_jid=5036245101`). |

---

## 8. Concrete Remaining Limitations (Prioritized by Empirical Impact)

1. **Discovery directory probing backlog (`13,649 / 13,749` = `99.3%` unprobed) & unsupported ATS coverage:** Nine of sixteen benchmark UK graduate tech/AI employers (including Wayve, Synthesia, Quantexa, Mistral AI, Improbable, Revolut) are already in `discovered_boards` with `0` probes; others (DeepMind/Google, Arm, HRT, Bloomberg) use Workday or custom ATS portals outside the 4 public board adapters.
2. **Legacy discovery truncation still affects `22 / 30` candidates:** Until those 22 candidates are re-observed by scheduled discovery probes, their retained descriptions remain capped at 2,500 characters — empirically measured at **0%** visa/degree/deadline mention recall vs **62.5% / 37.5% / 12.5%** on complete discovery candidates.
3. **Structured deadline scarcity & Greenhouse deadline wording hygiene:** `0 / 2,478` listed postings currently carry a structured/dated feed deadline, while `301` unstructured `feed_observation` rows in `posting_deadline_claims` include raw HTML-escaped markup (`&lt;p&gt;&lt;strong&gt;...`) and repeated wording across versions, also making `posting_deadline_claims` aggregation queries take ~2.2 seconds.
4. **Corroborator HTML text extraction (`<script>`/`<style>` & `<a href>` attributes):** Live corroboration of `https://www.animahealth.com/company/careers` revealed that `_html_to_text()` retains inline `<script>`/`<style>` text in `evidence_excerpt` and does not inspect `<a href>` attributes when checking `source_identity`/`official_source_link` claims. Fixing this is Criterion #1 before remote ingestion.
5. **Duplicate `gh_jid` query parameter on custom Greenhouse URLs:** Observed live on `greenhouse:ocadogroup:5036245101` (`https://careers.ocadogroup.com/jobs/details?gh_jid=5036245101&gh_jid=5036245101`), where URL normalization appended `gh_jid` onto a URL that already included it.
6. **Sanitized SQL errors (`sqlstate: 42703`) require strict `sql_schema(table=...)` discipline:** Deliberate error sanitization omits column names when an agent guesses a schema column (e.g., `sources.company` vs `employers.name`, or `posting_deadline_state` JSONB `claims`). Agents perform cleanly when inspecting `sql_schema(table=...)` first (`~12 ms`), and `search()` currently covers `posting_versions` only (not `discovery_candidates`).
7. **Local-only agent evidence ingestion & manual corroboration:** External public evidence found during ChatGPT Research sessions cannot yet be submitted remotely, and `jobwatch-corroborator.service` runs manually on demand (no scheduled timer yet) pending the acceptance criteria in Section 6.
8. **Unattended Workable-wide polling remains disabled:** Only explicitly verified employer Workable boards are monitored; broad Workable directory polling remains off per policy.
