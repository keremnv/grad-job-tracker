# Graduate Job Watch — Coverage Improvement, Extraction Fixes & Remote Agent Integration Qualification

**Date:** 2026-11-25 (UTC)  
**Deployed Commit:** `62f740219ea78fd56cbb8a908d6d16f33d6b4c34` (`origin/main`)  
**Production Host:** EC2 `i-0a21a9da641e3f22e` (`t4g.small`, `eu-north-1`, PostgreSQL 17 + `pgvector 0.8.7`)  
**Authorization Scope:** Deployed approved HTML/deadline hygiene, Ocado URL canonicalization, corroborator HTML/link extraction, migration `0013`, controlled legacy evidence recovery, and targeted benchmark employer probes using existing production budgets. **Increased discovery budgets, new recurring external adapters, recurring corroboration schedules, and remote Evidence MCP exposure remain isolated / review-only and are NOT enabled in production.** Unattended Workable-wide polling remains disabled; all owner records remain untouched.

---

## 1. Discovery Throughput & Backlog Analysis

### Current Backlog & Measured Provider Characteristics
Production holds **13,749** `discovered_boards` (`7,363` Greenhouse, `4,083` Ashby, `2,303` Lever), of which **13,643** (`99.23%`) remain unprobed after this session's targeted probes (`106` probed total).

Direct read-only measurements against upstream ATS APIs and production `jobwatch-discover-probe.service` runs establish exact sustainable capabilities:

| Provider | Backlog | `robots.txt` / Usage Rule | Lightweight Pass | Conditional `304` (`If-None-Match`) | `HEAD` Support | Measured Payload & Latency |
|---|---|---|---|---|---|---|
| **Greenhouse** | `7,317` unprobed | `Allow: /` (`Disallow: /embed/`); no `Crawl-delay` | **Yes:** `/v1/boards/{b}/jobs` (`18.0 KiB`, `192 ms`) vs `?content=true` (`655.0 KiB`, `961 ms`) — **36.3x smaller** | **Yes:** `304` (`140 ms`, `0 B`) | **Yes:** `200` (`479 ms`, `0 B`) | 1 req non-UK, 2 reqs UK; ~`0.25–0.5s` polite spacing |
| **Ashby** | `4,039` unprobed | Public Cloudflare-cached posting API (`cache-control: public, max-age=60`) | Full JSON only (omitting `?includeCompensation=true` saves minor metadata) | **Yes:** `304` (`55.8 ms`, `0 B`, `cf-cache-status: HIT`) | **Yes:** `200` (`92.6 ms`, `0 B`) | ~`150 KiB` avg (`60–393 ms`); ~`0.25–0.5s` polite spacing |
| **Lever** | `2,287` unprobed | **`Crawl-delay: 1`** on both `api.lever.co` and `api.eu.lever.co` | `limit=100` first page; `404` on global triggers 2nd req to EU endpoint | **Yes:** `304` (`0 B`, still `~10s` on huge boards) | **Yes:** `200` (`1.41 s`, `0 B`) | Slowest (`1.2–6.3s`); **MUST enforce `>= 1.0s` per-provider gap** (now enforced in [directory_discovery.py](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/src/jobwatch/postgres/directory_discovery.py)) |

### Measured Production Cost per Board (Actual EC2 Runs)
- **Run 1 (`08:12 UTC`):** `40` boards → `56` reqs (`1.40 req/board`), `11.08 MiB` (`277 KiB/board`), `33 s` wall clock, **`2.365 s` CPU**.
- **Run 2 (`11:09 UTC`):** `40` boards → `48` reqs (`1.20 req/board`), `6.42 MiB` (`161 KiB/board`), `60 s` wall clock, **`1.932 s` CPU**.
- **Observed yield across 100 initial random/prioritized probes:** `6%` `active_uk_relevant`, `84%` `active_non_uk` (includes senior-only UK boards), `10%` `empty`, `0%` `dead`. Identity verification averages `~2.3 reqs/relevant board` (only triggered on `active_uk_relevant` boards).

### Initial Exploration Backfill vs Steady-State Freshness Benchmark (Review-Only — NOT Enabled)
Because providers are independent hosts, **provider-balanced round-robin interleaving** (`greenhouse → ashby → lever`) naturally satisfies Lever's `Crawl-delay: 1` while probing `~1.5–2.0` boards/sec combined.

| Budget Profile | Boards / Day | Est. HTTP Reqs / Day | Est. Transfer / Day | Est. CPU / Day (`t4g.small`) | Wall Clock / Day | Time to Clear `13,643` Backlog | Est. Relevant Boards Yield (~4–6%) |
|---|---|---|---|---|---|---|---|
| **Current steady-state** (active) | `40` (`24` explore / `16` prio) | `~52` | `~9 MiB` | `~2.2 s` | `~1 min` | **~568 days** (~18.7 mo) | ~1.5–2.4 / day |
| **Conservative backfill A** (proposed review) | `160` (`140` explore / `20` prio) | `~210` | `~35 MiB` | `~9 s` | `~4 min` | **~97 days** (~3.2 mo) | ~6–9 / day |
| **Moderate backfill B** (recommended for review) | `300` (`270` explore / `30` prio, 3×100 split runs) | `~390` | `~66 MiB` | `~17 s` | `~7.5 min` | **~50 days** (~7 weeks) | ~11–18 / day |
| **Aggressive backfill C** (upper bound) | `600` (`540` explore / `60` prio, 6×100 split runs) | `~780` | `~132 MiB` | `~34 s` | `~15 min` | **~25 days** | ~22–36 / day |

- **Recommended two-phase design (subject to separate approval):**
  1. **Bounded initial exploration backfill (`Profile B`, ~300 boards/day in three 100-board windows):** Resumable via `last_probed_at IS NULL`, immediate halt on `HTTP 429` with per-provider backoff, `<= 32 MiB` cap per window, `>= 1.0s` Lever spacing, `<= 15` identity checks per window. Clears the entire 13,643-board directory in **~7 weeks** while using `< 0.02%` daily CPU and `~2 GiB/month` transfer (well within EC2/EBS limits; PostgreSQL growth for ~500–800 relevant boards' complete descriptions is `< 25 MiB` TOAST-compressed).
  2. **Steady-state freshness maintenance (post-backfill):** Drop back to `~40–60 boards/day` + store upstream `ETag` per `discovered_boards` row so `If-None-Match` returns `304 Not Modified` (`0 B`) on unchanged boards, reserving full JSON fetches for changed/active UK boards and keeping exponential backoff (`30d → 60d`) on `active_non_uk`/`empty` boards.

---

## 2. Targeted Employer Coverage Results & Custom ATS Investigation

### Why Benchmark Employers Remained Unprobed & Minimal Targeted Mechanism
Only **26 of 13,749** directory rows (`0.19%`) matched the regex `uk_hint` (`4` Ashby, `18` Greenhouse, `4` Lever). Prominent UK headquarters without `"UK"` or `"London"` in their name (`Wayve`, `Synthesia`, `Quantexa`, `Mistral`, `Improbable`, `HRT`) had `uk_hint = false` and waited behind deterministic hash rotation.

Rather than building a task queue or orchestration subsystem, migration [0013_deadline_claims_distinct_and_targeted_probe.sql](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/src/jobwatch/postgres/migrations/0013_deadline_claims_distinct_and_targeted_probe.sql) adds **three nullable columns** on `jobwatch.discovered_boards` (`investigation_requested_at`, `investigation_requested_by`, `investigation_reason`) plus a partial index. `select_probe_batch_prioritized()` in [directory_discovery.py](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/src/jobwatch/postgres/directory_discovery.py) now places boards with either an explicit investigation request (`request_board_investigation()`) **or** an unprobed `agent_evidence_observations.board_key` reference at the head of existing priority slots (`selection_reason = 'targeted_investigation'`) — strictly bounded by existing request/byte limits and **never activating any board into `sources`**.

### Live Targeted Probe Results (6 Benchmark Ashby Boards — Existing Budgets, 0 Activations)
Executed in `8.19 s`, `18` HTTP requests, `10.28 MiB` transfer → **42 new complete UK relevant candidates** persisted with full TOAST-compressed descriptions:

| Board Key | Employer | Probe Status | Total Jobs | UK Jobs | Relevant (`review`) | Identity Verification Status & Evidence |
|---|---|---|---|---|---|---|
| `ashby:mistral.ai` | Mistral | `active_uk_relevant` | `267` | `116` | **29** | **`verified_official_link`** (`Official site references ashbyhq.com/mistral.ai at https://mistral.ai/careers/`) |
| `ashby:wayve` | Wayve | `active_uk_relevant` | `150` | `52` | **9** | `hosted_link_only` (`https://wayve.ai/` — JS-rendered careers portal; manual review required before activation) |
| `ashby:quantexa` | Quantexa | `active_uk_relevant` | `58` | `33` | **3** | `hosted_link_only` (`https://www.quantexa.com/` — JS/separate portal) |
| `ashby:improbable` | Improbable | `active_uk_relevant` | `4` | `4` | **1** | `hosted_link_only` (`https://www.improbable.io/` — JS/separate portal) |
| `ashby:synthesia` | Synthesia | `active_non_uk` | `24` | `20` | `0` | `not_checked` (all 20 UK vacancies were senior/non-graduate roles) |
| `ashby:hrt` | Hudson River Trading | `empty` | `0` | `0` | `0` | `not_checked` (`200 OK`, empty Ashby board) |

### Unsupported / Custom Recruitment Sources Investigation (Read-Only)
| Employer | Measured Technical Endpoint Behaviour | Supported Acquisition Mechanism & Limitations |
|---|---|---|
| **Google / DeepMind** | `deepmind.google/careers/` redirects to Google's proprietary `HiringCportalFrontendUi` SPA (`www.google.com/about/careers/applications/jobs/results/?q=DeepMind`; `no-cache`, no documented public JSON API). | **Agent-contributed public observations** (`submit_public_vacancy` / `submit_public_observation`). Do **not** build a brittle reverse-engineered HTML/XHR scraper. |
| **Hudson River Trading (HRT)** | `ashby:hrt` returns `200 OK` (`{"jobs":[]}`); `greenhouse:hudsonrivertrading` & `greenhouse:hrt` return `404`; `www.hudsonrivertrading.com/careers/` returns **`403 Forbidden`** to non-browser HTTP clients (WAF). | Keep `ashby:hrt` in standard Ashby discovery + **agent-contributed observations** for roles posted on `hudsonrivertrading.com/careers/` (never bypass WAF). |
| **Robin AI** | `ashby`/`greenhouse`/`lever` slugs return `404`; `apply.workable.com/api/v1/widget/accounts/robin-ai` returns `200 OK` (`48 B`, empty widget); `robinai.com` and `robinai.co.uk` returned `URLError` (domain/TLS unreachable). | No recurring adapter until active official employer domain & board are independently verified; handle via agent-contributed observations. |
| **Revolut** | `ashby`/`greenhouse`/`lever` slugs return `404`; `www.revolut.com/careers/` returns **`403 Forbidden`** (Cloudflare/WAF on in-house portal). | **Agent-contributed public observations** only (no WAF bypass or recurring scraper). |
| **Wise** | `greenhouse:wise` in `discovered_boards` is an unrelated employer (`Wise Worksite Field Sales`); Wise plc (`wise.jobs`) uses **SmartRecruiters** (`https://api.smartrecruiters.com/v1/companies/Wise/postings` returns `200 OK`, documented public JSON API with `ETag`). | Candidate for a future documented SmartRecruiters adapter (subject to separate review; **not** implemented or enabled now). |

---

## 3. Legacy Evidence Recovery Results & Same-Vacancy Paired Comparison

All **22** legacy truncated discovery candidates belonged to `ashby:10xteam` (freelance AI trainer roles originally captured before Stage A and before `screen-2026-11-25.1`). Controlled re-observation directly against `https://api.ashbyhq.com/posting-api/job-board/10xteam?includeCompensation=true`:
- **Recovered:** **22 / 22** (`100%`) were still live and upgraded cleanly to `evidence_completeness = 'complete'` (`0` disappeared/unavailable; `0` fabricated).
- **Historical preservation:** All **28** legacy `truncated` snapshots remain intact in `discovery_candidate_snapshots` (`100` snapshots total: `28 truncated`, `72 complete`). All `72 / 72` current `discovery_candidates` now hold complete descriptions (`0` truncated).
- **Deterministic rescreening:** Re-observing the 22 `10xteam` freelance AI trainer roles through `screen-2026-11-25.1` cleanly reclassified all 22 from `review` to `excluded` (`exclude.freelance_ai_trainer`).

### Methodologically Rigorous Same-Vacancy Paired Evaluation (Truncated vs Complete Snapshot of the Exact Same Vacancy)
Comparing truncated vs complete snapshots **on the exact same vacancy** corrects the confounding cohort difference noted in our previous report:

| Same-Vacancy Paired Cohort | Paired Vacancies | Avg Truncated Chars | Avg Complete Chars | Visa / RTW Mentions (Trunc → Complete) | Degree Mentions (Trunc → Complete) | Deadline / Rolling Mentions (Trunc → Complete) | Compensation Mentions (Trunc → Complete) |
|---|---|---|---|---|---|---|---|
| **`other_boards_healed`** (6 UK tech/engineering roles: `ashby:3e`, `ashby:anima`, `greenhouse:alphafmc`, `lever:margo-group` ×2, `lever:wisdomtree-2`) | **6** | `2,500` | **`5,682`** (+127.3%; range `4,538–7,580`) | **`0/6 (0%) → 3/6 (50.0%)`** | **`0/6 (0%) → 3/6 (50.0%)`** | **`0/6 (0%) → 1/6 (16.7%)`** | `6/6 → 6/6` |
| **`10xteam_recovered`** (22 freelance AI trainer roles) | **22** | `2,500` | **`4,070`** (+62.8%; range `3,759–4,401`) | `0/22 → 0/22` | `0/22 → 0/22` | `0/22 → 0/22` | `22/22 → 22/22` |

**Key takeaway:** On actual engineering vacancies (`other_boards_healed`), same-vacancy comparison proves that 2,500-char truncation wiped out **100%** of visa/RTW (`0/6 → 3/6`), degree (`0/6 → 3/6`), and deadline (`0/6 → 1/6`) evidence. On the 22 `10xteam` freelance trainer listings, complete text added `+1,570` chars on average (contract scope/deliverables) while visa/degree/deadline mentions were genuinely absent (`0/22`) in the full adverts themselves.

---

## 4. Deadline & HTML Extraction Improvements

### Prevalence of Prose Deadlines vs Structured ATS Fields (Measured on 2,482 Listed Postings)
- **Structured ATS `application_deadline` fields on listed postings:** **`0 / 2,482`** (`0%`). (Only `2` historical `not_seen` postings ever carried a structured ATS deadline field.)
- **Prose deadline evidence on current listed postings (`posting_deadline_claims_distinct`):** **`117`** distinct current prose claims across **`115`** postings:
  - **`explicit_date`:** `29` claims (e.g., Graphcore `"Application deadline: Midnight, 29th November 2026"` → `2026-11-29`; Stripe `"Applications close: 30th of November 2026"` → `2026-11-30`) — **100% precision** on inspected samples (`0` invented dates).
  - **`missing_year_date`:** `11` claims (e.g., Graphcore `"Application deadline: Midnight 29th November"`, DC Thomson `"Closing date: 9th December at 1300."`) — conservatively retained with `candidate_date = None`, **never inventing a year**.
  - **`rolling_or_no_deadline`:** `49` claims (e.g., Cambridge Consultants `"There is no deadline for this role, as we recruit on a rolling basis."`).
  - **`unstructured_warning`:** `28` claims (e.g., `"We encourage you to apply early, as we may close applications before the advertised deadline."`).

### Deployed Fixes
1. **HTML normalisation & entity hygiene ([filtering.py](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/src/jobwatch/filtering.py), [timing.py](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/src/jobwatch/timing.py)):** Double-pass `html.unescape()` (fixing Greenhouse `&amp;nbsp;` / `&lt;p&gt;`), `<script>`/`<style>`/`<noscript>`/`<template>` stripping, inline-tag removal without injecting spaces (fixing `29<sup>th</sup>` → `29th` and `<strong>Application deadline</strong>:` → `Application deadline:`), optional `of` in DMY dates (`30th of November 2026`), and 4-digit 24h time tokens (`at 1300`).
2. **Distinct claims queryability without losing provenance ([0013_deadline_claims_distinct_and_targeted_probe.sql](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/src/jobwatch/postgres/migrations/0013_deadline_claims_distinct_and_targeted_probe.sql)):** `jobwatch.posting_deadline_claims` and every historical `claim_id` remain 100% unchanged (preserving `verifications.deadline_supersedes`), while the new view **`jobwatch.posting_deadline_claims_distinct`** normalizes HTML entities/tags via `jobwatch.normalize_claim_wording(text)` and collapses `356` raw active claims into **`222`** distinct claims (`145` present in current versions, **`0` HTML-clutter rows remaining**; e.g., Cambridge Consultants' 6 repeated copies collapse to 1 row with `observation_count = 6` and all 6 `claim_ids` preserved).
3. **Ocado `gh_jid` URL canonicalization ([feeds.py](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/src/jobwatch/feeds.py)):** Collapses Ocado's duplicate identical `&gh_jid=<id>` query parameter to `https://careers.ocadogroup.com/jobs/details?gh_jid=<id>` while enforcing exact backreference validation (mismatched IDs still fail closed) and leaving `posting_key` (`greenhouse:ocadogroup:<id>`) and historical immutable `posting_versions` untouched.

---

## 5. Production Verification of Approved Fixes

- **Regression tests before deployment:** **`325 / 325`** offline tests (including [test_corroborator_html_and_deadlines.py](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/tests/test_corroborator_html_and_deadlines.py)) and **`221 / 221`** PostgreSQL 17 integration tests (including [test_evidence_mcp_pg.py](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/tests/postgres/test_evidence_mcp_pg.py)) passed.
- **Release artifacts (commit `62f740219ea78fd56cbb8a908d6d16f33d6b4c34`):**
  - `app.tar.gz`: `sha256: 77fa32ae4116ce5ca04da5b5a4f54bb319c871138bdbf741bfaa81db370d093d` · S3 VersionId `uuPmkTu7eqAfBijC219jBFOmnXLs.Wu6`.
  - `agent.tar.gz`: `sha256: fbb25f1e8e219078f8557534aa0ad6cbb7a6c2f3171e47749e0ec20c620a35d5` · S3 VersionId `5Ip6sIcY.DHwXXzVo5dsLeZTUhGdIFCL` (`model_inference: false`).
- **Pre-deploy & post-recovery backups + off-host `--network none` PG 17.11 restore:**
  - Pre-deploy backup: `e7e9dc6fade84209817fd8ffaec9a82c.dump` (`sha256: b1616af2...`, VersionId `a3C5hBObT.PnIsnr9p3zG6G4kQd8uPeV`).
  - Final post-recovery backup: `c6dd05f2a417447581654a21d19407cf.dump` (`39,671,401 B`, `sha256: b4f40ed6f88a730b2577f076ecba849f705ee6562c14d1d51989b3fcc81a3be6`, VersionId `d_iqXSZZPQNtAObUWXihlux6synXwdYU`).
  - **Off-host `--network none` restore verified:** `exact_rows_schema_and_sequences: true` across **20/20** tables, **5/5** sequences, **8/8** roles, **13/13** migrations (`discovery_candidates = 72`, `discovery_snapshots = 100`, `corroborations = 3`, `distinct_deadline_claims = 222`, `readonly_select_distinct = true`).
- **Live fix verification:**
  - **Corroborator HTML/link fix ([agent_evidence.py](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/src/jobwatch/postgres/agent_evidence.py)):** Re-corroborating `aa9e27cf-f4fb-4ae9-9601-52dd9ab400bf` (`ashby:anima`) appended `b7adce1f-a578-4a4e-a895-0b76ba0a6e8c` (`corroborator:isolated-v3`) with **0** `<script>`/`<style>` text in `evidence_excerpt` (`1,467` clean visible chars), `wording_status = 'not_found'` (preserving strict visible-text separation), `link_evidence = {"matched_via": "a_href", "matched_url": "https://jobs.ashbyhq.com/Anima"}`, and `overall_state = 'claim_corroborated'`. Historical attempt `9b617320-...` (`source_retrieved`) remained preserved append-only.
  - **Ocado URL canonicalization:** `60 / 60` listed Ocado postings now carry clean `?gh_jid=<id>` (`0` listed duplicates); `2` historical `not_seen` postings remain immutable.
  - **System health:** `0` failed systemd units; all **6** CloudWatch alarms `OK`; `741 MiB` used / `1,093 MiB` available RAM; PostgreSQL disk `467M / 16G` (`4%`).

---

## 6. Qualified Remote Evidence-Ingestion Integration Proposal (Isolated — NOT Exposed)

### Isolated Implementation & Verification Completed
- **Core & MCP server:** [evidence_tools.py](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/src/jobwatch/postgres/evidence_tools.py) + [evidence_mcp.py](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/src/jobwatch/postgres/evidence_mcp.py) (`jobwatch-evidence-mcp`).
  - Strictly bound to `jobwatch_agent_evidence` (`NOINHERIT`, `CONNECTION LIMIT 5`, zero direct table writes, `EXECUTE` only on `submit_agent_observation` and `submit_agent_vacancy`).
  - Exposes **only** `submit_public_observation` and `submit_public_vacancy` with `ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)` — **strictly enforcing ChatGPT per-write client confirmation**.
  - Performs **zero outbound network calls** (`resolve_dns=False`; systemd template enforces `RestrictAddressFamilies=AF_UNIX`).
- **Isolated end-to-end MCP verification ([test_evidence_mcp_pg.py](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/tests/postgres/test_evidence_mcp_pg.py)):** Official MCP SDK `ClientSession` over Unix-socket `streamable-http` verified exact tool inventory, `readOnlyHint=False`, live `submit_public_observation` + `duplicate_replayed`, `submit_public_vacancy` (`unscreened_agent_submission`), IMDS SSRF refusal (`ssrf_refused`), wrong DB role refusal (`wrong_database_identity`), and transactional hourly budget enforcement (`budget_exceeded`).
- **Prepared deployment templates (NOT installed/enabled in production):**
  - Service unit: [jobwatch-agent-evidence.service](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/infra/aws/integrations/examples/jobwatch-agent-evidence.service) (`MemoryMax=96M`, `CPUQuota=15%`, `RestrictAddressFamilies=AF_UNIX`).
  - Tunnel config: [tunnel-evidence.yaml.example](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/infra/aws/integrations/examples/tunnel-evidence.yaml.example) (`jobwatch-tunnel-evidence` user, NOT in `jobwatch-db`).
  - Corroborator timer: [jobwatch-corroborator.timer.example](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/infra/aws/systemd/jobwatch-corroborator.timer.example).

### Bounded Recurring Corroboration Schedule Analysis
Without a recurring schedule, observations submitted remotely from ChatGPT would wait for manual operator runs before entering `posting_deadline_claims` or `admit_corroborated_vacancies`. A **bounded 30-minute recurring timer** (`jobwatch-corroborator.timer.example`) makes contributed evidence practically useful within `<= 30 minutes` while preserving all isolation invariants:
- **Schedule:** `OnCalendar=*:05,35:00 UTC`, `RandomizedDelaySec=2min`, `Persistent=false` (48 runs/day max; exits immediately in `< 0.1s` when queue is empty).
- **Request & transfer caps:** `--limit 10` observations/run, `max_requests = 30`/run, `max_bytes = 5 MiB`/run, `TimeoutStartSec=120`, strictly jailed by kernel `nftables` UID `984` (public HTTPS 443 only; IMDS/loopback/RFC1918 dropped).
- **Failure behaviour:** Per-observation failures (`timeout`, `blocked_or_403`, `js_shell_or_empty`, `http_error`) append an explicit `evidence_unavailable` corroboration row so failed URLs do **not** retry infinitely or block later items; `CorroborationBudget` exhaustion halts the current run cleanly and remaining queued items process on the next 30-minute tick.
- **Resource cost on `t4g.small`:** `0 MiB` RSS between runs (`oneshot`), `<= 192 MiB` peak (`~30–45 MiB` typical), `< 0.1%` daily CPU, `$0` extra AWS cost.

### Exact Production Integration & Connection Procedure (For Owner Review Before Exposure)
1. **Create dedicated tunnel OS identity (never in `jobwatch-db`):** `useradd -r -s /usr/sbin/nologin -G jobwatch-agent-evidence jobwatch-tunnel-evidence`.
2. **Set conservative remote submission budget:** Insert explicit budget row in `jobwatch.agent_evidence_budgets` (e.g., `hourly_limit = 20`, `daily_limit = 60` for `jobwatch_agent_evidence`).
3. **Create separate OpenAI tunnel & env file:** Register a dedicated tunnel ID for public evidence ingestion, write `/etc/jobwatch/tunnel-evidence.env` (`0600 root:root`), and render `/etc/jobwatch-tunnel/evidence.yaml` from [tunnel-evidence.yaml.example](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/infra/aws/integrations/examples/tunnel-evidence.yaml.example).
4. **Promote & enable units:** Add `jobwatch-agent-evidence.service` to `install_agent_runtime.py` `UNITS`, copy [jobwatch-agent-evidence.service](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/infra/aws/integrations/examples/jobwatch-agent-evidence.service) and [jobwatch-corroborator.timer.example](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/infra/aws/systemd/jobwatch-corroborator.timer.example) into `/etc/systemd/system/`, run `systemctl daemon-reload`, and enable `jobwatch-agent-evidence.service`, `jobwatch-tunnel@evidence.service`, and `jobwatch-corroborator.timer`.
5. **Connect in ChatGPT:** Add as a **third separate MCP connector** ("Graduate Job Watch Evidence") alongside read-only Research MCP and Owner Actions MCP; confirm ChatGPT prompts for explicit user approval before executing `submit_public_observation` or `submit_public_vacancy`.
6. **Instant revocation / rollback:** `systemctl disable --now jobwatch-tunnel@evidence.service jobwatch-agent-evidence.service jobwatch-corroborator.timer` (plus optional `ALTER ROLE jobwatch_agent_evidence NOLOGIN`) revokes remote ingestion immediately with zero impact on Research MCP, Owner MCP, collection, calendar publication, or backups.

---

## 7. Updated Empirical Research Results (7 Tasks — Live Production vs Isolated)

| # | Research Task | Before (Stage B Baseline) | After (`62f74021` Live Production) | Source |
|---|---|---|---|---|
| **1** | **Discover newly advertised relevant vacancies** | `2,478` listed (`30` priority, `194` review); `30` discovery candidates (`8` complete, `22` truncated); `daily`: `2,027 ms`, `fts_symbol`: `93.8 ms`, `bm25_prefiltered`: `691.2 ms`. | `2,482` listed (`30` priority, `194` review); **`72` discovery candidates (`50` relevant `review`, `22` `excluded`; `72/72` = `100% complete`, `0` truncated!)**; `daily`: `1,995.2 ms`, `fts_symbol`: `123.5 ms`, `bm25_prefiltered`: `762.8 ms`. | Live production Research MCP |
| **2** | **Find vacancies absent from DB** | `100` probed / `13,649` unprobed; `6` `active_uk_relevant` boards; `0/6` benchmark Ashby employers probed. | `106` probed / `13,643` unprobed; **`10` `active_uk_relevant` boards**; **6/6** benchmark Ashby boards probed (`+42` complete UK relevant candidates across Mistral, Wayve, Quantexa, Improbable; Mistral `verified_official_link`); custom ATS gaps (`DeepMind`, `HRT`, `Revolut`, `Robin AI`, `Wise`) documented. | Live production Research MCP + read-only endpoint checks |
| **3** | **Investigate eligibility using retained evidence** | `22/30` legacy discovery rows truncated (confounded cohort comparison). | **`72/72` (`100%`) complete**; `50` relevant discovery candidates avg `5,586` chars (`18/50` visa/RTW, `20/50` degree); same-vacancy paired check proves `0/6 → 3/6` visa & degree on real engineering roles vs `0/22 → 0/22` on freelance `10xteam`. | Live production Research MCP |
| **4** | **Compare current vs historical descriptions** | `6` multi-snapshot discovery candidates. | **`28` multi-snapshot discovery candidates** (all `28` retain both legacy `truncated` and upgraded `complete` snapshots side-by-side; `8.3 ms` query). | Live production Research MCP |
| **5** | **Resolve / expose deadline uncertainty** | `303` raw active claims with HTML entities & duplicates; `122` Ocado duplicate `gh_jid` URLs. | **`posting_deadline_claims_distinct`** collapses `356` raw active → `222` distinct (`145` current, **`0` HTML-clutter rows**, `1,437.7 ms`); prose audit identifies `29` explicit dates, `11` conservative missing-year dates, `49` rolling, `28` warnings; Ocado listed duplicates **`0 / 60`**. | Live production Research MCP |
| **6** | **Contribute externally discovered public evidence** | Anima `source_identity` stuck at `source_retrieved` due to inline JS & ignored `<a href>`; no MCP server prepared. | Anima re-corroborated live to **`claim_corroborated`** via `a_href` with clean JS-free excerpt (`12.6 ms` / `8.6 ms` MCP readback); **isolated Evidence MCP qualified** over real Unix socket (`readOnlyHint=False`, SSRF/budget/identity verified; **not yet exposed remotely**). | Live production (`aa9e27cf-...`) + Isolated PG 17 MCP test |
| **7** | **Select deadlines via authorized owner workflow (dry-run)** | Dry-run succeeded, but previewed duplicated Ocado `?gh_jid=...&gh_jid=...` URL. | Dry-run on `greenhouse:ocadogroup:5036245101` succeeded with clean canonical URL `https://careers.ocadogroup.com/jobs/details?gh_jid=5036245101`; **`calendar_selections: 0 -> 0`, `verifications: 24 -> 24`** (zero commits). | Live production Owner MCP dry-run |

---

## 8. Remaining Concrete Limitations

1. **Directory probing backlog (`13,643 / 13,749` = `99.23%` unprobed):** Awaits owner review of the bounded backfill budget (`Profile B`, ~300 boards/day in split windows) and `ETag`/`304` freshness caching in Section 1.
2. **Remote Evidence MCP & recurring corroboration not yet exposed:** Qualified in isolation; ChatGPT Research sessions still cannot submit externally discovered custom-ATS vacancies (`DeepMind`, `Revolut`, `HRT`) or external deadlines directly until Section 6 is reviewed and approved.
3. **JS-rendered official careers sites limit automated bidirectional identity checks:** Three of the four newly productive benchmark boards (`Wayve`, `Quantexa`, `Improbable`) returned `hosted_link_only` because their official sites render links client-side (only `Mistral` achieved static `verified_official_link`); manual/browser verification remains necessary before activating those three.
4. **No recurring SmartRecruiters or custom-portal adapters:** Wise uses SmartRecruiters (documented public JSON API; future candidate), while Google/DeepMind, Revolut, and HRT's main portal require agent-contributed public observations rather than fragile scrapers.
5. **Prose deadlines are not automatically converted into verified calendar dates (by design):** All `29` explicit prose dates and `11` missing-year prose dates remain non-authoritative `feed_observation` prose wording until corroborated or explicitly verified by the owner (`verifications` / `calendar_selections`).
6. **`search()` indexes `posting_versions` only (not `discovery_candidates`):** Agents searching unactivated discovered employers (such as the 42 new Mistral/Wayve/Quantexa/Improbable roles) must query `research_discovery_evidence` via `sql_query`.
7. **Unattended Workable-wide polling remains disabled:** Only explicitly verified employer Workable boards are collected.
