# P0 reliability audit — 17 October 2026 (UTC)

Local P0 acceptance checks passed. This is a local live collection, not an
operating GitHub service. The supplied directory has no `.git` metadata; no
repository, remote, commit, push or Actions run was created. The untouched
starting files were copied to `/tmp/jobwatch-audit-baseline/` for this session.

## Commands and results

- Initial `python -m unittest discover -s tests -v`: three import errors because
  the src-layout package was not installed. This was an environment setup issue.
- `python -m venv .venv`; `.venv/bin/python -m pip install -e .`: successful.
  The documented isolated install established the working environment. No
  runtime dependencies added.
- `.venv/bin/python -m unittest discover -s tests -v`: baseline 14 tests passed;
  after P0 fixes, 32 tests passed. Network checks are separate from tests.
- `.venv/bin/python -m jobwatch.cli scan`: baseline and corrected collectors
  both succeeded on all three configured boards on 2026-10-17. Corrected scan
  time is retained in `data/jobs.json` under `last_scan.at`.
- Targeted API audit fetched the same documented endpoints, retaining temporary
  cached responses locally and one structural, reduced fixture per provider in
  `tests/fixtures/`. Temporary cache is not a product dependency.

| Board | Successful returned postings | Baseline retained candidates | Public API |
|---|---:|---:|---|
| Palantir / Lever | 310 | 16 | [feed](https://api.lever.co/v0/postings/palantir?mode=json&skip=0&limit=100) |
| Monzo / Greenhouse | 71 | 1 | [feed](https://boards-api.greenhouse.io/v1/boards/monzo/jobs?content=true) |
| incident.io / Ashby | 34 | 6 | [feed](https://api.ashbyhq.com/posting-api/job-board/incident?includeCompensation=true) |

These are 415 source listings and 23 screening candidates, not 415 suitable
roles or independently open applications. P2 will change candidate selection.
Two application pages displayed fields and a submission control during read-only
inspection. Nothing was filled or submitted; successful backend submission is
not established. Inspection records are separate in `data/verifications.json`:

- [Palantir new-grad infrastructure, UK Government](https://jobs.lever.co/palantir/cadc0eb2-2703-43e4-8e4f-41edf5b071c6/apply):
  application page visible. Graduation fit and the owner's eligibility unknown.
- [Monzo Backend Engineer III](https://job-boards.greenhouse.io/monzo/jobs/6635595):
  application page visible; description asks for strong backend experience.
  It is a review candidate, not confirmed graduate entry.
- [incident.io Product Engineer Graduate](https://jobs.ashbyhq.com/incident/743b1624-7267-49a9-84a0-6223e414fbf0):
  web reader received a JavaScript shell; application status unknown. The
  application URL was inaccessible through that reader. No browser used.

The other 21 candidates have no independent application-page verification.

## Documentation, policies and actual response shapes

Reviewed [Greenhouse Job Board API](https://docs.greenhouse.io/job-board),
[Lever Postings API](https://github.com/lever/postings-api), and
[Ashby public posting API](https://developers.ashbyhq.com/docs/public-job-posting-api).
Greenhouse and Ashby document full board lists without a pagination cursor;
Lever documents skip/limit. Four Lever pages were required for 310 listings.

Greenhouse `updated_at` is an update time; live responses also supplied
`first_published`. They are now kept under their original field names; only
`first_published` populates the compatibility `published_at` value. Lever's
observed numeric `createdAt` is retained as raw source evidence, not interpreted
as a publication date. Ashby's `publishedAt` means last publication, not first
creation. No feed dates become collector `first_seen`.

Lever's `categories.allLocations`, country and requirements lists are now
preserved. Ashby secondary locations include their own country evidence; its
live application URLs use `/application`. Greenhouse office evidence is retained.
No board was added or slug guessed during P0. Config includes dated identity
validation sources: [Monzo](https://monzo.com/careers),
[Palantir](https://www.palantir.com/careers/open-positions/), and
[incident.io](https://incident.io/careers). Monzo links its hosted board;
Palantir identifies Lever open positions and its hosted board links the official
homepage; incident.io's public careers HTML contains matching Ashby job IDs.

Reviewed provider policy pages:
[Greenhouse legal index](https://www.greenhouse.com/legal),
[Lever customer terms](https://www.lever.co/legal/terms-of-service), and
[Ashby customer terms](https://www.ashbyhq.com/resources/terms).
These are primarily customer subscription policies, not a separate blanket
licence for arbitrary collection. The collection uses only the explicitly
published read-only posting APIs. No numeric GET quota was found in the three
posting guides; the documented Lever POST limit is irrelevant to this collector.

Robots checks: [Greenhouse](https://boards-api.greenhouse.io/robots.txt) returned
200, disallowing `/embed/` only; [Lever](https://api.lever.co/robots.txt) returned
200 with allow `/` and a one-second crawl delay (now respected between pages).
Ashby's API `/robots.txt` returned 401; no authenticated access was attempted.
Its documented public posting endpoint returned 200 without credentials.
HTTP 429 causes a failed board without automatic retries. Timeout 25 seconds,
response cap 8 MiB per request, pagination cap 10,000 with failure on exhaustion.

## Fixed with regression evidence

- Malformed records, invalid URLs/IDs, mismatched provider or board, duplicate
  identities, unsupported Ashby versions and inconsistent Greenhouse totals
  fail the whole board. They no longer silently disappear and create false
  `not_seen` transitions. Custom Greenhouse hosts require explicit future review.
- Corrected actual nested location and date semantics; retained source records
  and description evidence for tracked candidates, without daily snapshots.
- Kept feed status independent of screening. Previously a tracked role becoming
  senior/excluded could be incorrectly marked absent even while still listed.
- Total outages now persist board health and update the failure report while
  preserving observations. Report-only runs retain last scan time/failures.
- Report strings are HTML/Markdown escaped; public links validated and quoted.
  Owner notes and application ledger are never included in CI reports.
- Local scan lock and pre-write comparisons detect concurrent state/report
  edits; atomic replacement avoids partially written JSON. Lock recovery after
  process termination remains manual (first confirm no scan is running).
- Stored actual field-change history instead of relying on nonexistent Git
  history. Scan never opens `data/applications.csv` for writing.

Touched code: `feeds.py`, `state.py`, `cli.py`, new `render.py`; tests:
`test_cli.py`, new `test_reliability.py` and three reduced fixtures. Config,
public observations, latest report and separate verification records updated.

## Actions and remaining operational limits

Reviewed [GitHub schedule behaviour](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
and [concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).
The existing cron remains 07:27 UTC: 07:27 GMT, 08:27 BST. Runs can be delayed
or dropped. The inactivity auto-disable rule documented there applies to public
repositories after 60 days, not a blanket claim about private repositories.

The scan workflow has contents-write permission only because it commits public
observations; tests have read permission. Existing concurrency serialises scans;
a concurrent owner push is rejected by an ordinary push, never force-updated or
merged automatically. Collection/commit now requires a private default branch,
and summary/commit steps require an actual collection attempt. Existing Actions
summary retained; no new notification channel added. No Actions were run here.
The current v4/v5 action references remain mutable; SHA pinning is deferred to a
separately reviewed dependency update. Private CI minutes and repo policies must
be checked by the owner when setting up hosting.

`last_seen` and board health deliberately update each successful scan, so daily
state commits still occur. Change events are deduplicated; complete prevention
of timestamp-only Git churn is not implemented. The Git history backup described
in the starter is unavailable until the owner creates a repository. See README
for a local data backup/recovery procedure.

False positives observed: two incident.io Product Engineer student placements;
Monzo Engineer III requires experience. Ambiguous non-UK/remote locations and
2026 intake titles also need safer review rules. These belong to P2. P0 does
not establish comprehensive UK coverage, eligibility or vacancy deadlines.

Next: P1, expand a dated source-linked manual watchlist while retaining just the
three verified API boards; then independently test P2 screening changes.
