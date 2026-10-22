# Working design note — discovery index sprint (22 October 2026)

Branch `feature/discovery-index`, cut from `origin/main` at `8bb5148`.
Baseline: 114 offline tests passing. This note records the intended changes
before implementation; the outcome is in `DISCOVERY_ARCHITECTURE_REVIEW.md`.

## Reproduced findings (local experiments, offline)

| Finding | Reproduction | Result |
|---|---|---|
| Excluded unseen postings discarded | 100-posting synthetic board, 80 excluded titles, `state.merge` | 20 of 100 identities retained |
| Graduate titles excluded | `filtering.classify` on seven titles | "Graduate Engineer", "Technology Graduate Programme 2027", "Junior Cloud Engineer", "Graduate Security Engineer", "Graduate Data Scientist", "Forward Deployed Engineer, New Grad" → `excluded`; "Machine Learning Engineer, Early Careers" → `review` (`early[ -]career\b` misses "careers") |
| Rule change reported as employer change | Patch `classify`, re-merge identical source | `changed` event with fields `priority`, `reason` |
| Judgment changes already in history | Count `data/jobs.json` history | 20 of 49 events are `reason`/`priority`-only |
| Tests coupled to operational data | `test_calendar`, `test_leads`, `test_config` read real `data/`/`config/` | Scheduled scan runs these before collecting |
| Two URL identities | `applications.url_identity` drops fragments; `calendar.safe_url` keeps them | Ledger and calendar disagree on "same vacancy" |
| Daily churn | `git show fd17d6b` | Every posting's `last_seen`, every deadline's `observed_at`, all board timestamps rewritten |

## Intended design

1. **Complete discovery index.** `data/jobs.json` schema 2 stores *every*
   observed posting identity from a successful board, excluded or not. Each
   record separates source fields, observation fields, and a `screening`
   object (`verdict`, stable `codes`, `rules_version`, `input_sha256`, `basis`).
2. **Content-addressed source text.** Description strings move out of
   `jobs.json` into `data/source_text/<aa>/<sha256>.txt` (exact source string,
   immutable, written before the state that references it). Unchanged text is
   never rewritten. `raw` provider payloads are no longer committed; the
   metadata needed for screening (department, team, employment type) is
   extracted instead. Old payloads remain in Git history.
3. **Volatile health split.** Board health and the last-scan summary move to
   `data/scan_health.json`. Per-posting `last_seen` is derived from board
   health while listed and frozen only when the posting goes `not_seen`;
   unchanged deadline evidence keeps its first `observed_at`. Goal: an
   unchanged scan leaves `jobs.json` byte-identical.
4. **Source and screening events separated.** `source_events`
   (`appeared`/`changed`/`not_seen`/`reappeared`) contain source fields only.
   `screening_events` (`rescreened`) record verdict/code changes with cause
   `rules_changed`, `source_changed` or both. Legacy mixed `history` is
   preserved verbatim as `legacy_history`, never reinterpreted.
5. **Screening model.** Structured result with reason codes; uses title,
   department/team and description. Early-career signal plus unknown
   discipline goes to `review`, not `excluded`. Adjacent families (data
   science, security, customer-facing engineering, technology programmes,
   generic "engineer") are capped at `review`.
6. **Agent contract.** `queue`, `show`, `excluded`, `changes`, `health`,
   `rescreen` (read-only preview), `search`, `triage`, `verify`, `eval`,
   `validate-data`, `capabilities`, each with `--json` and a versioned envelope.
   Domain joins (application ↔ posting, triage, verification, freshness) live
   in one module (`opportunities.py`); text output renders the same data.
7. **Owner triage.** `data/triage.json`, validated, locked, atomic,
   idempotent retries, explicit `--replace` to change a decision.
8. **Evaluation.** Regression cases (unit tests) kept separate from an
   author-labelled synthetic diagnostic set and an empty owner-label file
   with a labelling workflow. No performance claims without owner labels.

## Migration and compatibility constraints

- `load_state` reads schema 1 and upgrades in memory; the next write saves
  schema 2. Deterministic and idempotent (schema 2 input is unchanged).
- The migrated data is **not committed on this branch**: the bot keeps
  committing schema-1 `jobs.json` to `main` daily, so a committed migration
  would conflict. After merge, the first scheduled scan migrates.
- Excluded postings never retained before the migration cannot be
  reconstructed. Postings first recorded by the first complete scan of a board
  that had earlier successful scans get `first_seen_basis: "index_backfill"`:
  their true first listing is unknown, and they are not reported as new.
- The scheduled workflow must also stage `data/scan_health.json` and
  `data/source_text/`.
- Existing owner files (`applications.csv`, `verifications.json`,
  `leads.json`) keep their formats.
