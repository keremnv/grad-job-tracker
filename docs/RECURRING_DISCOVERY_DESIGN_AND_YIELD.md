# Recurring Automated Discovery — Workable Permitted-Usage Assessment, Directory Workflow & Proposed Policy

**Date:** 24 November 2026 (UTC)  
**Status:** Implemented and tested locally (migration `0010_discovered_boards.sql` + `directory_discovery.py`). **Not yet deployed to production; no recurring discovery timers are enabled.**

---

## 1. Workable-wide discovery: permitted-usage investigation & conclusion

### 1.1 Primary-source findings (inspected 24 November 2026)

| Source inspected | Observed facts |
| --- | --- |
| `https://jobs.workable.com/robots.txt` (`HTTP 200`) | Declares `User-agent: *`, `Content-Signal: search=yes, ai-input=yes, ai-train=no`, `Allow: /search/*`, `Disallow: /search*?*`, `Disallow: /search`, `Disallow: /profile*`, and publishes `Sitemap: https://jobs.workable.com/sitemap.xml` (static SEO category pages, not a live vacancy JSON feed). Does not explicitly list `/api/`. |
| `https://apply.workable.com/robots.txt` (`HTTP 200`) | Declares `User-agent: *`, `Content-Signal: search=yes, ai-input=yes, ai-train=no`, `Disallow:` (empty). Covers per-employer public career widgets (`apply.workable.com/api/v1/widget/accounts/{slug}`). |
| `https://www.workable.com/terms` (`HTTP 200`, updated `24 Nov 2026`) | Governs Workable SaaS customers (`fair usage limitations`, prohibits `reverse engineer, decompile, or disassemble the Software or Website`, and restricts harvesting Candidates). |
| `https://www.workable.com/candidate-terms` (`HTTP 200`) | Candidate GDPR privacy notice for applicants; does not grant a public API license for cross-employer search scraping. |
| Official Workable APIs | Workable officially documents only: (a) authenticated customer **SPI v3** (`{subdomain}.workable.com/spi/v3/jobs`, requires employer-issued Bearer/OAuth token with `r_jobs` scope, ~10 req / 10 s) and (b) public per-employer **Careers Embed / Widget** (`apply.workable.com/api/v1/widget/accounts/{slug}?details=true`) intended for employer career sites. Workable does **not** document or license `jobs.workable.com/api/v1/jobs` as a public developer API; it is an undocumented frontend XHR endpoint behind Cloudflare WAF rate limits (`HTTP 429`). |

### 1.2 Decision on recurring Workable-wide search

- **Keep recurring automated production polling of `jobs.workable.com/api/v1/jobs` DISABLED.** Although `robots.txt` does not disallow `/api/` and carries `Content-Signal: search=yes, ai-input=yes, ai-train=no`, relying on an undocumented frontend endpoint for unattended daily production scraping creates fragile Cloudflare `429` failure modes and operates in a legal/ToS grey zone without an explicit public API license.
- **Keep per-employer Workable widget monitoring ENABLED for verified boards** (`Stalis`, `LMAX Group`, `DC Thomson`, `Grayce`, `TidalSense`, and any future owner-activated boards): those boards explicitly embed `apply.workable.com` widgets on their official careers pages (`robots.txt` `Disallow:` empty), requiring only 1 request/board/day.
- **Keep `src/jobwatch/discovery/workable_search.py` isolated as an optional manual/on-demand research tool only** (strictly bounded, read-only, halts immediately on `429`, never touches candidate/PII endpoints, never used for AI training).

### 1.3 Evaluated documented / licensed alternatives for cross-employer UK discovery

| Mechanism | Official / Licensed? | UK Graduate Coverage | Terms & Cost | Fit for Graduate Job Watch |
| --- | --- | --- | --- | --- |
| **Open-source MIT ATS directories (`kalil0321/ats-scrapers`) + official public ATS APIs (Greenhouse/Ashby/Lever)** | **Yes** (MIT CSV + documented public employer board APIs) | High for UK tech startups/scaleups/quant/AI (`13,749` boards; `100%` recall of our 21 monitored GH/Ashby/Lever boards) | Free, MIT-licensed directory + documented public ATS board APIs | **Primary recurring automated discovery mechanism (implemented below).** |
| **Reed.co.uk Developer API** | **Yes** (official free developer portal & API key) | Very broad UK market (includes non-ATS SMEs, Workable employers, and consultancies) | Free developer tier (API key required; attribution & outbound Reed apply link guidelines) | **Strong documented vacancy-first complement** to discover UK employers outside Greenhouse/Ashby/Lever (including Workable/bespoke employers) if owner approves registering a free Reed API key. |
| **Adzuna Developer API** | **Yes** (official free personal/research tier) | Broad UK multi-board aggregation + salary history | Free strictly for personal/non-commercial use; short snippet descriptions; outbound links route via Adzuna | **Viable secondary personal-use feed** (requires free App ID/Key; less full-text detail than Reed or native ATS APIs). |
| **UK Gov Find an Apprenticeship / Find a Job (DWP)** | Apprenticeship API only; **no** public Find a Job REST API | Apprenticeships only (not BSc/MSc graduate SWE/AI) | Free DfE API for apprenticeships | Low fit for 2027 graduate SWE/ML roles. |

---

## 2. Recurring Employer-Directory Discovery Workflow (Greenhouse, Ashby, Lever)

Implemented in [directory_discovery.py](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/src/jobwatch/discovery/directory_discovery.py), [postgres/directory_discovery.py](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/src/jobwatch/postgres/directory_discovery.py), [0010_discovered_boards.sql](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/src/jobwatch/postgres/migrations/0010_discovered_boards.sql), and [directory_discovery_access.sql](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/sql/postgresql/directory_discovery_access.sql):

1. **Periodic directory refresh (`fetch_directory`, `sync_directory_boards`):** Downloads MIT-licensed Greenhouse, Ashby, and Lever board CSVs (`<= 8 MiB` each), validates every slug against `feeds.BOARD`, records SHA-256/byte/timestamp provenance, and upserts into `jobwatch.discovered_boards`.
2. **Deduplication against existing sources:** Links `discovered_boards.source_id` against `jobwatch.sources` (all statuses: `monitored`, `candidate`, `paused`, `retired`) and automatically excludes all registered sources from the probe queue.
3. **Conservative two-pass UK relevance screening (`probe_ats_board`):**
   - **Greenhouse two-pass bandwidth saver:** Fetches lightweight `/jobs` metadata first (`~5–50 KiB`), and only requests `?content=true` when UK location signals are present (skipping full HTML download on non-UK boards!).
   - **Ashby:** Single documented `posting-api/job-board/{board}` call.
   - **Lever:** Single bounded `limit=100` page with automatic EU endpoint (`api.eu.lever.co`) fallback on 404.
   - Screens UK-located vacancies with **unchanged `jobwatch.filtering.screen()`** (`screen-2026-10-23.1`), persisting only `priority`/`review` UK vacancies into existing `jobwatch.discovery_candidates` via `discovery.record_discoveries()` (reusing excerpt caps, deduplication, and `research_discovery_candidates`).
4. **Automated read-only employer identity check (`verify_board_identity`):** For boards yielding UK relevant roles, inspects the hosted ATS page (`<a href>` only, filtering ATS/social/asset hosts) and checks the employer's official site / `/careers` for explicit bidirectional corroboration (`verified_official_link`, `hosted_link_only`, `unverified_js_or_blocked`). Never bypasses JS/WAF gates and **never** auto-activates `sources`.
5. **Rotating cooldown queue & strict separation of authority:** `select_probe_batch` balances UK-hinted and general unknown boards, re-probes `active_uk_relevant` boards after `7` days and `active_non_uk`/`empty`/`dead` boards after `30` days. Runs as `jobwatch_collector` with **zero** writes to `sources`, `postings`, `leads`, `applications`, `triage`, or `calendar_selections`.
6. **Research MCP visibility:** `jobwatch.research_discovered_boards` joins `discovered_boards` with `sources` and active `discovery_candidates` counts; read-only grants in `directory_discovery_access.sql` & `chat_readonly.sql` allow ChatGPT Research MCP to query newly discovered unknown boards and vacancies immediately while refusing mutations.

---

## 3. Measured Live Discovery Yield (24 November 2026, read-only bounded run)

Executed live against `kalil0321/ats-scrapers` and official Greenhouse/Ashby/Lever APIs, strictly deduplicated against all 26 currently monitored production boards (`delay=0.35 s`, zero production writes):

| Metric | Measured value |
| --- | ---: |
| Total directory boards refreshed (`3` CSV fetches, `0.86 MiB`) | **`13,749`** (`7,363` Greenhouse, `4,083` Ashby, `2,303` Lever) |
| Existing monitored GH/Ashby/Lever boards deduplicated | **`21 / 21` (`100%`)** (`11` GH, `8` Ashby, `2` Lever) |
| Previously unknown boards in directory | **`13,728`** (`23` UK-name-hinted + `13,705` general) |
| Bounded sample probed (`17` UK-hinted + `24` general) | **`41` unknown boards** |
| Active / empty / dead / rate-limited (`429`) | **`39` active (`95.1%`)**, `2` empty, `0` dead, **`0` rate-limited** |
| Total vacancies inspected / UK vacancies | **`671` total** / **`180` UK (`26.8%`)** |
| **UK-relevant unknown boards discovered** | **`4 / 41` (`9.8%` hit rate!)** — `2/17` (`11.8%`) UK-hinted, `2/24` (`8.3%`) general |
| UK relevant candidate vacancies surfaced | **`4` `review` roles** across **4 previously unknown UK employers** |
| Total requests / bandwidth / wall time / peak Python RAM | **`65` requests**, **`8.61 MiB`** (`~215 KiB/board`), **`48.6 s`**, **`6.33 MiB`** peak |

### Newly discovered unknown UK employers & roles from this single 41-board sample

1. **Zego** (`ashby:zego`, general stratum — `19/28` UK vacancies): *Data Engineer* (London) — `https://jobs.ashbyhq.com/zego/1da7075c-2bb2-460b-9eab-88716c4c0f95`
2. **Eucalyptus** (`greenhouse:eucalyptus`, general stratum — `31/86` UK vacancies): *Full-Stack Engineer - Shop/Ecosystem* (UK - HQ - London) — `https://job-boards.greenhouse.io/eucalyptus/jobs/4663694005`
3. **Alpha FMC - UK** (`greenhouse:alphafmc`, UK-hinted — `3/3` UK vacancies): *UK - BusOps - Junior IT Support Engineer* (United Kingdom) — `https://job-boards.greenhouse.io/alphafmc/jobs/7859436002`
4. **WisdomTree UK** (`lever:wisdomtree-2`, UK-hinted — `2/4` UK vacancies): *Software Engineer* (London) — `https://jobs.lever.co/wisdomtree-2/1f88bab3-aa85-4ad5-860f-5d929f1af1e3`

*Operational finding from the live sample:* UK tech employers rarely include `"UK"` or `"London"` in their company slug (`Zego`, `Eucalyptus`), and the general stratum achieved an `8.3%` UK-relevant board discovery rate (`~3–4` new UK tech boards per 40-board daily batch!).

---

## 4. Proposed Recurring Schedule, Request Budget & Activation Policy (Awaiting Owner Approval)

Before enabling any production systemd timer for migration `0010`:

| Control | Proposed production setting | Rationale / Resource impact on `t4g.small` |
| --- | --- | --- |
| **Directory CSV refresh** | **Weekly** (Sunday `04:00 UTC`, `3` GitHub raw GETs, `~0.9 MiB`) | Refreshes newly listed Greenhouse/Ashby/Lever employers without daily churn. |
| **Rotating board probe job** | **Daily** (`04:30 UTC`, offset from `06:15 UTC` collection & `00:00/12:00 UTC` backups) | **`max_boards=40`**, **`max_requests=80`**, **`max_bytes=32 MiB`**, **`delay=0.5 s`**, **`verify_identity_limit=8`**, immediate halt on `429`. Expected runtime **`~50–70 s`**, peak RAM **`<20 MiB`**, DB growth **`<2 MiB/month`** (metadata + bounded excerpts only, never full HTML blobs). |
| **Queue rotation & cooldowns** | Prioritize unprobed `uk_hint` first (up to 50% of batch), then rotating general unprobed boards; re-probe `active_uk_relevant` every `7 d`, `active_non_uk`/`empty`/`dead` every `30 d` | Discovers ~`3–4` new UK-relevant employers/day (`~90–120/month`) while keeping UK candidates fresh. |
| **Promotion & Activation Policy (Human-in-the-Loop)** | **Never auto-activate.** Candidates and boards appear automatically in Research MCP (`research_discovered_boards`, `research_discovery_candidates`). Promote a board to `sources.status='monitored'` ONLY when: (1) `>= 1` relevant UK graduate/junior/early-career vacancy (or strong UK tech fit), (2) `identity_status='verified_official_link'` (or explicit manual verification for JS-rendered sites), (3) `custom_host_urls = 0` (or explicit reviewed `CUSTOM_GREENHOUSE_URLS` rule), and (4) explicit owner GO + live `check_endpoint` + `activate_source`. | Keeps monitored fleet tightly curated (`26` → `50–120` high-signal UK boards) while automatically surfacing vacancies across thousands of unknown employers without database bloat. |
| **Workable-wide discovery** | **Disabled** as a recurring production job | Re-evaluate only if Workable publishes a licensed search API; consider adding an official free **Reed.co.uk API** discovery adapter as a documented UK vacancy-first complement in a later sprint. |
