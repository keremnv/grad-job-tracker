# Discovery Implementation, Verified Employer Expansion & Proposed Production Rollout

**Date:** 24 November 2026 (UTC)  
**Status:** Implemented and tested locally in isolated disposable PostgreSQL 17. **NOT deployed to production.** No production migrations, schedules, source activations, or owner writes have been executed.

---

## 1. Summary of implemented & tested code changes

| Area | Files | Behaviour & preserved invariants |
| --- | --- | --- |
| **Native Workable ATS adapter** | `src/jobwatch/feeds.py`, `src/jobwatch/identity.py`, `src/jobwatch/config.py`, `src/jobwatch/postgres/collection.py`, `src/jobwatch/postgres/material.py` | Collects `https://apply.workable.com/api/v1/widget/accounts/{board}?details=true`. Groups multi-country entries sharing one `shortcode` (as proven by Stalis `1C9CCEA6B4` UK+Ireland) into one canonical `Posting` while retaining every raw location entry losslessly for `replay_native` and `evidence`. Fails closed on conflicting titles/URLs under a shared shortcode or invalid hosts. Also adds dotted/case-insensitive Ashby board slug support (`Snyk`, `iwoca.co.uk`). |
| **Isolated Workable search discovery adapter** | `src/jobwatch/discovery/workable_search.py`, `src/jobwatch/discovery/__init__.py` | Replaceable adapter wrapping `jobs.workable.com/api/v1/jobs` + bounded `apply.workable.com` widget corroboration. Enforces strict request caps (`search <= 100`, `widget <= 100`), conservative delays (`0.5 s` / `1.5 s`), immediate halt on HTTP `429`, cross-query and canonical-shortcode deduplication, and explicit `authoritative_board_observation=False` provenance. **Recurring production scheduling remains disabled.** |
| **Discovery candidate lifecycle & schema** | `src/jobwatch/postgres/migrations/0008_workable_and_discovery.sql`, `src/jobwatch/postgres/discovery.py`, `src/jobwatch/postgres/importer.py` | Widens `sources.provider` to include `'workable'` and `sources.board` for dotted slugs (`iwoca.co.uk`). Adds lightweight `jobwatch.discovery_candidates` (storing compact plain-text `description_excerpt <= 2,500 chars` + cryptographic `description_sha256` & `evidence_sha256` instead of duplicating full HTML blobs) and `jobwatch.research_discovery_candidates` view. Supports full lifecycle: `new` → `observed` → `verified_identity` → `linked_posting` / `promoted_lead` / `dismissed`, cross-mechanism deduplication, offline `rescreen_candidates`, and owner-only audited `promote_to_lead`. |
| **Research & MCP visibility** | `sql/postgresql/discovery_access.sql`, `sql/postgresql/chat_readonly.sql`, `src/jobwatch/postgres/reports.py` | Grants strictly read-only `SELECT` on `discovery_candidates` and `research_discovery_candidates` to `jobwatch_agent_readonly` (Research MCP). Exposes newly discovered external vacancies from unknown employers in `sql_query`, `sql_schema`, and `run_report('daily'|'weekly')` without weakening owner write separation. |
| **Qualification tests** | `tests/test_feeds.py`, `tests/test_postgres_material.py`, `tests/test_workable_discovery_adapter.py`, `tests/postgres/test_discovery_lifecycle.py` | Covers Workable multi-country shortcode grouping, conflicting shortcode fail-closed, material change vs formatting noise, `replay_native`, 429 halt, cross-mechanism deduplication, auto-linking, owner-only audited promotion, and Research MCP read-only boundaries. |

---

## 2. Official employer verification results (24 November 2026)

Every candidate board below was independently checked against the employer's **official website / careers pages** (read-only static HTML/JS inspection; no WAF/JS bypass). **No board has been activated in production.**

### 2.1 Existing manual-watch employers (11 live ATS boards found in directories)

| Tier | Employer | Provider & Board | Region | Official verification evidence (checked 2026-11-24) | Live jobs / `priority` / `review` | Ready for activation? |
| --- | --- | --- | --- | --- | --- | --- |
| **Wave 1 (Immediate)** | **Graphcore** | `greenhouse:graphcore` | `global` | `https://www.graphcore.ai/jobs` loads `module_greenhouse_api_V3_22.min.js`; hosted board `job-boards.greenhouse.io/graphcore` links back to `graphcore.ai`. | `185` / **10** / `19` | **Yes** (highest immediate UK 2027 grad yield) |
| **Wave 1 (Immediate)** | **Canonical** | `greenhouse:canonical` | `global` | `https://canonical.com/careers/all` links `greenhouse.io/canonical`. | `316` / `0` / `75` | **Yes** (incl. Graduate SWE & Junior Ubuntu/Kernel roles) |
| **Wave 1 (Immediate)** | **Faculty** | `ashby:faculty` | `global` | `https://faculty.ai/en-gb/job-board` references `ashby` and matches live posting ID `27e13461-0792-4c98-94e5-a989cf4b8701`. | `90` / `0` / `13` | **Yes** |
| **Wave 1 (Immediate)** | **Thought Machine** | `ashby:thought-machine` | `global` | `https://www.thoughtmachine.net/careers` links `ashbyhq.com/thought-machine`. | `34` / `0` / `2` | **Yes** |
| **Wave 1 (Immediate)** | **Snyk** | `ashby:snyk` | `global` | `https://snyk.io/careers/` links `ashbyhq.com/Snyk` (supported by case-insensitive Ashby fix in Commit 2). | `37` / `0` / `1` | **Yes** |
| **Wave 1 (Immediate)** | **Autotrader** | `greenhouse:autotrader` | `eu` | `https://careers.autotrader.co.uk/jobs` references `greenhouse` and `autotrader`; EU board `job-boards.eu.greenhouse.io/autotrader`. | `12` / `0` / `0` | **Yes** (low cost; watches for 2027 UK grad intake) |
| **Wave 2 (Needs custom-host Greenhouse rule)** | **Stripe** | `greenhouse:stripe` | `global` | `https://stripe.com/careers/search` references `greenhouse` and live posting ID `8172510`. All 770 posting URLs use custom host `stripe.com/jobs/search?gh_jid=...`. | `770` / `0` / `10` | **Deferred to custom-host rule** (also largest board: `5.8 MiB`) |
| **Wave 2 (Needs custom-host Greenhouse rule)** | **Jane Street** | `greenhouse:janestreet` | `global` | `https://www.janestreet.com/join-jane-street/apply/8631912002/?gh_jid=8631912002` returns HTTP 200 matching Greenhouse ID `8631912002`. | `217` / `0` / `14` | **Deferred to custom-host rule** |
| **Wave 2 (Needs custom-host Greenhouse rule)** | **Ocado Group** | `greenhouse:ocadogroup` | `eu` | `https://careers.ocadogroup.com/jobs` contains `gh_jid` links; posting URLs use `careers.ocadogroup.com/jobs/details?gh_jid=...` (duplicate param!). | `59` / `0` / `7` | **Deferred to custom-host rule** |
| **Hold (Verification incomplete)** | **Quantexa** | `ashby:quantexa` | `global` | `jobs.ashbyhq.com/quantexa` links back to `quantexa.com`, but `/careers/` is JS-rendered without static Ashby link. | `51` / `0` / `3` | **Manual browser confirmation before activation** |
| **Blocked (Origin TLS error)** | **DV Trading** | `greenhouse:dvtrading` | `global` | `https://www.dvtrading.co/careers` continues to return HTTP `526` (origin TLS error). | `70` / `0` / `6` | **Blocked** (retain as candidate/manual check until official domain TLS verified) |
| **Rejected (False collision)** | ~~Wise~~ | `greenhouse:wise` | — | US-based Wise Foods (`Area Sales Leader - Memphis, TN`), **not** UK fintech Wise PLC (`wise.jobs`). | `18` | **Rejected** |

### 2.2 Newly discovered Workable & directory employers (beyond the existing manual watchlist)

| Wave | Employer | Provider & Board | Official verification evidence (checked 2026-11-24) | Live UK grad/early-career signal | Status |
| --- | --- | --- | --- | --- | --- |
| **Wave 1B (After `0008`)** | **Stalis** | `workable:stalis` | `https://stalis.com/why-stalis/culture-and-careers/` embeds `id="workable-roles"`, `workable.com/assets/embed.js`, and `whr_embed`. | `workable:stalis:1C9CCEA6B4` — *Data Science & AI Graduate* (`priority`) | **Verified; ready after `0008`** |
| **Wave 1B (After `0008`)** | **LMAX Group** | `workable:lmax` | `https://careers.lmax.com/` uses `lmax-workable` plugin and renders exact live shortcode `572A2F5CB1`. | `workable:lmax:572A2F5CB1` — *Junior Software Developer (Java)* (`priority`) + 1 `review` | **Verified; ready after `0008`** |
| **Wave 1B (After `0008`)** | **DC Thomson** | `workable:dcthomson` | `https://www.dcthomson.co.uk/careers/` links `https://apply.workable.com/dcthomson/`. | `workable:dcthomson:599B94CAA8` — *Junior Software Engineer* (`priority`) | **Verified; ready after `0008`** |
| **Wave 1B (After `0008`)** | **Grayce** | `workable:grayce` | `https://www.grayce.co.uk/careers/` links `https://apply.workable.com/grayce/`. | `workable:grayce:1789116104` — *Graduate Software Engineer - Bridgend* (`priority`) | **Verified; ready after `0008`** |
| **Wave 1B (After `0008`)** | **TidalSense** | `workable:tidalsense` | `https://tidalsense.com/careers/` embeds `whr_embed`, `apply.workable.com` CSP, and `tidalsense@jobs.workablemail.com`. | `workable:tidalsense:C60DD0137A` — *Research Engineer* (`review`) | **Verified; ready after `0008`** |
| **Wave 1B (After `0008`)** | **iwoca** | `ashby:iwoca.co.uk` | `https://www.iwoca.co.uk/careers/` links `ashbyhq.com/iwoca.co.uk` (requires `0008` dotted-slug constraint). | *Applied AI Engineer*, *Backend/Full-Stack Engineer* (London) | **Verified; ready after `0008`** |
| **Wave 1B (After `0008`)** | **Edra** | `ashby:edra` | `https://edra.ai/careers` links `ashbyhq.com/edra`. | *AI Research Engineer (London)*, *Forward Deployed AI Engineer (London)*, 5 SWE roles | **Verified; ready after `0008`** |
| **Wave 1B (After `0008`)** | **Ably** | `greenhouse:ably30` | `https://ably.com/careers` → `/vite/assets/job-listings-By590VYg.js` explicitly configures Greenhouse board `ably30` (`eu`). | *Software Engineer, Pub/Sub (London, UK)* | **Verified; ready after `0008`** |
| **Hold (Verify manually)** | Mitsubishi Electric UK, Goliath, Queueco, Longshot Systems Ltd | `workable:*` | `/careers` 404 or no static Workable link on homepage. | 3 `priority` + 1 `review` roles (`599B…`, `299B…`, `4E1A…`, `EA27…`) | **Keep in `discovery_candidates` / `leads` until careers page manually checked** |

---

## 3. Staged production rollout & resource budget

All production changes remain **gated on explicit owner review and GO**.

| Rollout stage | Scope | Monitored boards (cumulative) | Estimated listed postings | Est. DB size (1 mo) | Est. S3 dump (1 mo) | Daily scan time | BM25 current RAM (est.) |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| **Current live baseline** | 9 boards (no Workable, no discovery) | 9 | 638 | `67.8 MiB` | `16.1 MiB` | `22.4 s` | `~82 MiB` (measured) |
| **Stage 1 (Packaged release + `0008` + Wave 1A/1B)** | Deploy packaged release, apply `0008_workable_and_discovery.sql` + `discovery_access.sql` (with pre/post S3 backup & restore check), validate & activate Wave 1A (6 manual-watch boards: Graphcore, Canonical, Faculty, Thought Machine, Snyk, Autotrader) + Wave 1B (8 newly verified Workable/Ashby/GH boards: Stalis, LMAX, DC Thomson, Grayce, TidalSense, iwoca, Edra, Ably) | **23** | **~1,400** | `~155 MiB` | `~37 MiB` | `~38 s` | `~129 MiB` (**safe** inside `MemoryMax=256M`) |
| **Stage 2 (Custom-host GH + selective UK directory wave)** | Add explicit config-driven custom Greenhouse host rules for verified `Stripe`, `Jane Street`, `Ocado Group` + promote top verified UK boards from weekly offline `ats-scrapers` header sweeps | **~50** | **~2,400** | `~260 MiB` | `~62 MiB` | `~1.3 min` | `~191 MiB` (**approaching 256M** — complete Stage 3 retrieval upgrade before exceeding ~50 boards!) |
| **Stage 3 (Retrieval scalability upgrade)** | Add `search_sym_tsv` (`simple` symbol-preserving GIN FTS, `~2.5–14 MiB`) + two-stage GIN-prefiltered BM25 (or current-only SQL BM25), and decouple `ReadTools.working_set` from total historical `postings` | **50–120** | **~2,400–4,200** | `~260–460 MiB` | `~62–110 MiB` | `~1.3–3 min` | **`~45–60 MiB` flat** (eliminates corpus-size OOM!) |
| **Stage 4 (Optional scheduled Workable discovery)** | Enable bounded daily `WorkableSearchAdapter` (`<=20` search reqs, `<=35` widget reqs) writing into `discovery_candidates` **only after owner reviews ToS comfort & failure alerting** | 50–120 + Workable search feeder | +`~50–150` lightweight candidates (`<1 MiB`) | `+ <1 MiB` | `+ <0.3 MiB` | `+ ~80 s` (separate timer) | Unchanged (stays outside `posting_versions`) |

---

## 4. Permitted use, deferred decisions, and remaining limitations

1. **Workable `jobs.workable.com/api/v1/jobs` terms & status:** `jobs.workable.com/robots.txt` declares `Content-Signal: search=yes, ai-input=yes, ai-train=no` and does not disallow `/api/`; `apply.workable.com/robots.txt` has `Disallow:` empty and the same `Content-Signal`. However, `jobs.workable.com/api/v1/jobs` is an **undocumented public frontend API** subject to Workable's general website Terms of Use and Cloudflare WAF rate limits. Therefore:
   - Recurring production scheduling is **intentionally NOT enabled**.
   - The adapter is isolated in `src/jobwatch/discovery/workable_search.py`, strictly bounded, read-only, never touches candidate/PII endpoints, never uses data for AI training, and halts immediately on HTTP `429`.
2. **Deferred items requiring explicit owner sign-off:**
   - Whether to approve **Stage 1** deployment (`0008_workable_and_discovery.sql` + `discovery_access.sql` + Wave 1A/1B board activations).
   - Whether to implement a general per-source `custom_url_pattern` for **Stripe**, **Jane Street**, and **Ocado Group** (Wave 2) — note Stripe alone adds `770` postings (`5.8 MiB` per fetch), which accelerates corpus growth toward the current BM25 ceiling.
   - Whether to approve **Stage 3** (`search_sym_tsv` + two-stage/SQL BM25) *before* expanding beyond ~50 boards.
   - Whether to enable **Stage 4** (scheduled recurring Workable search) vs running it manually/on-demand.
   - Screening refinement: `iwoca`'s *"Growth Associate - M&A Platform"* false positive (`Associate` + `Platform` → `priority`) suggests adding `growth` / `m&a` to `NON_ENGINEER` in a future versioned screening release (`RULES_VERSION` bump).
3. **Remaining limitations:**
   - **Absence from discovery ≠ closure:** Disappearing from `jobs.workable.com` search results never marks a candidate closed or a posting `not_seen`.
   - **Non-ATS employers remain outside ATS-wide discovery:** Employers using Workday, Avature, Taleo, SuccessFactors, or bespoke sites (e.g., Softwire, G-Research, Bloomberg, ARM, Lloyds, BAE Systems, JLR, BT Group, Arctic Lake) remain manual watch / separate research.
   - **Workable widget pagination check:** No single Workable board we tested exceeded 100 jobs (`ellison-institute-of-technology` had 74); if a future monitored Workable board exceeds 100 jobs, verify whether `apply.workable.com/api/v1/widget/accounts/{slug}` returns all jobs or truncates before trusting `absence_safe=true` on very large Workable boards.
