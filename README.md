# Graduate Job Watch — UK 2027

Private, personal, **read-only vacancy discovery** and application tracking for
UK graduate software, backend, infrastructure, systems and applied ML roles.
It is normally operated through a coding agent (Claude Code or Codex) using
`AGENTS.md`; every agent operation is a CLI command with a JSON contract, and
everything it knows is in reviewable files.

It never applies to jobs, contacts employers, scrapes LinkedIn or logs in
anywhere.

## How it works

```
config/employers.json ── verified public ATS boards (Greenhouse, Lever, Ashby) + manual watchlist
        │  daily GitHub Actions scan (best effort, ~07:27 UTC; often hours later)
        ▼
data/jobs.json          discovery index: every observed posting, excluded or not,
                        with source fields, observation fields and a screening judgment
data/jobs-text/         exact source descriptions, content-addressed (sha256)
data/jobs-health.json   per-board health and last-scan summary
reports/latest.md       rendered public-data report
        │
        ▼  jobwatch commands (queue, show, excluded, changes, health, search, …)
owner files: data/applications.csv · data/triage.json · data/verifications.json · data/leads.json
private output: .local-reports/ (weekly review, calendars, label templates; Git-ignored)
```

A feed listing means only that the feed listed the posting. It does not
establish an open application form, eligibility, sponsorship or a deadline.
Screening is a deterministic heuristic with stable reason codes, not a
suitability judgment.

## Quickstart

```bash
python3 -m venv .venv && .venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m jobwatch.cli capabilities          # what the agent can do
.venv/bin/python -m jobwatch.cli queue                 # what needs a decision
.venv/bin/python -m jobwatch.cli show <posting-key>    # why, and on what evidence
```

Add `--json` to any discovery command for the machine-readable envelope.

## Everyday use

| Question | Command |
|---|---|
| What appeared recently? | `recent --days 7` |
| What haven't I acted on? | `queue` |
| Why is this `review`? What is unknown? | `show KEY` |
| What did the screener exclude? | `excluded` |
| What changed — the employer, or our rules? | `changes --key KEY` |
| Are any boards stale or failing? | `health` |
| Record my decision | `triage set KEY --decision interested\|dismissed\|later [--reason CODE]` |
| Record an application | `applications add --posting-id KEY` / `applications update …` |
| This week's actions and deadlines | `weekly` → `.local-reports/weekly.md` |
| Calendar files for manual import | `calendar export` → `.local-reports/*.ics` |
| Is the operational data consistent? | `validate-data` |

**Triage vs application stages.** Triage records whether you want an
opportunity in your queue (`interested`, `dismissed`, `later` with an optional
revisit date); no record means unreviewed. The application ledger records what
you actually did (`bookmarked` … `withdrawn`). An application stage outranks
triage in the queue; scans never touch either.

## Provenance model

| Category | Where | Meaning |
|---|---|---|
| Source fields | `jobs.json` (`title`, `location`, `department`, …), `jobs-text/` | What the employer's feed returned at the last successful observation |
| Observation | `first_seen`, `first_seen_basis`, `feed_status`, `missing_since`, board health | What the collector saw and when; `not_seen` is absence, never closure |
| Judgment | `screening` (`verdict`, `codes`, `rules_version`) | Deterministic and reproducible offline; never a source fact |
| Verification | `verifications.json` | Separate read-only checks of official pages, with `verified_by` |
| Owner state | `applications.csv`, `triage.json` | Only you (or an agent on your instruction) write these |

Source changes (`changed`, `not_seen`, `reappeared`) and judgment changes
(`rescreened`, with cause `rules_changed` / `source_changed`) are recorded
separately. History from before the discovery index is kept as
`legacy_history` and labelled as mixed.

## Further reading

- `AGENTS.md` — operating contract for agents (authorisation, sync, recovery).
- `docs/DISCOVERY_ARCHITECTURE_REVIEW.md` — discovery index design, migration, evaluation, limits.
- `docs/APPLICATIONS_AND_CALENDAR.md` — ledger, weekly review, Proton calendar import, backups.
- `docs/DEVELOPMENT.md` — module map and invariants for development work.
- `docs/HOSTING.md`, `docs/P0_AUDIT.md`, `docs/STAGE1_REVIEW.md`, `docs/STAGE2_REVIEW.md` — dated evidence records.

Public API documentation: [Greenhouse](https://docs.greenhouse.io/job-board),
[Lever](https://github.com/lever/postings-api),
[Ashby](https://developers.ashbyhq.com/docs/public-job-posting-api).
