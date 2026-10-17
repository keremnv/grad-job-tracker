# Phase outcomes — initial local audit, 17 October 2026 UTC

This is the earlier local-audit snapshot (last scan 16:49 UTC). The subsequent
GitHub setup, successful hosted run and newly verified incident.io deadline are
recorded in [`HOSTING.md`](HOSTING.md).

The standing specification and handoff were read in full before edits. Work was
performed locally in the supplied folder. It has no Git metadata: there were no
commits, remote writes, repository creation, pushes, deployments or GitHub Actions
runs. No application was filled or submitted, no CV or LinkedIn was changed,
and no notification destination was configured.

## Status and evidence

| Phase | Delivered | Tests at stage boundary | Acceptance / uncertainty | Next small step |
|---|---|---:|---|---|
| P0 | Provider/documentation audit, live collection, application-page samples, regression fixes, visible outages, source-date semantics, safe output and state handling | 32 passed (14 baseline) | Local acceptance met. Hosting and successful application submission are not established | Set up and manually check a private repository/Actions run when requested |
| P1 | 31 watch entries, 30 dated official careers sources, 3 enabled independently checked original ATS boards; mapping validation enforced | 35 passed | Watchlist gate met. Bloomberg remains unverified; manual watches are not automatically collected and individual vacancies remain unverified | Manually review the additional 2027 leads; verify any new feed before enabling |
| P2 | Explainable role families and ranking; junior/associate roles; cautious geography; intake/experience/clearance uncertainty; student placements screened out | 41 passed | Tested implementation delivered, not a measured precision/recall claim. Personal preferences unresolved | Review the 23 candidates and supply real false-positive/negative examples |
| P3 | Exact source fields/wording, timezone-aware urgency, 14-day checks, field-change history and three-day staleness warnings | 45 passed, expanded later | Partial evidence gate: implementation tested offline, but **zero exact dated application deadlines found in live candidates** | Record a real source-stated deadline when encountered and verify its report |
| P4 | Independent read-only manual ledger; local private weekly overview; content-bound, attributed requirement mentions | 51 passed, final suite 54 | Tooling delivered; decision-support evidence partial: two checked descriptions, no checked ML/AI sample; empty owner ledger | Expand checked entry-level descriptions across employers and role families before deciding CV variants |
| P5 | Notification choices, privacy trade-offs and deduplication design documented | No delivery code added; final offline suite retained | Design only. No delivery channel selected or new automation implemented | Owner may choose a channel in a later task |

## Actual live results

Last actual scan completed **2026-10-17T16:49:22Z**. All three boards succeeded:

| Employer / provider | Returned source postings | Priority | Review | Excluded by current screening |
|---|---:|---:|---:|---:|
| [Palantir / Lever](https://api.lever.co/v0/postings/palantir?mode=json&skip=0&limit=100) | 310 | 5 | 12 | 293 |
| [Monzo / Greenhouse](https://boards-api.greenhouse.io/v1/boards/monzo/jobs?content=true) | 71 | 0 | 2 | 69 |
| [incident.io / Ashby](https://api.ashbyhq.com/posting-api/job-board/incident?includeCompensation=true) | 34 | 1 | 3 | 30 |
| **Total** | **415** | **6** | **17** | **392** |

These support **23 listed candidates**, not 23 suitable or verified-open jobs.
The state keeps 25 historically tracked postings: the current 23 plus two
placements now screened excluded while their feed status remains listed.
No source ID was collapsed across separate vacancies. Two newly retained
candidates after P2 were Monzo Software Engineer III and Palantir Site
Reliability Operations Analyst - UK Government. They need owner review, not an
assumption that they are suitable graduate roles. The final two change events
are parser-derived deadline-evidence corrections, not newly advertised jobs.

Two read-only application-page samples showed fields and a submit control:
[Palantir new-grad infrastructure](https://jobs.lever.co/palantir/cadc0eb2-2703-43e4-8e4f-41edf5b071c6/apply)
and [Monzo Backend Engineer III](https://job-boards.greenhouse.io/monzo/jobs/6635595).
These are recorded as `open_verified` **at inspection time**, separately from
feed status and owner application stages. Successful submission was not tested.
Owner eligibility remains unknown. The other **21 current candidates** have
unknown application status in this tracker. The sampled
[incident.io graduate role](https://jobs.ashbyhq.com/incident/743b1624-7267-49a9-84a0-6223e414fbf0)
returned a JavaScript shell through the web reader; no browser automation used.

No boards failed in the live collections. Failure preservation was demonstrated
with deterministic malformed/truncated/429/timeout/partial/total-outage tests,
not by claiming a real provider outage happened.

Additional official-page leads are in `reports/manual-leads.md`: Cambridge
Consultants 2027 software and AI roles. These are separate from feed counts and
have unknown application status. Its software role explicitly states rolling
recruitment/no deadline; the tracker does not infer this for any other role.

## Source verification and coverage limitations

The complete dated source list and reasons are in `config/employers.json` and
`reports/watchlist.md`. All initial employer interests remain. Smaller/regional
and tooling employers include Scott Logic, TPP, Redgate, TTP and Autotrader;
applied AI/research/software watch sources include Cambridge Consultants,
Faculty, Graphcore, Quantexa and MathWorks. This is a watchlist, not a claim that
all have a suitable 2027 opening now.

Careers redirects were followed and recorded: Softwire, Stripe, Faculty,
Redgate and BAE Systems; BJSS careers redirected to CGI UK and is represented
as one watch entry. Bloomberg's original Avature URL and an official London
page were inaccessible through the reader and remain unverified. Initial
attempts at MathWorks/Autotrader URLs were replaced only after accessible
official sources were found. Roke (403), AND Digital (reader access failure)
and Raspberry Pi's attempted careers URL (404) were not added as verified
sources. No access restrictions were bypassed. Manual pages are not scheduled
for automated collection.

Current provider docs, policy/robots outcomes, request limits, parsing changes,
source identity evidence and GitHub operational caveats are recorded in
`docs/P0_AUDIT.md`. No numeric GET quota was found in the three posting guides;
Lever's published one-second crawl delay is respected between pages. Greenhouse
custom employer-hosted job URLs currently fail conspicuously and require future
explicit review, rather than accepting arbitrary feed links.

## False positives, missed roles and dates

- Both incident.io placements were retained as candidates by the scaffold;
  they are now excluded from the graduate queue, with history preserved.
- Monzo Engineer III roles stay in review with experience uncertainty, not
  labelled confirmed graduate entry. Palantir's operations analyst is also a
  review lead, with no guarantee of an engineering fit.
- Tests cover nested Lever locations and Ashby secondary-country evidence;
  a non-UK primary country no longer silently discards a UK secondary location.
- London/Cambridge alone do not prove UK geography. Remote/global and ambiguous
  records are kept for review; explicit foreign locations can be excluded.
- Junior Python/Java, compiler/tooling, MLOps and research-software titles are
  recognised. 2026/2028 title mismatches are reviewed for start flexibility.
  There is no calendar cutoff excluding roles opening after January 2027.
- Citizenship, clearance, relocation, sponsorship and sector preferences remain
  unresolved. Source wording is flagged, not used to invent owner eligibility.
- The live candidates contain one application-deadline prose statement (Monzo's
  explicit no-closing-date wording) and zero exact dated deadlines. Palantir
  offer deadlines are not application deadlines. Date-only values retain
  unknown expiry time/timezone; tests use a stated UTC calendar reference.

## Exact verification commands

Commands below use the isolated virtual environment; activation allows the
unprefixed `python` commands in AGENTS.md. Final offline suite: **54 passed**.

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m jobwatch.cli scan
.venv/bin/python -m jobwatch.cli report
.venv/bin/python -m jobwatch.cli watchlist
.venv/bin/python -m jobwatch.cli weekly
.venv/bin/python -m jobwatch.cli requirements
cmp data/applications.csv /tmp/jobwatch-audit-baseline/data/applications.csv
```

Scan returned 0, reports/weekly/requirements commands returned 0, and `cmp`
confirmed the manual ledger is byte-for-byte unchanged. Tests use no outbound
network. Three compact structural fixtures were derived from actual official
responses, with source notes in `tests/fixtures/README.md`. The final parser
also passed a cached replay of all 415 source records without new network calls;
that replay is not another current live observation.

Both workflow YAML files parsed with the environment's existing PyYAML;
no dependency was added. `actionlint` is unavailable. Syntax parsing and manual
review do not establish a successful GitHub Actions run.

## Files changed

- Existing collector code: `src/jobwatch/feeds.py`, `filtering.py`, `state.py`,
  `cli.py`. New helpers: `config.py`, `render.py`, `timing.py`, `overview.py`.
- Tests: updated `tests/test_cli.py`, `test_state.py`; added `test_config.py`,
  `test_filtering.py`, `test_reliability.py`, `test_timing.py`, `test_overview.py`,
  and three reduced JSON fixtures plus their source README.
- Sources/public observations: `config/employers.json`, `data/jobs.json`;
  new separate inspection records `data/verifications.json`.
- Reports: updated `reports/latest.md`; new `watchlist.md`, `requirements.md`,
  `manual-leads.md`; private ignored `.local-reports/weekly.md` (mode 0600).
- Docs/operations: `README.md`, `docs/CODEX_HANDOFF.md`, `.gitignore`,
  `.github/workflows/scan.yml`; new `docs/P0_AUDIT.md`, this phase report and
  `docs/NOTIFICATIONS.md`.

`AGENTS.md`, package dependencies, `data/applications.csv`, `tests/test_feeds.py`
and the test workflow were not changed. No dashboard, paid service, external
sender, database, browser automation or LLM call was added.

## Remaining limits and highest-value next change

A three-board collector is not comprehensive UK discovery; the new employer
sources mostly require manual review. Hosting remains inactive. Daily
observation timestamps still produce state changes/possible daily commits;
unchanged material content generates no repeated change event. SHA-pinned
Actions updates and broader duplicate detection remain future review items.
The local lock prevents overlapping collectors and pre-write comparisons catch
edits during collection; it is not a transactional database or an absolute
cross-process race guarantee. Local backups and careful recovery are documented.

The highest-value next step is **owner review of the ranked candidates and
manual 2027 leads**, recording actual eligibility/deadline evidence and a few
representative false positives. More independently checked descriptions across
SWE, platform and ML/AI should follow; the two-description sample does not
support a CV-variant decision. No personal preference or notification channel
was assumed to complete the safe local work.
