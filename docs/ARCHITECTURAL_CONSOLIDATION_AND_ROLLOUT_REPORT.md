# Architectural Consolidation and Automatic Evidence Infrastructure Report

**Date:** 25 November 2026  
**Deployed Production Commit:** `9ee0506b078fc4f8583c13275192bbb5d62dc555` (following `bbb9abbea740a5abedf756f69dc472ae39ea4709`)  
**Held Back (Isolated & Verified Locally Only — NOT Deployed to Production):**
- Calendar explicit-inclusion policy transition ([`docs/CALENDAR_POLICY_TRANSITION_PROPOSAL.md`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/docs/CALENDAR_POLICY_TRANSITION_PROPOSAL.md), [`sql/postgresql/proposals/calendar_deadlines_explicit_only.sql`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/sql/postgresql/proposals/calendar_deadlines_explicit_only.sql))
- Agent-contributed evidence ingestion capability ([`docs/SCHEMA_INSPECTABILITY_AND_AGENT_EVIDENCE.md`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/docs/SCHEMA_INSPECTABILITY_AND_AGENT_EVIDENCE.md), [`sql/postgresql/proposals/0011_agent_evidence_observations.sql`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/sql/postgresql/proposals/0011_agent_evidence_observations.sql), [`sql/postgresql/proposals/agent_evidence_roles.sql`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/sql/postgresql/proposals/agent_evidence_roles.sql), [`src/jobwatch/postgres/agent_evidence.py`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/src/jobwatch/postgres/agent_evidence.py))
- Unattended Workable-wide polling (remains disabled), Adzuna (deferred), Reed (deferred)

---

## 1. Enduring Architectural Principles

Codified in [`AGENTS.md`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/AGENTS.md) and [`docs/ARCHITECTURE_PRINCIPLES.md`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/docs/ARCHITECTURE_PRINCIPLES.md):

- Graduate Job Watch is an **inspectable evidence infrastructure for agents**, not an autonomous personal decision-making system.
- Primary engineering objectives: information acquisition and recall, fidelity to original source evidence, freshness, provenance and historical preservation, explicit representation of uncertainty, efficient expressive retrieval, and coherent inspectable schema design.
- Deterministic operational automation (discovery scheduling, verification, bounded source activation, screening heuristics, deadline extraction) is persisted with full auditability and reversibility; personal interpretation, suitability assessment, prioritisation, and recommendations remain ephemeral session-based agent activities.
- Public evidence ingestion authority is strictly separated from personal owner-action authority (`applications`, `triage`, `calendar_selections`, manual `leads`).

---

## 2. Discovery Scheduling & Automatic Activation Qualification

### A. Scheduling Benchmark (Distinguishing Simulated vs Measured Behaviour)

Upgraded [`experiments/discovery_scheduling_eval.py`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/experiments/discovery_scheduling_eval.py) to track **unique UK employers** and **unique UK vacancies** separately from repeated observations, provider balance, and starvation resistance across 90-day and 180-day windows (`kind = "simulated_benchmark"`):

| Strategy (90d simulated, `3,600` probes) | Unique Boards Explored | Unique UK Employers | Unique UK Vacancies | Repeated UK Obs / 100 Reqs | Provider Share (GH / Ashby / Lever) | New UK Boards Detected (Avg Days) |
|---|---:|---:|---:|---:|---|---|
| `fifo` (pure rotation) | 3,600 | 300 | 1,033 | 7.69 | 44.9% / 31.6% / 23.5% | 3/47 (—) |
| `greedy_priority` (0% explore) | 1,042 | 96 | 345 | 22.90 | 45.8% / 31.6% / 22.7% | 1/46 (—) |
| `prioritized_explore` (`0.35` explore) | 1,866 | 165 | 539 | 26.11 | 38.6% / 35.2% / 26.3% | 50/50 (`0.4d`) |
| **`prioritized_explore` (`0.60` explore — deployed)** | **2,459** | **208** | **710** | **25.70** | **36.3% / 34.9% / 28.8%** | **47/47 (`0.1d`)** |

Over 180 days, `exploration_ratio=0.60` discovers **`406` unique UK employers** and **`1,400` unique UK vacancies** (`+43%` more unique employers and `+51%` more unique vacancies than `0.35`, and `4.2×` greedy priority) while preserving weekly freshness on active UK boards and sub-day detection of newly added boards. `select_probe_batch_prioritized` returns inspectable `selection_reason`, `priority_score`, and component `score_breakdown`.

**Live measured production probe (25 Nov 2026 `11:09–11:10 UTC`, `jobwatch-discover-probe.service`):**
- `scheduler: "prioritized"`, `boards_probed: 40`, `selection_reasons: {"exploration": 24, "priority": 16}` (exact 60%/40% split)
- `provider_probes: {"greenhouse": 21, "lever": 11, "ashby": 8}` (eliminated alphabetical bias)
- `requests_made: 48`, `bytes_read: 6.12 MiB`, `elapsed_seconds: 60`, `CPU: 1.932 s`, `halted_reason: null` (`0` HTTP `429`)
- Discovered **`3` new `active_uk_relevant` boards** and **`4` new UK candidate vacancies** (`28` total `discovery_candidates`).

### B. Bounded Automatic Source Activation Qualification

Implemented in [`src/jobwatch/postgres/auto_activation.py`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/src/jobwatch/postgres/auto_activation.py) and qualified in disposable PostgreSQL 17 ([`tests/postgres/test_auto_activation.py`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/tests/postgres/test_auto_activation.py)):

- **Least-privilege 3-phase systemd pipeline** (`04:50 UTC` daily, preserving existing DB peer auth without widening privileges):
  1. `jobwatch-auto-stage.service` (`jobwatch-owner`): acquires session advisory lock `pg_try_advisory_lock(hashtextextended('jobwatch.auto_activation', 0))`, evaluates deterministic gates (`verified_official_link`, public `careers_url`, non-empty `identity_evidence`, zero unreviewed custom URL hosts, `>=1` UK-relevant vacancy, configurable caps: `<=60` monitored boards, `<=6,000` listed postings, `<=500` vacancies/board, `<=3`/run), stages qualifying `candidate` sources.
  2. `jobwatch-auto-validate.service` (`jobwatch-collector`, chained via `OnSuccess`): executes standard `check_endpoint`, certifying live feed + `absence_safe = True` + vacancy cap.
  3. `jobwatch-auto-activate.service` (`jobwatch-owner`, chained via `OnSuccess`): re-acquires advisory lock + re-checks caps, activates via `activate_source` under `policy:auto-activation-v1`.
- **Concurrency & lifecycle safety:** Proven in PG 17 tests that concurrent runs exit safely (`locked_by_concurrent_run: true`) without exceeding budgets; `deactivate_auto_source` strictly refuses manually curated sources (`PermissionError`), pauses only `auto_activation:*` sources, preserves 100% of historical postings/versions/events, and is reversible via `reactivate_auto_source`.
- **Live measured production pass (`11:10 UTC`):** Evaluated `6` `active_uk_relevant` discovered boards (`elapsed: 1 s` per phase, `locked_by_concurrent_run: false`), safely **deferred all `6/6`** (`staged_count: 0`, `activated_count: 0`) because none yet held `verified_official_link`. All `26` manually verified sources remained untouched.

---

## 3. Independent Screening & Deadline Extraction Evaluation

### A. Screening Qualification (`screen-2026-11-25.1`)

- **44-case regression benchmark** ([`evaluation/expanded_corpus_eval.json`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/evaluation/expanded_corpus_eval.json)): `screen_v1` `21/44` (`47.7%`) → `screen` (`screen-2026-11-25.1`) **`44/44` (`100.0%`)**.
- **Independent held-out evaluation (stratified random sample of `60` live postings, `seed=20261125`, plus exhaustive audit of all `58` UK borderline exclusions):**
  - **Recall on UK borderline exclusions:** **`0 / 58` false exclusions** (`100%` recall — `34` internships/placements, `10` senior/staff/manager, `9` non-software hardware/RF/mechanical, `5` business/finance/design/strategy).
  - **Empirical defects surfaced by held-out audit and fixed before rollout:**
    1. `Stripe — Software Quality Assurance Engineer (Taipei, Taiwan)` was slipping into `review` (`location.unknown`); expanded `FOREIGN` with Taiwan/Taipei and additional international hubs, cleanly removing **8** non-UK postings from `review` → `excluded`.
    2. Plural `"Future Graduates"` missed `EARLY`; expanded to `\bgraduates?\b`.
    3. Kept adjacent families (`data_science`, `security`) in `review` with an explicit, queryable atomic signal `role.software_evidenced_adjacent` rather than inflating `priority`, and added inspectable non-blocking atomic codes `year.2027_explicit`, `level.inclusive_degree_ladder`, `level.benign_tenure_or_cap_only`, `level.desirable_experience_only`, and `level.entry_description_only`.

### B. Deadline Extraction Qualification (`normalizer-v2`)

- **Full-corpus recall audit (`2,463` listed descriptions):** `0` postings with explicit deadline cues were completely missed; **`13` multiline heading splits** (`1` Riverlane `"Deadline\nPlease submit your application by November 6th, 2026"` + `12` Palantir `"Application deadline\nWe accept applications on an ongoing basis."`) and HTML superscript formatting (`6<sup>th</sup>, 2026` / `29<sup>th</sup> November`) were identified and fixed in [`src/jobwatch/timing.py`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/src/jobwatch/timing.py).
- **Live corpus result (`normalizer-v1` → `normalizer-v2`):**
  - `non_application_noise` claims reduced to **`0`** (Riverlane/Canonical/Jane Street work-deadline phrases eliminated).
  - `explicit_date` prose claims: **`28`** (`100%` precision, `0` start-date false matches).
  - `rolling_or_no_deadline` prose claims: **`38 → 49`** (all multiline Palantir ongoing-basis statements recovered).
  - `missing_year_date`: **`11`** (explicitly flagged, zero invented years).
  - Added `analyze_posting_deadlines()` surfacing multi-claim `has_date_conflict` explicitly without silent resolution.

---

## 4. Production Rollout Evidence & Backup OOM Fix

### Rollout Summary (`i-0a21a9da641e3f22e`, `t4g.small`, `eu-north-1`)

| Verification Check | Measured Production Result |
|---|---|
| Final deployed commit | `9ee0506b078fc4f8583c13275192bbb5d62dc555` (`app` SHA-256 `14f98425…`) |
| Pre-maintenance S3 backup (`11:07:23Z`) | `backups/2026/11/25/20b20ea642764461a3de6e7fe8cc56cb.dump` (`23.70 MiB`, `VersionId: D6PvZyZoXeOnd7nauz7et_V2p5GRVVzV`) |
| Schema migrations | Strictly `1..10` (`Database schema is up to date`; proposal `0011` NOT applied) |
| Calendar policy & feed | `/etc/jobwatch/calendar-deadlines.sql` untouched (`sha256: 509950a7…`); `deadlines: 12`, `actions: 0` unchanged |
| Non-destructive rescreen + renormalization | `26/26` monitored sources `rescreened` under `screen-2026-11-25.1` & `normalizer-v2`; `113` `normalizer/renormalized` events + `329` `screening/rescreened` events; **all `2,929` `normalizer-v1` + `1,013` legacy versions 100% preserved** alongside `2,595` `normalizer-v2` versions |
| Post-rescreen listed verdicts (`2,463`) | `priority`: **21 → 30** (`+10` genuine UK core grad roles, `-1` iwoca commercial role), `review`: **210 → 190**, `excluded`: **2,232 → 2,243** |
| **Critical catch & fix: streaming backup** | Preserving `6,537` immutable `posting_versions` (`223.9 MiB` DB) pushed the legacy in-memory `database_backup.py` `fingerprint()` + `archived_sequences()` to `1.24 GiB` RSS (OOM-kill on `2 GiB` host). Rewrote both to stream via a server-side named cursor (`itersize=200`, per-row SHA-256) and line-by-line `pg_restore` (keeping full backward compatibility for older manifests). **Peak RSS dropped `10.5×` to `120.6 MiB` (`123,464 KB`) in `17.38 s`!** |
| Post-rollout S3 backup (`11:16:33Z`) | `backups/2026/11/25/22815587695c4b23bc28d8d5cde39d41.dump` (`36.25 MiB`, `s3_checksum_and_version_confirmed: true`, `VersionId: jbHQKg9uAb_8R84CUsycKIgbNWGuMDB7`) |
| Host headroom & health | RAM available: `1,214 MiB / 1,834 MiB`; PG volume: `458 MiB / 16 GiB (4%)`; `jobwatch-backup-health` & `jobwatch-mcp-health`: `success`; `0` failed units; **all `6/6` CloudWatch alarms `OK`** |
| Active timers (`8`) | `backup`, `backup-health`, `mcp-health`, `calendar`, `collect`, `discover-refresh` (Sun `04:00`), `discover-probe` (daily `04:30`), `auto-activate` (daily `04:50`) |

---

## 5. Summary of Proposals Awaiting Owner Review

1. **Calendar explicit-inclusion transition:** Full 12-event audit and transition plan in [`docs/CALENDAR_POLICY_TRANSITION_PROPOSAL.md`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/docs/CALENDAR_POLICY_TRANSITION_PROPOSAL.md). All 12 currently published events are past Oct/Nov 2026 dates that entered via default inclusion (including 2 unrelated Monzo postings and 1 duplicate Riverlane lead), with `0` rows in `calendar_selections`, `applications`, or `triage`.
2. **Agent-contributed evidence ingestion:** Full least-privilege authority model (`jobwatch_agent_evidence`), SSRF/rate/payload guards, contradiction preservation, corroboration workflow, and MCP architecture review in [`docs/SCHEMA_INSPECTABILITY_AND_AGENT_EVIDENCE.md`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/docs/SCHEMA_INSPECTABILITY_AND_AGENT_EVIDENCE.md), verified end-to-end on PostgreSQL 17 in [`tests/postgres/test_agent_evidence_pg.py`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/tests/postgres/test_agent_evidence_pg.py).
3. **Schema audit follow-up (`discovery_candidates` excerpt limit):** `93.7%` (`2,308/2,463`) of live descriptions exceed the current `2,500`-char `description_excerpt` cap (`p50 = 5,062`, `46` deadlines and `484` eligibility clauses appear after char `2,500`). Raising the cap (or storing compressed snapshots for `priority`/`review` candidates) is recommended as a future non-destructive migration.
4. **Remaining limitations:** Workable-wide polling remains disabled; Adzuna is deferred and Reed remains an optional future employer-discovery feeder; `normalizer-v2` preserves all historical `normalizer-v1` versions (`+68.7 MiB` DB storage, well within the `16 GiB` PG volume now that backups stream at `120.6 MiB` peak RSS).
