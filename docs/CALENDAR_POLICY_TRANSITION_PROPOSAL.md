# Calendar Authority and Inclusion-Policy Transition Proposal

**Status:** Proposal & live impact analysis ONLY — **NOT deployed to production.**  
**Proposed policy file:** [`sql/postgresql/proposals/calendar_deadlines_explicit_only.sql`](file:///home/kerem/Desktop/Job/grad-job-tracker-starter-updated/grad-job-tracker/sql/postgresql/proposals/calendar_deadlines_explicit_only.sql)  
**Reuses existing table:** `jobwatch.calendar_selections` (migration `0007`, zero parallel calendar architecture).

---

## 1. Current Deployed Production State (Read-Only Snapshot, `2026-11-25T10:10:19Z`)

- **Deployed policy (`/etc/jobwatch/calendar-deadlines.sql`):** All-evidence default (`SELECT posting_key FROM research_postings UNION ALL SELECT lead_key FROM agent_leads`).
- **Explicit rows in `jobwatch.calendar_selections`:** **`0`**
- **Owner `applications` rows:** **`0`** | **Owner `triage` rows:** **`0`**
- **Published ICS feeds (`/run/jobwatch-calendar-status/status.json`):** `deadlines.ics` = **`12` events** (`warnings = 0`), `actions.ics` = **`0` events**.

### All 12 Currently Published Deadline Events & Exact Transition Impact

None of the 12 current events have an explicit row in `calendar_selections`, `applications`, or `triage`. Every single one entered the feed solely via the legacy all-evidence default:

| # | Identity Key(s) | Company | Title | Published Deadline | Why Currently Published | Explicit-Only Transition Effect (`0` selections) |
|---:|---|---|---|---|---|---|
| 1 | `ashby:incident:743b1624…` (+ linked `vacancy:c3b34d7c…`) | incident.io | Product Engineer Graduate | `2026-10-29` *(past)* | Default policy (lead + posting) | `qualifies` → **`not_selected`** (removed from ICS; UID preserved in manifest) |
| 2 | `greenhouse:monzo:8232726` | Monzo | **Associate Data Scientist - Intern** | `2026-10-31T20:00:00-04:00` *(past, intern)* | Default policy (structured feed field) | `qualifies` → **`not_selected`** (unintended intern removed) |
| 3 | `greenhouse:monzo:8358652` | Monzo | **Property & Facilities Lead** | `2026-11-20T16:00:00-05:00` *(past, non-tech)* | Default policy (structured feed field) | `qualifies` → **`not_selected`** (unintended non-tech role removed) |
| 4 | `greenhouse:riverlane:5000685101` | Riverlane | Engineering Graduate Scheme 2027 | `2026-11-06` *(past)* | Default policy (verified posting) | `qualifies` → **`not_selected`** |
| 5 | `vacancy:298d9ea4…` *(unlinked Riverlane URL duplicate)* | Riverlane | **Engineering Graduate Scheme 2027 (duplicate)** | `2026-11-06` *(past, duplicate of #4)* | Default policy (unlinked legacy lead URL) | `qualifies` → **`not_selected`** (duplicate eliminated) |
| 6 | `vacancy:hsbc-cyber-graduate-sheffield-2027` | HSBC | Cyber Graduate — Sheffield | `2026-11-01` *(past)* | Default policy (official lead) | `qualifies` → **`not_selected`** |
| 7 | `vacancy:alloyed-software-graduate-8467771` | Alloyed | Software Engineering — Graduate & Internship Opportunities | `2026-11-01` *(past)* | Default policy (official lead) | `qualifies` → **`not_selected`** |
| 8 | `vacancy:amex-ai-grad-26013776` | American Express | Campus Full-Time AI Engineer — 2027 (Burgess Hill) | `2026-11-06` *(past)* | Default policy (official lead) | `qualifies` → **`not_selected`** |
| 9 | `vacancy:amex-software-grad-26013783` | American Express | Campus Full-Time Software Engineer — 2027 (Burgess Hill) | `2026-11-06` *(past)* | Default policy (official lead) | `qualifies` → **`not_selected`** |
| 10 | `vacancy:sse-it-ot-graduate-559755` | SSE | IT & OT Graduate Programme | `2026-11-01` *(past)* | Default policy (official lead) | `qualifies` → **`not_selected`** |
| 11 | `vacancy:jpmc-swe-2027-210774781` | JPMorganChase | 2027 Software Engineer Programme — London / Glasgow | `2026-11-01` *(past)* | Default policy (official lead) | `qualifies` → **`not_selected`** |
| 12 | `programme:civil-service-digital-fast-stream-2027` | Civil Service Fast Stream | Digital Fast Stream 2027 | `2026-11-05` *(past)* | Default policy (official lead) | `qualifies` → **`not_selected`** |

**Measured dry-run projection count on live production DB:**
- Current `/etc/jobwatch/calendar-deadlines.sql`: **`12`** projected deadline events.
- Proposed `calendar_deadlines_explicit_only.sql` (`SELECT NULL::text AS evidence_key WHERE false`) with current `0` selections: **`0`** projected deadline events.
- **Key observation:** All 12 currently published events already have **passed** deadlines (`2026-10-29` through `2026-11-20`, still inside the 30-day past lookback window as of `2026-11-25`), and 3 of the 12 are demonstrably unwanted noise (Monzo *Property & Facilities Lead*, Monzo *Associate Data Scientist - Intern*, and an unlinked duplicate of Riverlane's scheme). No active future owner-selected deadline is lost by transitioning cleanly!

---

## 2. How Explicit Identity-Based Inclusion Works (Zero Parallel Architecture)

Existing `reports.select_deadlines` already computes:

```
published = (policy_keys ∪ calendar_selections[mode='include']) − calendar_selections[mode='exclude']
```

Replacing `/etc/jobwatch/calendar-deadlines.sql` with `SELECT NULL::text AS evidence_key WHERE false` makes `policy_keys = ∅`, so:

1. **No auto-inclusion of new vacancies:** New postings/leads (even with structured `application_deadline` fields, such as Monzo non-tech roles) remain `status = 'not_selected'` and never enter `deadlines.ics` unless explicitly inserted into `jobwatch.calendar_selections` (`mode = 'include'`).
2. **Automatic updates for selected vacancies:** `calendar_selections` binds **vacancy identity** (`posting_key` or `lead_key`), NOT calendar dates (`approved_deadline` is audit-display metadata only). Whenever a selected vacancy receives a well-supported deadline update (e.g., an updated structured feed `application_deadline` with superseded prior claim, a verified lead deadline correction, or a superseding `verify_posting_deadline`), the deterministic hourly/triggered publisher automatically emits the updated date under the **exact same stable UID** (`calendar.uid('deadline', identity)`) and increments **`SEQUENCE`**.
3. **Unresolved conflicts (safe omission, never silent newest-wins):** If a selected vacancy has two or more distinct active qualifying deadline dates, `reports.calendar_events` refuses to pick the newest date: it **omits** the event from `deadlines.ics`, marks the manifest entry `active=false` with `SEQUENCE+1`, emits an explicit warning (`Conflicting explicit deadlines omitted for <uid>`), preserves the `calendar_selections` row, and `calendar_selection.describe` reports `status = 'conflicting'` with all competing claims intact for inspection.
4. **Lost or uncertain supporting evidence:** If a previously published selected deadline loses supporting evidence (e.g., wording changes to rolling, or structured field disappears without an active verification), the publisher cleanly removes the event (`active=false`, `SEQUENCE+1`, UID retained in manifest), while keeping the `calendar_selections` row intact (`status = 'insufficient'` or `'missing'`). Once well-supported evidence returns, the exact same UID re-activates with `SEQUENCE+2`.

---

## 3. Proposed Rollout Procedure (Requires Explicit Owner Approval)

1. Owner reviews the 12 events above and confirms whether any (0..12) should be pre-seeded into `jobwatch.calendar_selections` (`mode='include'`) before cutover (recommended: **0**, since all 12 have passed, and instead explicitly select upcoming 2026/2027 targets such as Graphcore/Stripe/TidalSense once verified).
2. Take a pre-change backup (`jobwatch-backup.service`).
3. Replace `/etc/jobwatch/calendar-deadlines.sql` with `sql/postgresql/proposals/calendar_deadlines_explicit_only.sql` (`0640 root:jobwatch-db`).
4. Run `jobwatch-calendar.service`, confirm clean manifest update (`SEQUENCE` advanced on deactivated UIDs, 0 errors), and verify `run_report(kind="calendar_selection")` reflects `not_selected` for unselected vacancies and `qualifies` for explicitly selected ones.
5. Update `docs/CALENDAR_SELECTION.md` and `owner_mcp` tool description to note that default inclusion is now explicit-only.
