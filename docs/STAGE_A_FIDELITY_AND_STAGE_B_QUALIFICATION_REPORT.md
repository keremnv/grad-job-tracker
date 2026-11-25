# Stage A Evidence Fidelity Deployment & Stage B Ingestion Qualification Report

**Date:** 25 November 2026  
**Deployed Stage A Commit:** `e71ba3ad9d51fa3aad16b6ad5a0cacb33a8c96aa`  
**Production Host:** AWS EC2 `i-0a21a9da641e3f22e` (`t4g.small`, `eu-north-1`, Ubuntu ARM64, PostgreSQL 17.11 + `pgvector 0.8.7`)  
**Scope Enforced:** Stage A (`0011_discovery_evidence_fidelity.sql`, discovery fidelity writers, read-only Research MCP grant delta, Ashby `publicWebsite` + bounded Lever header-prefix identity verifier) deployed to production. Stage B (`0012_agent_evidence_hardened.sql`, `jobwatch_agent_evidence`, remote ingestion, third MCP service) **NOT deployed**.

---

## 1. Stage A Production Deployment & Verification Evidence

### Artifact & Guarded Rollout Chain

| Artifact / Event | SHA-256 / Identifier | Verification |
|---|---|---|
| Application release (`app.tar.gz`) | `ce9d00acdcaddb02ff72d6e834af752027ede57c0f048ae9d847c3e7dd821287` | S3 `AES256`, VersionId `Gg.XVfNjRMHXqXZz9JgwLDHXYD.Nz58.`, packaged migrations `0001..0011` only (`0012` excluded) |
| Agent runtime (`agent.tar.gz`) | `df215559eb66526f1dd771cf49b5d539fe42c53222509a390e9ce6175a125184` | S3 `AES256`, VersionId `58RBa.7H2UQhpULmzMtJQQnucATF09VI`, `model_inference=false` |
| Pre-deploy streaming S3 backup | `a5d8e3dc30e92c31bb0e6791a6ca2ae2ce31c684c66e8f6b0715c7c78ac06970` | `38,014,128` B (`36.25 MiB`), `s3_checksum_and_version_confirmed=true` |
| Migration `0011` (`jobwatch-db-migrate`) | `Applied 0011_discovery_evidence_fidelity.sql` | Idempotent repeat reported `Database schema is up to date`; `schema_migrations`: `count=11, max=11` |
| Read-only grant delta (`discovery_fidelity_access.sql`) | `GRANT SELECT` on `discovery_candidate_snapshots`, `research_discovery_evidence` | Applied for `jobwatch_agent_readonly`, `jobwatch_agent_owner`, `jobwatch_calendar_reader` |
| Stage B absence verification on production | `has_agent_evidence_role=false`, `has_agent_evidence_table=false` | Confirmed zero Stage B roles or tables exist on production |
| Explicit calendar policy preservation | `/etc/jobwatch/calendar-deadlines.sql` SHA-256 `92792a74f9c7418ee9378949cf163e5aaf080a03b7beb81c270d16cf4871212b` | Unchanged (`0` `calendar_selections`, `0` auto-included events) |
| Research MCP read & refusal check (`jobwatch_agent_readonly`) | `research_discovery_evidence` (`ok=True`, `30` rows), `discovery_candidate_snapshots` (`ok=True`, `30` rows) | `DELETE FROM jobwatch.discovery_candidate_snapshots` refused (`ok=False`) |
| Post-rollout streaming S3 backup | `c832614ac7468ccab3c087b1e0ed531ad5086a45425a422db500007021749502` | `backups/2026/11/25/bdd557c4633b4893b293a59eb2288268.dump` (`38,411,538` B = `36.63 MiB`, VersionId `j2vRawmqFAj_2So3_pvqjfuV7QzHillX`), manifest VersionId `DmXfJg.dhyuvl3CRmUM7_kSA47IL75ka`, `18.01 s`, peak RSS `120.9 MiB` |
| Post-rollout off-host restore verification | Disposable local PostgreSQL 17 (`--network none`) | `pg_restore --exit-on-error --single-transaction` completed in `19.49 s`; **17/17 fingerprinted tables** (including `discovery_candidates=30`, `discovery_candidate_snapshots=30`, `discovered_boards=13,749`) and **5/5 sequences** (`584`, `98`, `247`, `3729`, `37`) matched manifest exactly; read-only snapshot `SELECT` allowed and `DELETE` refused |
| Host & CloudWatch status | `0` failed systemd units, `1,110 MiB` available RAM (`0` swap), **6/6 CloudWatch alarms `OK`** | `backup-health`, `calendar-health`, `cpu-credit-balance`, `host-status`, `mcp-health`, `operation-failure` all `OK` |

### 0011 Correctness & Constraint Verification

All 317 offline unit tests and all 219 disposable PostgreSQL 17 integration tests passed prior to release, including explicit tests for:

- **PostgreSQL `CHECK` NULL semantics (`3VL UNKNOWN` trap):** inserting `evidence_completeness='complete'` with `complete_description_sha256 IS NULL` raises `CheckViolation` on BOTH `discovery_candidates` and `discovery_candidate_snapshots`.
- **Excerpt hash fidelity:** non-empty `description_excerpt` with `NULL` or mismatched `excerpt_sha256`, and empty `description_excerpt` with non-null `excerpt_sha256`, are rejected by `CHECK`.
- **Retention-policy invariants:** `excerpt_only_terms_restricted` forbids `complete` and non-null `description_text`; `metadata_only` requires `unavailable`, `NULL` `description_text`, and empty `description_excerpt`.
- **Legacy backfill honesty:** all 28 pre-0011 production rows migrated cleanly to `evidence_completeness='truncated'`, `description_text=NULL`, `complete_description_sha256=NULL`, valid `excerpt_sha256`, and baseline snapshots tagged `legacy_pre_0011_snapshot=true` (zero fabricated `complete` claims).
- **Content-immutable snapshot history vs observation counts:** runtime roles (`jobwatch_collector`, `jobwatch_owner`) hold `SELECT, INSERT` and column-level `UPDATE (last_observed_at, observation_count)` ONLY — `UPDATE description_text` and `DELETE` raise `InsufficientPrivilege`.
- **Single-version consistency in `research_discovery_evidence`:** every `effective_*` column (`effective_evidence_source`, `effective_version_id`, `effective_completeness`, `effective_retention_policy`, `effective_description`, hashes, timestamps, `effective_provenance`) switches atomically on the same source version (`posting_versions` when linked, current snapshot otherwise).

---

## 2. Actual PostgreSQL TOAST Storage, Backup & Retrieval Costs

### A. Disposable PostgreSQL 17 TOAST & Duplication Benchmark (500 Realistic ~6.56 KiB Descriptions)

Testing 500 varied English technical descriptions (`3,281,387` B raw, mean `6,562.8` B) in real PostgreSQL 17.11 immediately uncovered a **4.9× MVCC TOAST bloat trap** on repeated unchanged `UPDATE`s — which we fixed in `src/jobwatch/postgres/discovery.py` by omitting unchanged TOAST/search columns on re-observation so PostgreSQL reuses existing TOAST OID pointers:

| Stage (500 candidates, 3.13 MiB raw text) | `discovery_candidates` Heap / Index / TOAST / Total | `discovery_candidate_snapshots` Heap / Index / TOAST / Total | Key Observation |
|---|---|---|---|
| 1. Initial 500 candidates (1 snapshot each) | `1.30 / 2.54 / 2.85 / 6.73 MiB` | `0.97 / 0.29 / 1.04 / 2.33 MiB` | Real PG TOAST compresses `6,528` B -> `1,795` B (`3.64×` ratio; `2.13 KiB/row` incl. TOAST index) |
| 2a. After 2,500 unchanged observations (**before** TOAST-reuse fix) | `2.36 / 4.86 / 14.01 / 21.27 MiB` | `4.77 / 0.45 / 1.04 / 6.30 MiB` | Unconditional `SET description_text=%s` caused **+391% TOAST bloat** (`2.85 -> 14.01 MiB`) |
| 2b. After 2,500 unchanged observations (**after** TOAST-reuse fix) | `2.36 / 4.86 / 2.85 / 10.10 MiB` | `4.77 / 0.45 / 1.04 / 6.30 MiB` | **0 bytes of new TOAST written** across 2,000 re-observations on BOTH tables |
| 3. After 20% wording edits (100 new snapshots, 600 total) | — | `+200 KiB` TOAST (`1.04 -> 1.23 MiB`) | Scales linearly with distinct edits (`~2.0 KiB` TOAST per distinct version); omitting `raw_evidence_gz` saves a redundant 3rd copy (`673 KiB` gzip for 500 rows) |

### B. Live Production Storage & Latency Measurements (`i-0a21a9da641e3f22e`, 28 Monitored Boards, 2,475 Listed Postings)

| Relation / Operation | Measured Production Value |
|---|---|
| `posting_versions` (`6,578` rows) | `197.86 MiB` total (`5.66 MiB` heap, `25.16 MiB` indexes, `167.00 MiB` TOAST) |
| `discovered_boards` (`13,749` rows) | `8.55 MiB` total (`6.99 MiB` heap, `1.52 MiB` indexes, `8 KiB` TOAST) |
| `postings` (`2,475` listed) | `4.40 MiB` total (`3.04 MiB` heap, `1.32 MiB` indexes, `8 KiB` TOAST) |
| `discovery_candidates` (`30` rows, `8` complete avg `5,132` chars, max `7,580` chars) | `928 KiB` total (`120 KiB` heap, `536 KiB` indexes, `240 KiB` TOAST) |
| `discovery_candidate_snapshots` (`30` rows, `36` observations) | `232 KiB` total (`24 KiB` heap, `64 KiB` indexes, `112 KiB` TOAST) |
| Total `jobwatch` database size | `225.94 MiB` (`236,918,451` B) |
| Streaming S3 backup (`database_backup.py`, 17 tables fingerprinted) | `36.63 MiB` archive, `18.01 s` elapsed, `120.9 MiB` peak RSS |
| Off-host PG17 restore (`--network none`, `--single-transaction`) | `19.49 s` elapsed |
| Research MCP `research_discovery_evidence` SQL (`30` rows) | `21.8 ms` warm (`121.1 ms` cold) |
| Research MCP `search` (`fts_symbol`) | `140.0 ms` |
| Research MCP `search` (`bm25_prefiltered`) | `737.3 ms` |
| Research MCP `run_report` (`requirements` / `daily`) | `1,809.3 ms` / `1,840.8 ms` |

---

## 3. Discovery & Automatic Activation Results

### Separate Funnel Counts (Post-Stage-A Live State)

| Metric | Count | Detail |
|---|---|---|
| Total directory boards tracked | `13,749` | `100` probed (`84` `active_non_uk`, `6` `active_uk_relevant`, `10` `empty`), `13,649` `unprobed` |
| Unique relevant employers (in `discovery_candidates` / active UK boards) | `7` | `Anima`, `Evervault`, `3E`, `Alpha FMC - UK`, `MARGO`, `WisdomTree UK`, plus legacy `10x Banking` (`ashby:10xteam`, now 0 relevant) |
| Unique discovered candidate vacancies (`discovery_candidates`) | `30` | `0` priority, `30` review (`8` complete in `discovery_candidates`, `2` newly discovered complete in both candidate + snapshot, `22` legacy truncated) |
| Verified strict bidirectional identities (`verified_official_link`) | `2` | `ashby:anima` (Anima Health) and `ashby:evervault` (Evervault) |
| Successful automatic activations (`sources.status='monitored'`) | `2` | `source_id=36` (`ashby:evervault`, 6 roles, 2 review) and `source_id=37` (`ashby:anima`, 11 roles, 1 review) — fleet grew `26 -> 28` monitored boards (`2,475` listed postings) |

### Newly Auto-Activated Boards & Collected Relevant Vacancies

1. **Anima Health (`ashby:anima`, `source_id=37`, 11 listed roles, 10 UK):**
   - Bidirectional identity evidence: Ashby `window.__appData` `"publicWebsite": "https://www.animahealth.com"` → `https://www.animahealth.com/company/careers` statically references `ashbyhq.com/anima`.
   - Live validation: `run_id=550aa6c7-32fb-4426-8538-5eb7473a4b2f`, `absence_safe=true`, `11` vacancies.
   - Relevant collected role: `ashby:anima:785441b7-54ea-4840-bf27-b5e8bf71430c` — **Product Engineer (all levels)** (`Remote, London`, `review`, `software`).
2. **Evervault (`ashby:evervault`, `source_id=36`, 6 listed roles, 6 UK — discovered during the 20-board prioritized probe):**
   - Bidirectional identity evidence: Ashby `window.__appData` `"publicWebsite": "https://evervault.com"` → `https://evervault.com/careers` statically references posting ID `da95abe6-9799-4684-bc0e-e0f81225b8e6`.
   - Live validation: `run_id=71b16186-c4ca-41a4-869f-ecec59491dfd`, `absence_safe=true`, `6` vacancies.
   - Relevant collected roles:
     - `ashby:evervault:101bdb4d-0dd3-4c42-b904-2253238cae27` — **Product Engineer, Experience** (`London`, `review`, `software`, complete `3,444` chars retained).
     - `ashby:evervault:da95abe6-9799-4684-bc0e-e0f81225b8e6` — **Product Engineer, Cards** (`London`, `review`, `software`, complete `3,524` chars retained).

### Empirical Identity-Verification Failure Analysis (Across All 7 Checked Active UK Boards)

No verification rule was weakened. Across the 5 boards that remained conservatively deferred:

1. **Hosted ATS board configured without any outbound employer website link (`3/5`):** `greenhouse:alphafmc`, `lever:margo-group`, `lever:wisdomtree-2` (`unverified_js_or_blocked`). On `lever:margo-group`, the bounded 512 KiB prefix read (`allow_truncate=True`) eliminated the previous `-1` oversized-body failure and confirmed the hosted Lever header genuinely omits an external website link.
2. **Official site uses JS rendering or a non-`/careers` path (`2/5`):** `ashby:3e` (`https://www.3eco.com/`) and `ashby:10xteam` (`https://www.10x.team`) both extracted `publicWebsite` cleanly via the new Ashby JSON extractor, then stopped conservatively at `hosted_link_only` because static HTML at `/` and `/careers` did not corroborate the ATS board.

---

## 4. Final Ingestion Trust-Boundary Review (Stage B — Isolated Only, NOT Deployed)

Every remaining trust-boundary gap identified before Stage B approval has been closed inside `sql/postgresql/proposals/0012_agent_evidence_hardened.sql`, `sql/postgresql/proposals/agent_evidence_roles.sql`, and `src/jobwatch/postgres/agent_evidence.py`:

| Trust-Boundary Requirement | Enforcement Mechanism (Inside Trusted SQL / Isolated Worker) |
|---|---|
| No caller-supplied hashes | `submit_agent_observation` and `submit_agent_vacancy` accept **zero hash parameters**; `payload_sha256`, `submission_sha256`, `complete_description_sha256`, `excerpt_sha256`, and `evidence_sha256` are computed strictly inside SQL via `pg_catalog.sha256(...)`. |
| Authenticated contributor-scoped idempotency | `UNIQUE (db_login, idempotency_key)` + advisory lock keyed on `db_login || ':' || idempotency_key`. Distinct logins cannot collide with or probe each other's keys. |
| Candidate identity consistency & anti-contamination | `candidate_key` is derived inside SQL (never caller-supplied); hosted ATS URLs are cross-checked against `(provider, board)`; conflicting provider/board/URL claims raise `23505` (`candidate_identity_conflict`); consistent submissions matching an existing candidate record ONLY an observation and **never mutate or inject snapshots into collector-owned candidates**. |
| Untrusted screening verdicts rejected | `submit_agent_vacancy` accepts **zero screening parameters**; new agent candidates always enter with `verdict='review'`, `family='unscreened'`, `codes=ARRAY['evidence.agent_submitted_unscreened']`, `rules_version='unscreened-agent-submission'`, `screening_input_sha256=NULL` until the trusted collector runs deterministic `discovery.rescreen_candidates()`. |
| Completeness & retention invariants | Independently enforced in both SQL `SECURITY DEFINER` logic and table `CHECK` constraints (`metadata_only`, `excerpt_only_terms_restricted`, `complete` non-empty text). |
| `SECURITY DEFINER` & `pg_temp` safety | Every function is owned by non-superuser `jobwatch_migrator`, sets `search_path = pg_catalog, jobwatch, pg_temp` (`pg_temp` LAST), qualifies `pg_catalog.` calls, revokes `PUBLIC`, and grants `jobwatch_agent_evidence` zero raw table `INSERT`/`UPDATE`/`DELETE`/`TRUNCATE` privileges (`NOINHERIT`, `CONNECTION LIMIT 5`, zero role memberships). |
| Target-vacancy binding in corroboration | `corroborate_observation()` computes `target_binding_status` (`bound_exact_url`, `bound_host_and_identity`, `unbound_target`, `not_applicable`) and stores `evidence_completeness`, `evidence_excerpt`, and `provenance`. SQL `CHECK` forbids `overall_state='claim_corroborated'` whenever `target_binding_status='unbound_target'`. |
| Network isolation (beyond app SSRF checks) | Specified in [corroborator-isolation.md](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/infra/aws/proposals/corroborator-isolation.md): dedicated `jobwatch-corroborator` systemd sandbox + kernel `nftables` per-UID egress block of `127.0.0.0/8`, RFC1918, CGNAT, and `169.254.0.0/16` (IMDS) + verified EC2 IMDSv2 (`HttpTokens=required`, `HttpPutResponseHopLimit=1`). |

---

## 5. Direct SQL Security & Invariant Tests

Verified in disposable PostgreSQL 17 via [test_agent_evidence_pg.py](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/tests/postgres/test_agent_evidence_pg.py) and [test_agent_evidence_security.py](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/tests/test_agent_evidence_security.py) (plus [test_discovery_lifecycle.py](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/tests/postgres/test_discovery_lifecycle.py) for `0011`):

1. **Live `pg_temp` shadowing attack:** Granted `TEMP` to `jobwatch_agent_evidence`, created hostile `pg_temp.sha256()`, `pg_temp.clock_timestamp()`, and `pg_temp.hashtextextended()`, then called `jobwatch.submit_agent_observation` directly via SQL — verified `pg_temp` functions were completely bypassed.
2. **PostgreSQL catalog grant audit:** Verified all 5 `0012` functions are owned by `jobwatch_migrator`, have `search_path=pg_catalog, jobwatch, pg_temp`, have zero `PUBLIC` `EXECUTE` privileges, and `jobwatch_agent_evidence` has zero non-`SELECT` grants across `information_schema.role_table_grants` and zero `pg_auth_members` memberships.
3. **Direct SQL hostile inputs:** Verified direct SQL calls reject private/IMDS/loopback/non-443/credential URLs (`InvalidParameterValue`), invalid calendar dates `2026-02-30` (`DatetimeFieldOverflow`), idempotency tampering (`UniqueViolation`), mismatched Greenhouse board URLs (`InvalidParameterValue`), conflicting collector candidate identities (`UniqueViolation`), and retention-policy violations (`InvalidParameterValue`).
4. **Cross-login idempotency isolation:** Verified `jobwatch_owner` and `jobwatch_agent_evidence` independently record distinct submissions under the identical `idempotency_key` (`shared-idem-001`) without collision.
5. **Anti-contamination & unscreened neutralization:** Verified direct SQL submission for an existing collector candidate (`greenhouse:betauk:77`) leaves its title, description, and snapshot count untouched; new vacancies enter as `unscreened-agent-submission` and upgrade to `priority` (`full`) only after trusted collector `rescreen_candidates()`.
6. **Concurrent budget enforcement:** Verified `5` concurrent connections submitting `6` observations under distinct spoofed `logical_contributor`/`claimed_actor` strings allow strictly `2` and block `4` under `pg_advisory_xact_lock` keyed on `db_login`.
7. **Unbound-page corroboration refusal:** Verified an unrelated page (`https://unrelated-grad-blog.co.uk/tips`) containing the verbatim closing-date quote caps at `target_binding_status='unbound_target'`, `overall_state='wording_matched'`, `claim_status='unverified'` (and direct SQL `INSERT` of `claim_corroborated` + `unbound_target` raises `CheckViolation`), while an official employer page with matching role title achieves `bound_host_and_identity` + `claim_corroborated` with `0` writes to `verifications` or `calendar_selections`.

---

## 6. Architectural Consolidation Review

Reviewing observation, evidence-version, and identity boundaries across all three subsystems:

| Subsystem | Identity Relation | Distinct Evidence Versions | Observation Occurrences | Authority Boundary |
|---|---|---|---|---|
| **Native collection** | `postings` (`posting_key`) | `posting_versions` (immutable, `evidence_sha256` + `normalizer_version`) | `collection_runs` + `postings.last_seen_at` + `posting_events` | Authoritative monitored-board observations (`jobwatch_collector`) |
| **Discovery (0011)** | `discovery_candidates` (`candidate_key`, `current_snapshot_id`) & `discovered_boards` (`board_key`) | `discovery_candidate_snapshots` (content-immutable, `(candidate_key, evidence_sha256)`) | `discovery_candidates.observation_count` + `discovery_candidate_snapshots.first_observed_at/last_observed_at/observation_count` | Public unactivated observations (`jobwatch_collector`); never mutates monitored `postings` or owner tables |
| **Agent evidence (0012 proposal)** | References existing keys or creates unscreened `discovery_candidates` | Append-only `agent_evidence_observations` + `agent_evidence_corroborations` | Each observation is an explicit recorded sighting; corroboration history is append-only | Untrusted claims via `SECURITY DEFINER` (`jobwatch_agent_evidence`); corroborated only by isolated worker; promoted only explicitly |

**Concrete coherence findings resolved & noted:**

- **Resolved before deploy:** Fixed `research_discovery_evidence` so `effective_*` columns never mix native `posting_versions` text with candidate hashes/timestamps/provenance; fixed PostgreSQL `CHECK` 3VL `NULL` semantics; fixed MVCC TOAST bloat on unchanged candidate re-observations.
- **Concrete live inconsistency identified (non-destructive, documented below):** On the 6 pre-0011 legacy candidates re-probed after `0011`, `discovery_candidates` upgraded cleanly to `complete`, while `discovery_candidate_snapshots` hit `ON CONFLICT (candidate_key, evidence_sha256)` against the `legacy_pre_0011_snapshot=true` truncated backfill row (because `evidence_sha256` hashes the unchanged `raw_item`), leaving `candidate_completeness='complete'` while `effective_completeness='truncated'` on those 6 pre-0011 rows (whereas all post-0011 new candidates like Evervault are `complete` on both). Because `discovery_candidate_snapshots` intentionally prohibits runtime updates to `description_text`, the cleanest fix in a future minor patch is including completeness in the snapshot's version key or computing `evidence_sha256` for legacy backfills with a distinct legacy namespace so a first post-0011 complete capture appends cleanly as snapshot #2 alongside the preserved legacy truncated snapshot #1.
- **No universal schema refactor needed:** Native `posting_versions`, `discovery_candidate_snapshots`, and `agent_evidence_observations` have genuinely distinct trust and lifecycle semantics; ordinary SQL views (`research_discovery_evidence`, `research_agent_evidence`) keep cross-subsystem queries simple without blurring authority boundaries.

---

## 7. Proposed Local-Only Stage B Deployment & Example Practical Workflow

Full specification is documented in [corroborator-isolation.md](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/infra/aws/proposals/corroborator-isolation.md):

- **No third MCP service** and **no remote ingestion** until local utility is proven over 2–4 weeks.
- **Minimal local-only rollout (pending separate GO):** promote `0012`, provision `jobwatch_agent_evidence` strictly over local Unix peer socket/SSM CLI, deploy the `nftables`-jailed `jobwatch-corroborator` oneshot unit, and use the 3-path non-silo integration (`new_vacancy` → collector `rescreen_candidates()`, `source_identity` → `discovered_boards` auto-activation pipeline, `deadline_wording` → `research_agent_evidence` conflict view → explicit owner `verifications`/`calendar_selections`).

---

## 8. Remaining Concrete Limitations

1. **Legacy pre-0011 snapshot conflict on unchanged re-observations (`6` live rows):** Re-probing a pre-0011 truncated candidate whose raw ATS item has not changed upgrades `discovery_candidates` to `complete`, but `ON CONFLICT (candidate_key, evidence_sha256)` re-increments the legacy truncated snapshot instead of appending a new complete snapshot. Newly discovered candidates (e.g., Evervault) are unaffected and store complete snapshots immediately.
2. **Post-activation auto-linking of existing discovery candidates (`3` live rows):** `ashby:anima` and `ashby:evervault` were collected into `postings` after their discovery probe ran; their `discovery_candidates` rows (`linked_posting_key IS NULL`) will auto-link to `native_posting_version` on their next discovery re-probe (or via a lightweight post-collection linking pass).
3. **Conservative identity verification ceiling (`5/7` active UK boards unverified):** Hosted boards lacking outbound employer website links (`greenhouse:alphafmc`, `lever:margo-group`, `lever:wisdomtree-2`) and JS-rendered employer careers portals (`ashby:3e`, `ashby:10xteam`) remain deferred unless reviewed manually.
4. **Stage B (`0012`) remains undeployed:** Local-only ingestion and the `nftables`-isolated corroboration worker await explicit owner approval after review of this qualification report.
5. **Unattended Workable-wide discovery remains disabled** per existing policy.
