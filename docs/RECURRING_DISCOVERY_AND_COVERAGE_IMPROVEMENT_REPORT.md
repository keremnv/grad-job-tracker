# Recurring Discovery Rollout and Deterministic Coverage Improvement Report

**Date:** 25 November 2026  
**Deployed Production Commit:** `7d8aee615c84ae410688b4b7a608adad50de569b` (Migration `0010` + initial recurring directory discovery schedule ONLY)  
**Isolated Local Prototypes (NOT deployed to production):** Prioritized discovery scheduler (`select_probe_batch_prioritized`), automatic source activation prototype (`auto_activation.py`), candidate deterministic screener v2 (`screen-2026-11-25.1`), and structured prose deadline parser (`parse_prose_deadline`).

---

## 1. Production Deployment Evidence (`0010` + Recurring Discovery)

Only migration `0010_discovered_boards.sql`, read-only access grants (`sql/postgresql/directory_discovery_access.sql`), and the approved initial recurring directory discovery schedule were deployed to `i-0a21a9da641e3f22e` (`t4g.small`, `eu-north-1`) via the guarded release workflow.

### Release Artifacts & Backups

| Item | Verified Value |
|---|---|
| Deployed commit | `7d8aee615c84ae410688b4b7a608adad50de569b` |
| App ARM64 artifact (`AES256`) | `artifacts/app-7d8aee615c84ae410688b4b7a608adad50de569b.tar.gz` (`sha256: 1c6527faa4c32067b0dbdcbb1980123972bbf4b231488ae61f0b3e853ea2d9c3`) |
| Agent ARM64 artifact (`AES256`) | `artifacts/agent-7d8aee615c84ae410688b4b7a608adad50de569b.tar.gz` (`sha256: cf2adde176aed52973fe75c9223050213efc7c088deb724ce267a1b47961bb1a`) |
| Overnight scheduled backup (`00:00:29Z`) | `backups/2026/11/25/0871fb8aca51436f82b6fd818d9aebda.dump` (`23.41 MiB`) |
| Pre-maintenance S3 backup (`08:12:07Z`) | `backups/2026/11/25/3998371962d14314a469444868bcf628.dump` (`23.44 MiB`, `16 s`) |
| Post-rollout S3 backup (`08:13:46Z`) | `backups/2026/11/25/2d991d3c5d4f4e10a59837f3b2683db8.dump` (`23.70 MiB`, `17 s`) |
| Migration `0010` idempotency | Applied cleanly; repeat migrate confirmed no-op |
| Active discovery timers | `jobwatch-discover-refresh.timer` (Sun `04:00 UTC`), `jobwatch-discover-probe.timer` (daily `04:30 UTC`) |
| Hardened budgets | `max_boards=40`, `max_requests=80`, `max_bytes=32 MiB`, `verify_limit=8`, `delay=0.5s`, immediate HTTP `429` halt, `MemoryMax=128M`, `CPUQuota=25%` |
| Research MCP socket verification | `research_discovered_boards` + `research_discovery_candidates` readable via `/run/jobwatch-agent-read/mcp.sock`; `DELETE FROM jobwatch.discovered_boards RETURNING *` refused (`isError=true, ok=false`) |
| Health & CloudWatch alarms | `jobwatch-mcp-health.service`: `{"mcp_healthy": 1, "probe_ok": 1, "restarts": 0, "restarts_delta": 0}`; all 6 CloudWatch alarms `OK` |

### Initial Controlled Discovery Run Results

- **`jobwatch-discover-refresh.service`:** Synced **`13,749` ATS boards** across Greenhouse, Ashby, and Lever; cleanly deduplicated **`21/21`** monitored boards from those three providers (`monitored_sources=26` unchanged).
- **`jobwatch-discover-probe.service`:** Probed **`40` boards** in **`33 s`** (`2.365 s` CPU, `56` HTTP requests, `10.56 MiB` transferred, `0` HTTP `429` rate limits):
  - `34` `active_non_uk`
  - `3` `active_uk_relevant`: `ashby:10xteam` (`22` review), `ashby:3e` (`1` review), `greenhouse:alphafmc` (`1` review)
  - `3` `empty`
  - **`24` candidate vacancies** inserted into `jobwatch.discovery_candidates` (visible via `research_discovery_candidates` and `research_discovered_boards`).
  - **`0` sources activated** and **`0` owner-table or `owner_audit` writes**.

---

## 2. Discovery Scheduling Improvement (Isolated Prototype)

At 40 probes/day, naive alphabetical FIFO over `13,749` boards takes ~344 days for a single pass, exhibits strong alphabetical/provider bias, and fails to revisit newly added or high-yield UK employers promptly.

We implemented `select_probe_batch_prioritized` in [`src/jobwatch/postgres/directory_discovery.py`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/src/jobwatch/postgres/directory_discovery.py) and evaluated three deterministic scheduling strategies over a 90-day simulation (`3,600` probes, empirical 13,728-board yield distribution + 30 newly added boards per weekly refresh) in [`experiments/discovery_scheduling_eval.py`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/experiments/discovery_scheduling_eval.py):

| Strategy | Total Requests | Unique Boards Explored | Unique UK Boards Found | UK-Relevant Observations | Priority Observations | UK Obs / 100 Requests | New UK Boards Detected (Avg Latency) |
|---|---:|---:|---:|---:|---:|---:|---:|
| `fifo` (current alphabetical rotation) | 3,907 | 3,600 | 307 | 307 | 69 | **7.86** | 0 (—) |
| `greedy_priority` (0% exploration reserve) | 4,770 | 1,013 | 103 | 1,170 | 224 | **24.53** | 0 (—) |
| `prioritized_explore` (35% explore + multi-signal + backoff) | 4,857 | **1,841** | **154** | **1,257** | **295** | **25.88** (`3.29×` FIFO) | **46** (`0.4 days`) |

### Key Scheduling Design Choices

- **Anti-starvation exploration reserve (`35%`):** Strictly allocated to `probe_status='unprobed'` boards, balanced round-robin across Greenhouse, Ashby, and Lever via deterministic `abs(hashtext(board_key))` ordering (eliminating alphabetical bias), with newly discovered directory entries (`first_discovered_at >= now - 7d`) probed first.
- **Deterministic multi-signal priority score (`65%`):** Combines newly added entries (`+30`), verified official website identity (`+25`), historical `priority_vacancies * 20`, `least(review_vacancies, 10) * 5`, `uk_hint` (`+20`), `uk_vacancies > 0` (`+15`), `.uk`/`.co.uk` domain (`+20`), and staleness since last probe (`+1/day`, capped at `60`).
- **Exponential backoff:** `dead`/`error` boards back off `7d → 14d → 28d → 56d` (`7 * 2^min(probe_count-1, 3)`); `active_non_uk`/`empty` boards back off `30d → 60d` after two consecutive non-UK probes.

---

## 3. Automatic Source Activation Policy & Isolated Prototype

Implemented in [`src/jobwatch/postgres/auto_activation.py`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/src/jobwatch/postgres/auto_activation.py) and tested against disposable PostgreSQL 17 in [`tests/postgres/test_auto_activation.py`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/tests/postgres/test_auto_activation.py). **Not enabled in production.**

### Objective Eligibility Gates (`AutoActivationPolicy`)

A discovered board qualifies for automatic activation **only** when every deterministic gate passes:

1. **Employer identity verified via official evidence:** `identity_status = 'verified_official_link'`, valid public `careers_url`, and non-empty `identity_evidence`. Ambiguous (`hosted_link_only`, `unverified_js_or_blocked`, `domain_mismatch`) boards remain in `discovered_boards`.
2. **Provider, region, and slug validation:** Supported provider (`greenhouse`, `ashby`, `lever`, `workable`), valid `feeds.BOARD` slug, and valid provider/region pair.
3. **URL contract safety:** Zero unreviewed custom URL hosts (`custom_host_urls = 0`, unless already explicitly allowlisted in `feeds.CUSTOM_GREENHOUSE_URLS`).
4. **UK recruitment evidence:** `probe_status = 'active_uk_relevant'` and `priority_vacancies + review_vacancies >= 1` (configurable `min_priority_vacancies`).
5. **Resource and fleet caps:**
   - Single-board vacancy cap: `<= 500` vacancies (re-checked during live endpoint validation).
   - Fleet monitored-board cap: `<= 60` monitored boards (initial operational ceiling).
   - Fleet total listed-posting cap: `<= 6,000` listed postings.
   - Per-run activation velocity cap: `<= 3` boards/run.
6. **Standard validation & audited activation:** Registers candidate source under explicit actor `policy:auto-activation-v1` (`discovered_by = 'auto_activation:...'`), runs collector-role `check_endpoint` requiring `status='validated'` and `absence_safe=True`, and activates via `activate_source`.

### Lifecycle & Deactivation Safeguards

`evaluate_deactivation_candidates` inspects **only** auto-activated sources (`discovered_by LIKE 'auto_activation:%'`), never touching manually curated owner sources. It flags auto-activated sources for pausing when any of three objective conditions trigger:

- `>= 5` consecutive failed `publish` runs (`consecutive_collection_failures`)
- Latest board size exceeds `2×` the single-board vacancy cap (`> 1,000`, `exceeded_2x_vacancy_cap`)
- Board has been monitored `>= 90` days with `0` currently listed `priority` or `review` vacancies (`zero_uk_relevant_yield_over_window`)

---

## 4. Deterministic Screening Audit & Candidate `screen_v2` Evaluation

### Empirical Audit of `screen-2026-10-23.1` on Live Production Corpus (`2,463` Listed Postings)

Read-only inspection of all `2,463` listed production postings (`21` priority, `210` review, `2,232` excluded) plus the `24` newly discovered candidates surfaced recurring deterministic failure modes:

- **False demotions (`priority` → `review`):**
  - Holiday/benefits tenure clauses matching `EXPERIENCE` (`Ocado Group`: *"25 days annual leave (rising to 27 after 5 years)"*).
  - Security-clearance UK residency clauses matching `EXPERIENCE` (`Cambridge Consultants`: *"continuously resident in the UK for a minimum of 5 years"*).
  - Explicit new-grad maximum-experience caps matching `EXPERIENCE` (`Stripe`: *"no more than 18 months of professional experience"*).
  - Inclusive degree ladders matching `DOCTORAL` (`Cambridge Consultants`: *"An excellent degree or PhD"*; `Palantir`: *"Bachelor's, Master's, or PhD in Computer Science"*).
  - Disambiguated UK office addresses triggering `location.unknown` (`Marshall Wace`: *"London, London - Sloane Street"*).
  - Multi-year titles like `(2026 or 2027 start)` triggering `year.title_mismatch` despite including `2027`.
- **False positives (`excluded` leaked into `priority`/`review`):**
  - Freelance RLHF/AI Trainer gigs (`10x Team`: `22` roles such as *Real Estate Agent - AI Trainer - Freelance* matching `\bai\b` → `ml_ai`).
  - Commercial/growth/ads roles (`iwoca`: *Growth Associate - M&A Platform* in `priority`; `Ocado Group`: *Partner Growth Associate / Executive* in `review`; `Canonical`: *Junior Ads Specialist* in `review`).

### Candidate `screen_v2` (`RULES_VERSION_V2 = 'screen-2026-11-25.1'`)

Implemented alongside untouched `screen()` in [`src/jobwatch/filtering.py`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/src/jobwatch/filtering.py). Adds three transparent codes (`exclude.freelance_ai_trainer`, `level.entry_description_only`, `level.desirable_experience_only`), sentence-scoped benign-clause stripping for holiday/residency/max-new-grad caps, desirable-vs-required experience separation, inclusive degree-ladder PhD handling, UK office disambiguation, `2027`-inclusive year compatibility, and promotion of explicit graduate `data_science`/`security` roles carrying software evidence.

#### A. Labelled 44-Case Evaluation Benchmark ([`evaluation/expanded_corpus_eval.json`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/evaluation/expanded_corpus_eval.json))

Covers all 8 requested screening challenge categories (`explicit_grad_holiday_residency_cap`, `commercial_and_freelance_noise`, `grad_data_science_and_cyber`, `generic_swe_entry_level_desc`, `required_vs_desirable_experience`, `mandatory_vs_non_mandatory_phd`, `multi_country_remote_ambiguous_uk`, `year_2027_compatibility`):

| Screener | Rules Version | Exact Accuracy (44 cases) | Priority FP | Priority FN | Included FP | Included FN |
|---|---|---:|---:|---:|---:|---:|
| Production `screen` | `screen-2026-10-23.1` | 47.7% (21/44) | 2 | 16 | 6 | 0 |
| Candidate `screen_v2` | `screen-2026-11-25.1` | **100.0% (44/44)** | **0** | **0** | **0** | **0** |

#### B. Read-Only Live Corpus Dry-Run (`2,463` Listed Postings + `24` Discovery Candidates)

Executed strictly read-only on EC2 (`jobwatch_agent_readonly`, `default_transaction_read_only = on`, zero writes):

- **Listed postings (`2,463`):** `priority` **21 → 31** (`+47.6%`), `review` **210 → 197**, `excluded` **2,232 → 2,235**.
- **Exact transition matrix:** `14` total verdict changes, **0 regressions**:
  - **11 `review → priority`:** 4 Cambridge Consultants 2027 graduate engineering/AI roles, 3 Marshall Wace 2027 London graduate/quant roles, 2 Ocado Group 2027 engineering graduates, 1 Stripe *Software Engineer, New Grad* (London), 1 Palantir *Software Engineer, New Grad - Query Engines* (London).
  - **1 `priority → excluded`:** iwoca *Growth Associate - M&A Platform*.
  - **2 `review → excluded`:** Ocado Group *Partner Growth Associate / Executive*, Canonical *Junior Ads Specialist*.
- **Discovery candidates (`24`):** **`22/24` (`91.7%`)** freelance AI Trainer roles from `10x Team` dropped cleanly `review → excluded` (`exclude.freelance_ai_trainer`).

Production remains on `screen-2026-10-23.1`; versioned rescreening will only run if approved by the owner.

---

## 5. Deadline Extraction & Calendar Audit

Read-only audit across all `2,463` listed postings found **`96` postings** with **`97` extracted deadline claims** (`0` structured `application_deadline` values; `97` prose sentences). Zero prose deadlines enter the ICS calendar automatically, preserving strict provenance separation (`unstructured_feed_text` requires verification before calendar promotion).

### Audit Findings & `parse_prose_deadline` Prototype Evaluation

Implemented in [`src/jobwatch/timing.py`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/src/jobwatch/timing.py) and evaluated read-only across all `97` live prose claims:

| Classification (`parse_prose_deadline`) | Live Count (of 97) | Empirical Evidence & Verification |
|---|---:|---|
| `explicit_date` | **27** | 17 Graphcore (`2026-11-29`, `Midnight`, `early_closure_warning=true`), 7 Stripe (`2026-11-29 / 11-30 / 12-03 / 12-16`), 2 Ocado (`2026-11-22`), 1 TidalSense (`2026-12-02`, `11:59pm`, `early_closure_warning=true`). **100% precision; `0` start-date false matches** (`Start date: September 2027` stripped cleanly). |
| `missing_year_date` | **11** | 8 Graphcore (`29th November`, including HTML superscript `29<sup>th</sup>` handled via `\s*(?:st|nd|rd|th)?`), 3 DC Thomson (`25th/26th November`, `12pm`/`4pm`). Flagged `missing_year=True`, `candidate_date=None` (never invents year). |
| `rolling_or_no_deadline` | **38** | Cambridge Consultants rolling recruitment notices. |
| `non_application_noise` | **4** | Riverlane (`work to deadline`), Canonical (`on a deadline`), and 2 Jane Street (`deadline-driven`). |
| `unstructured_warning` | **17** | Principally Palantir Lever postings where `"Application deadline"` sits as a standalone heading line separated by a newline from its value — identified as a real multi-line extraction gap in `deadline_evidence()` for future parser revision. |

---

## 6. External Vacancy-First Discovery Sources (Reed.co.uk & Adzuna)

Unattended Workable-wide search remains **disabled** pending permitted-usage clearance. We assessed the documented free-tier developer APIs of **Reed.co.uk** and **Adzuna** as alternative UK vacancy-first discovery mechanisms:

| Criterion | Reed.co.uk Developer API | Adzuna Developer API |
|---|---|---|
| **Auth & Cost** | Free self-serve API key (HTTP Basic Auth); no paid dependency. | Free self-serve `app_id`/`app_key` strictly for personal/evaluation/academic research (commercial use requires paid contract after 14-day trial). |
| **UK Coverage & Freshness** | Strong UK SME, graduate consultancy, and non-US-ATS employer coverage; live search index. | Broad UK aggregator index; fresher for major boards but heavily duplicated across aggregators. |
| **Description & Payload Limits** | Full job description returned via job-details endpoint. | **Truncated short snippets only** (no full job description via free API). |
| **URL Resolution to Official ATS** | Mixed: many listings are recruitment agencies or apply via `reed.co.uk`; `externalUrl` is populated only when employer enables external apply. | **Mandatory `redirect_url`** (`adzuna.co.uk/jobs/land/ad/...`); bypassing redirect violates developer terms. |
| **Persistence & Attribution Terms** | Requires Reed.co.uk attribution; prohibits stale bulk job-board mirroring/warehousing. | Requires visible `"Adzuna"` logo/link (`>=116×23px`) if displayed; explicitly prohibits building a persistent standalone job database without a commercial raw-data license. |
| **Recommended Fit** | **Viable optional ephemeral employer-discovery feeder** (query UK graduate tech keywords periodically, extract employer names + any `externalUrl`, resolve to official ATS boards/careers pages without mirroring Reed descriptions in PostgreSQL). | **Poor fit** (truncated descriptions break deterministic screening, mandatory redirect URLs break official-URL provenance, and persistence terms conflict with historical tracking). |

Neither API was integrated or enabled in production.

---

## 7. Updated Production Resource Measurements & Remaining Limitations

### Production Resource Footprint (`t4g.small`, `2 GiB RAM`, `eu-north-1`)

| Metric | Measured Post-0010 Value |
|---|---|
| Total PostgreSQL `jobwatch` DB size | `155.2 MiB` (`162,748,083 B`) |
| `jobwatch.discovered_boards` (13,749 rows + indexes) | `8.52 MiB` (`8,937,472 B`) |
| `jobwatch.discovery_candidates` (24 rows + indexes) | `384 KiB` (`393,216 B`) |
| S3 compressed logical backup (`pg_dump -Fc`) | `23.70 MiB` (`17 s` runtime) |
| 40-board daily discovery probe (`discover-probe`) | `33 s` wall time, `2.365 s` CPU, `56` requests, `10.56 MiB` transfer, `0` HTTP `429` |
| Monitored boards / Listed postings | `26` monitored boards / `2,463` listed postings |
| CloudWatch alarms & MCP health | `6/6` alarms `OK`; `mcp_healthy=1`, `probe_ok=1`, `restarts=0` |

### Remaining Limitations

1. **Production still uses initial FIFO discovery probe & `screen-2026-10-23.1`:** Prioritized scheduling (`select_probe_batch_prioritized`), automatic source activation (`auto_activation.py`), `screen_v2` (`screen-2026-11-25.1`), and `parse_prose_deadline` are verified locally and await explicit owner approval before any production deployment or rescreening.
2. **Multi-line prose deadline headings:** `deadline_evidence()` currently splits on newlines, causing standalone heading lines like Palantir's `"Application deadline\n..."` to lose the value on the next line.
3. **Directory refresh completeness:** Employer-directory coverage depends on upstream public ATS board lists (`ats-scrapers` CSVs for Greenhouse/Ashby/Lever) and does not cover employers on Workday, Taleo, SuccessFactors, or proprietary portals without a vacancy-first feeder (such as an optional Reed.co.uk employer-discovery adapter).
4. **Workable-wide discovery:** Remains disabled for recurring unattended scheduling; individual verified Workable employer boards (e.g., Stalis) continue operating cleanly via the native Workable adapter.
