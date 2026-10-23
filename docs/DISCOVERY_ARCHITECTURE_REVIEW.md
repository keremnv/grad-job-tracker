# Discovery architecture review — overnight sprint, 22 October 2026

Branch `feature/discovery-index`, cut from `origin/main` at `8bb5148`. Not merged.
Working design note: `DISCOVERY_DESIGN_NOTE.md`. All numbers below were
measured during the sprint; nothing is estimated unless it says so.

## 1. Architecture before and after

**Before** (main at `8bb5148`):

```
feeds.collect_board ─► filtering.classify (title regex) ─► state.merge
     excluded & previously unseen ⇒ dropped (never stored)
     history[] mixed source edits with priority/reason changes
data/jobs.json (schema 1): candidates only, inline description + raw payload,
     board health, last_seen per posting rewritten every scan
agents: read jobs.json directly and join it to applications.csv themselves
```

**After** (this branch):

```
feeds.collect_board (+ department/team/employment type, non-vacancy accounting)
   │
   ▼
state.merge ── records EVERY identity from a successful board
   ├─ source fields + observation fields (first_seen, first_seen_basis, feed_status, missing_since)
   ├─ source_events: new / changed / not_seen / reappeared / backfilled   (employer side only)
   ├─ filtering.screen ─► screening {verdict, family, codes, rules_version, basis, input_sha256}
   └─ screening_events: rescreened {cause: rules_changed | source_changed | legacy_migration …}
   │
   ├─ data/jobs.json        (schema 2; unchanged scan ⇒ byte-identical)
   ├─ data/jobs-health.json (board health + last-scan summary; the only daily churn)
   └─ data/jobs-text/aa/<sha256>.txt  (exact source descriptions, immutable)

opportunities.py ── the single join: index × applications.csv × triage.json
                    × verifications.json × board health  ─► views with provenance sections
commands.py ── capabilities · queue · recent · show · excluded · changes · health ·
               rescreen (preview) · search · eval · validate-data · triage · verify
               (--json ⇒ envelope jobwatch.cli/1; text output renders the same data)
```

## 2. Implemented versus proposed

Implemented, tested and committed:

| Stage | Delivered |
|---|---|
| 0 | Design note with reproduced findings (`f5739bd`). |
| 1 | Complete discovery index, content-addressed text, health split, deterministic schema-1 migration, backfill marking (`4dc28aa`). |
| 2 | Structured screening with stable codes, department/description signals, adjacent families, regression fixture (`ebb8c55`, `f36845a`). |
| 3 | Separate source and screening event streams with causes; legacy history preserved and labelled (`4dc28aa`). |
| 4 | Agent contract, opportunity views, read commands, text rendering (`8b0dd2d`). |
| 5 | Private owner triage (`8b0dd2d`). |
| 6 | Evaluation harness, synthetic set, blind owner-labelling workflow, BM25 search/baseline (`ce45429`, `8107b26`). |
| 7 | Operating contract `AGENTS.md`, `CLAUDE.md`, short README, history moved (`7949653`). |
| 8 | Unit tests independent of operational data, `validate-data`, unified URL identity, validated `verify` writes, churn reduction, explanation derivation (`8cf0316`, `cb4e791`, `f36845a`, `8b0dd2d`). |

Proposed only (not built): automatic retention tuning, a `leads` write command,
a public/private repository split, a replay demo, model-based screeners.

## 3. Decisions and why

- **Evolve `jobs.json` rather than add a parallel index.** One record per
  identity avoids two files disagreeing about what exists. Readers use small
  accessors (`verdict_of`, `last_seen`, `description_of`) that also accept
  schema-1 dicts, so the ledger, weekly report and calendar kept working.
- **Content-addressed text in a directory, not inline or one big JSON.**
  Unchanged descriptions never rewrite any blob; a new description adds one
  small file; texts are written before the state that references them, so a
  crash leaves only harmless unreferenced files, removed on the next save. The
  stored string is the exact source string, so `description_sha256` matches the
  hashes already used in `verifications.json`.
- **Drop committed `raw` payloads.** Department/team/employment type are
  extracted instead (also from schema-1 `raw` during migration). Raw payloads
  remain recoverable from Git history before the migration commit.
- **Health in its own file; `last_seen` derived.** While listed, a posting's last
  observation equals its board's `last_success`; it is stored only when the
  posting goes `not_seen`. Unchanged deadline evidence keeps its first
  `observed_at` (this field now means "first observed").
- **Screening stores codes, not prose.** Explanations are derived from codes on
  read, so wording edits cannot diverge from stored judgments. Legacy records
  keep their original text until rescreened.
- **Scans rescreen everything.** Observed postings are rescreened every scan;
  stored records on failed or disabled boards and `not_seen` records are
  rescreened from stored fields when `RULES_VERSION` changes. `rescreen` is
  a read-only preview: `jobs.json` keeps a single writer (the scheduled scan).
- **Migration is not committed on this branch.** The bot commits schema-1
  `jobs.json` to `main` daily; a committed migration would conflict. After
  merge, the first scheduled scan migrates (load upgrades in memory; save writes
  schema 2).

## 4. Schemas and CLI contracts

**`data/jobs.json` record (schema 2)** — source: `company, provider, board,
external_id, title, location, url, salary, country, countries, department,
team, employment_type, published_at, source_dates, description_sha256,
description_chars, deadlines`; observation: `first_seen, first_seen_basis
(observed | index_backfill | legacy_retained), feed_status (listed | not_seen),
missing_since, last_seen (only when not_seen)`; judgment: `screening {verdict,
family, codes, rules_version, basis (full | title_metadata_only | legacy),
input_sha256, screened_at}`; history: `source_events[], screening_events[]
(last 20), legacy_history[]`; plus the existing `application_verification`.

**`data/jobs-health.json`** — `{schema: 1, boards: {key: {status, last_attempt,
last_success, fetched_at, count, source_records, skipped_non_vacancies,
screening, error, index_complete_since}}, last_scan: {at, started_at,
rules_version, events (compact), failures}}`.

**`data/triage.json`** — `{schema: 1, decisions: {posting_key: {decision,
reason_code, note, revisit_on, decided_at, recorded_by}}}`; decisions
`interested | dismissed | later`; reasons `not_engineering, not_entry_level,
location, eligibility, employer, role_fit, duplicate, closed_or_expired,
already_handled, other`.

**`data/verifications.json`** — unchanged shape, plus optional `verified_by`
(`owner | agent | agent_on_owner_instruction`) on records, deadlines and
description checks.

**Envelope** — `{schema: "jobwatch.cli/1", command, ok, generated_at, data,
warnings[{code, message, …}], errors[{code, message, …}]}`. Exit 0 ok, 2 error
(nothing written). `capabilities --json` lists every command with its
authorisation class, all reason codes, error codes and warning codes.
Error codes: `invalid_argument, unknown_opportunity, ambiguous_identity,
triage_conflict, verification_conflict, invalid_state, lock_held,
concurrent_edit, io_error`.

**Opportunity view (`show`)** — `source` (feed facts incl. feed deadlines with
`provenance: feed_observation`), `observation` (incl. board status/staleness),
`judgment` (incl. `current_rules`), `verification` (incl. deadlines with
`provenance: source_verification`, `verified_by`), `owner` (`action_state`,
triage without note, application `{job_key, stage, link_basis}`), `unknowns`
(e.g. `eligibility_unverified, application_form_status_unknown,
deadline_unknown, uk_location_unknown, entry_level_unknown, closure_unknown,
first_listing_date_unknown`), `history`. `--source-text` adds
`untrusted_source_text {trust: untrusted_employer_content, handling, source_url,
sha256, text}`.

**Action states** — `unreviewed, revisit_due, interested, preparing` (queue
defaults) and `later, dismissed, applied, closed`. An application stage outranks
triage; a dismissed triage on an active application raises
`triage_application_conflict`.

**Identity** — posting key (strongest); exact URL (scheme/host/port normalised,
path, query and fragment kept); loose URL (fragment dropped) used only to refuse
ledger near-duplicates and to warn `possible_application_match`. The ledger and
calendar now call these shared functions; their behaviour is unchanged.

## 5. Migration and backfill

1. `load_state` reads schema 1, moves inline descriptions into the text store,
   extracts metadata from `raw` and drops it, stores the old judgment as
   `rules_version: legacy-schema-1`, renames `history` to `legacy_history`
   (verbatim), sets `first_seen_basis: legacy_retained`, and marks every legacy
   board `index_complete_since: null`.
2. The first successful schema-2 scan of each such board records every posting
   it has not seen before as `index_backfill` (event `backfilled`, not `new`),
   then sets `index_complete_since`. Later newcomers are `observed`.
3. Every legacy judgment produces one `rescreened` event with cause
   `legacy_migration`.

Measured on scratch copies of the real `data/jobs.json`: two independent
migrations produced byte-identical output; re-saving the migrated state was a
no-op. Limitations: excluded postings never retained before the migration cannot
be reconstructed; their true first listing date is unknown and they are excluded
from "new" in `weekly` and `recent`. The 20 legacy history events that changed
only `reason`/`priority` stay in `legacy_history`, labelled
`legacy_mixed` by `changes`, and are never reinterpreted as source facts.

## 6. Source-versus-judgment provenance

- `changed` events can only contain `SOURCE_FIELDS` and `deadlines`.
- A rule change with identical source yields only `rescreened` (cause
  `rules_changed`); test `test_rule_change_with_identical_source_is_a_screening_event_only`.
- An employer edit yields `changed` plus, if the verdict/codes change,
  `rescreened` with cause `source_changed`.
- A failed board's records keep their source fields byte-for-byte; they may be
  rescreened, which is a judgment change only.
- The public report separates "Source changes" from "Screening changes" and
  states that screening changes are not employer edits.

## 7. Screening behaviour and known false negatives

Rules version `screen-2026-10-22.1`. The seven example titles: "Junior Cloud
Engineer" and "Machine Learning Engineer, Early Careers" → `priority`; "Graduate
Engineer", "Technology Graduate Programme 2027", "Graduate Security Engineer",
"Graduate Data Scientist", "Forward Deployed Engineer, New Grad" → `review` with
`role.discipline_unknown`, `role.adjacent_family` or `role.customer_facing`.
All previous negative tests still pass. 27 pinned regression cases live in
`tests/fixtures/screening_regressions.json`.

Behaviour changes visible on real data (rescreen preview of the 54 retained
postings): three Palantir "Forward Deployed … Engineer, New Grad" UK roles move
`priority → review` because of `role.customer_facing`. This is a deliberate,
reviewable choice (see questions).

One-off live check (§12) of 618 postings: 557 excluded, 53 review, 8 priority.
Excluded early-career-looking titles were internships/placements, non-UK
locations, electronics/RF/mechanical disciplines or non-engineering roles.
Questionable exclusions found: "Technology Scholarship Scheme (2027 start)"
(`exclude.no_engineering_signal`) and the graduate electronics/RF roles
(`exclude.non_software_discipline`, deliberate). All are inspectable with
`excluded`.

Known weaknesses from the synthetic set: "Graduate Developer Advocate" is
`priority` (non-engineering work in a software title); "Software Engineer II"
(not entry level) and "Graduate Software Engineer" in Dublin (Ireland is not in
the foreign-location list) stay in `review`; "Software Engineer I" is not
recognised as entry level.

## 8. Evaluation results and their limits

`eval run --baseline` on `evaluation/synthetic_cases.json`: 45 cases, labels
17 relevant / 17 not relevant / 11 uncertain, **written and labelled by the
sprint author (an AI agent) before running the screener, not reviewed by the
owner**. Results: relevant kept (not excluded) 17/17 = 1.0; relevant as
priority 0.706; not-relevant excluded 0.647; six not-relevant cases kept in the
queue (listed in §7 and the output); queue precision@5/10/20 = 0.75 (4 judged) /
0.889 (9) / 0.929 (14); BM25 baseline precision@5/10/20 = 0.8 / 0.778 / 0.733.
These numbers show only that the rules behave as their author intended on cases
their author wrote; they are **not** evidence of real-world recall or precision.

Owner and triage datasets: **pending** — no owner labels or triage decisions
exist. `eval template` writes a blind stratified sample (verdicts hidden) to
`.local-reports/`; `eval run --dataset owner --labels PATH` scores it;
`--dataset triage` uses `interested` and relevance-reason dismissals only.
Unlabelled postings are never negatives; `uncertain` is excluded from
denominators.

## 9. Privacy and concurrency

- Triage notes, ledger notes and CV versions never appear in normal output,
  the public report or Actions logs; tests assert this. `triage list
  --include-notes` is the only path to notes.
- `queue`, `excluded`, `changes` and `search` never return description text;
  `show --source-text` returns it labelled as untrusted.
- Owner files use O_EXCL lock files (`*.json.lock`, ignored by Git), file-identity
  snapshots and atomic replace; a held lock or a mid-command edit is refused
  (`lock_held`, `concurrent_edit`) without writing. Identical retries return
  `unchanged`; changing a decision requires `--replace`.
- Single writer per file (see `AGENTS.md` §5). Scans protect `triage.json`,
  `applications.csv`, `leads.json` and `verifications.json` as outputs.
- `eval template` output must be inside `.local-reports/` and is mode 0600.
- The text store contains public employer descriptions only. It is new
  committed content (≈5 MB on first post-merge scan) in the private repository.

## 10. Rejected alternatives

SQLite/PostgreSQL (hundreds of records; Git diffs are the audit trail); keeping
`raw` payloads (largest field, rarely needed, in history); one large JSON text
store (rewrites a multi-MB blob for each new text); storing texts only for
non-excluded postings (makes rescreening excluded postings impossible, the core
requirement); rewriting legacy history into separate streams (would invent
precision); committing the migrated data on this branch (conflicts with daily
bot commits); per-posting `last_seen` (daily churn of every record); MCP, LLM or
embedding classification (out of scope by instruction).

## 11. Later Jev or model integration

The boundary is `screen(inputs) -> {verdict, family, codes, rules_version,
basis, input_sha256}`. A model-based screener should:
implement the same signature with its own `rules_version` (e.g. `jev-…`) and a
`model.*` code namespace; be compared through `evaluation.evaluate(cases,
screener=…)` (no change to labels, identity or evidence handling); and be
introduced by bumping the version so scans record `rescreened` events with cause
`rules_changed`. `stored_inputs()` gives the exact inputs the deterministic rules
saw, and `input_sha256` makes cached model outputs addressable. Running
deterministic rules and a model side by side (two `screening` objects) would be
a schema change worth deciding deliberately.

## 12. Tests and smoke checks performed

- Offline suite: baseline 114 passed; final **158 passed**
  (`.venv/bin/python -m unittest discover -s tests -v`). Three live-data tests
  were replaced by synthetic equivalents; one was removed in favour of
  `validate-data`. The suite also passes in a copy of the repository with no
  `data/` or `config/` directory.
- Real-data read-only checks: `health`, `queue`, `excluded`, `changes`,
  `rescreen`, `show`, `search`, `validate-data` against the working tree (no
  writes; `git status` clean of data changes afterwards).
- Churn, scratch copy of the real state with a replayed collector: an unchanged
  scan changed **0** lines of `jobs.json`, 58 lines of `jobs-health.json` and 52
  lines of the rendered report. For comparison, the real scan commit `fd17d6b`
  changed 216 lines of `jobs.json` and 86 of the report.
- **One controlled live read-only scan** (2026-10-22T21:32:33Z) of the nine
  configured public boards into scratch paths only: 9/9 boards succeeded, 618
  postings (Palantir 300, Cambridge Consultants 91, Monzo 73, Scott Logic 43,
  Riverlane 36, incident.io 34, hyperexponential 33, Marshall Wace 5, Tracebit
  3), 618 indexed (`feed == indexed` on every board), department/team populated
  on all 618, 564 `index_backfill` + 54 `legacy_retained`. Text store 525 files,
  4,958,959 bytes; `jobs.json` 1.40 MB (1.30 MB after the explanation change, by
  re-saving the same scratch state); health 176 KB on that first run (618 compact
  events, one-off). Production `jobs.json`, `verifications.json` and
  `applications.csv` SHA-256 hashes were verified unchanged afterwards. No
  Actions were dispatched.
- End-to-end CLI scenarios on synthetic data (scratch workspace): collection
  with relevant and excluded postings; provider failure after success (no
  absences, index bytes unchanged); rule change with identical source (only
  `rescreened`); queue/show/excluded; triage then queue; application join
  without duplicate recommendation; retry `unchanged`, `triage_conflict`,
  `lock_held`; JSON `invalid_state` error and `board_stale` warning; evaluation;
  `weekly` and `calendar export` (1 deadline + 1 follow-up). All behaved as
  specified.
- Workflow: `scan.yml` parsed as YAML; its new staging loop was dry-run in a
  scratch Git repository. **No GitHub Actions run of the modified workflow has
  happened.**

## 13. Remaining bugs, risks and open design questions

- The first scheduled scan after merge produces a large commit (≈5 MB text,
  ≈1.3 MB index, 600+ backfill events in the health file). Expected, one-off.
- The modified `scan.yml` staging has not run on GitHub.
- ~~`--as-of` also sets `decided_at`/`verified_at` defaults on write commands~~
  Resolved in §15: write commands refuse `--as-of`.
- `verifications.json` is reformatted (sorted keys) on its first `verify` write:
  a one-off noisy diff.
- Records on failed/disabled boards are rescreened only when `RULES_VERSION`
  changes; a rule edit without a version bump reaches them only when observed
  again (observed ones show cause `rules_changed_unversioned`).
- ~~Text retention: released 180 days after a posting goes `not_seen`~~
  Resolved in §15: no age-based expiry.
- Calendar export now ignores feed deadlines of excluded postings unless the
  posting has a verification record (previously all retained postings counted).
- Location rules now cover common European/North American/Asian locations
  (§15) but remain a list; level markers such as "I"/"II" are still not
  interpreted ("Software Engineer II" stays `review` with `level.unknown`).

## 14. Candidates to simplify or remove later

- Schema-1 fallbacks (`verdict_of`'s `priority` path, `description` in-memory
  path) once `main` has migrated.
- `legacy_history` (≈200 KB, frozen) once its information is no longer useful;
  Git keeps it.
- `recent` overlaps with `queue --since`.
- The static `CAPABILITIES` list duplicates argparse metadata.
- `search` and the BM25 baseline, if they see no use.
- The separate health file, if daily churn stops mattering.
- `verify description` and the requirements report, which serve a small
  sample-based CV analysis.

## 15. Hardening review (2026-10-23)

Owner decisions applied: forward-deployed graduate roles stay reviewable;
adjacent families stay in the index and are queued only with early-career
*and* software-engineering evidence; descriptions are kept in the private
repository without age-based expiry; triage stays in the private repository;
`open_verified` still needs owner authorisation; `--as-of` is read-only.

- **Verification freshness.** `verify status|deadline|description` take a
  required `--verified-at`. Same result and same check time: `unchanged`.
  Same result, later check: `refreshed` (timestamp and observer move forward).
  Same result, earlier check: `unchanged` (never moves back). A different
  status still needs `--replace`.
- **Write clock.** `triage set|clear`, `verify *` and `eval template` refuse
  `--as-of` (`invalid_argument`, nothing written). Tests inject the clock;
  `--as-of` is validated as a timezone-aware timestamp for reads.
- **Durability.** Write order is texts → index → health → orphan removal →
  report; each file is atomic. `tests/test_durability.py` interrupts the first
  migration before every file replacement and during orphan removal: every
  intermediate state loads and the next scan converges with no false `new`
  events. Writing health before the index would be unsafe
  (`index_complete_since` ahead of an incomplete index turns the backfill into
  "new" postings), so the order is pinned by test. Interrupting the first
  migration between index and health loses the schema-1 board health (it
  lived in the old index; Git history keeps it); the next scan restores it.
  `scan` re-reads its outputs after saving and exits 2 on any mismatch,
  missing text or unexpected exception.
- **Scan exit codes and workflow.** 0 = written; 1 = written, some or all
  boards failed (previously "all failed" was 2 although health and report
  were written); 2 = do not commit. `scan.yml` records the exit code and
  commits only on 0/1, so an interrupted write on the runner is discarded and
  the repository keeps its last consistent commit. A push rejected by a
  concurrent owner push is replayed once with `git pull --rebase` (owner
  commits never touch scan files); a conflict fails the job without forcing.
- **Text store.** Each indexed record keeps its current description (and the
  previous one after its latest change), including `not_seen` records, so
  rescreening always has the full evidence. Deleting a text file from the
  working tree does not remove it from Git history; the private repository
  history retains every committed description.
- **Screening (`screen-2026-10-23.1`).** Clearly non-UK locations (Ireland,
  Netherlands, Spain, Switzerland, Nordics, Poland, Israel, more US/Canadian
  cities, `US`) are excluded unless a UK signal is present ("London or Dublin"
  stays `review`; "Belfast, Northern Ireland" stays UK). "New South Wales" and
  "New England" no longer count as UK. Developer advocate/relations titles are
  `role.customer_facing` (review, not priority). Adjacent, generic and
  uncategorised titles with an early-career signal need software evidence
  (software, programming, a language, backend/frontend, ML, CS, …) in the
  title, department/team or description, or they are excluded with
  `exclude.no_engineering_evidence`; unretained or empty text stays `review`.
  On the 618-posting scratch index (main vs branch): no new non-UK posting is
  kept; Monzo "Software Engineer III, Barcelona" (kept on `main`) is now
  excluded; three Cambridge Consultants titles (two technology-strategy
  analysts, a healthcare design engineer) return to excluded. Synthetic set:
  relevant kept 1.0 (unchanged), not-relevant excluded 0.647 → 0.765.
