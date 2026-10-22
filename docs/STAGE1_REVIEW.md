# Stage 1 review — applications and weekly decisions

Implemented on `codex/stage-1-applications`, 22 October 2026. The initial working
tree was clean. GitHub repository metadata confirmed `keremnv/grad-job-tracker`
is private. No visibility settings changed, no push/merge or Actions dispatch,
and no live provider checks were performed during this stage.

## Changes

- `src/jobwatch/applications.py`: standard-library CSV validation, stable manual
  identities, tracked-ID linking, add/update/list/import CLI handlers, exclusive
  locking and atomic replacement. Existing ten-column format retained.
- `src/jobwatch/cli.py`: nested application commands; owner-file output collision
  checks include manual leads and explicitly selected application paths. Weekly
  reads official manual leads as well as independent posting verifications.
- `src/jobwatch/overview.py`: existing weekly implementation reordered around
  decisions, sorted active owner actions, source deadline provenance/deduplication,
  stage summaries, recent observations, stale feeds/failures.
- `.gitignore`: application lock files ignored alongside existing private output.
- Tests and README: offline integrity/privacy regressions and exact owner workflow.

The scanner still never reads application notes or writes the ledger. Actions
still stages only `data/jobs.json` and `reports/latest.md`; its private/default
branch guard, concurrency group and non-forced push remain intact. Collector
adapters, coverage, configurations and real `data/` files were unchanged.

## Validation and commands

All commands ran from the repository root. Python is the existing virtualenv's
interpreter; no runtime dependencies were added.

```bash
git switch -c codex/stage-1-applications
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m jobwatch.cli --help
.venv/bin/python -m jobwatch.cli applications add --help
.venv/bin/python -m jobwatch.cli weekly
git check-ignore .local-reports/weekly.md
stat -c '%a %n' .local-reports/weekly.md
git diff --check
git diff --exit-code -- data
```

Baseline: **64 passing tests**. Stage 1: **87 passing tests**, including all
existing adapter/collector reliability tests and 23 new ledger/weekly tests.
These include a subprocess writer refused by an active lock, detected external
edits during temporary-file writing and preview (including identical-byte file
replacement), replacement failure cleanup, unchanged owner data during a mocked
scan, blank historical dates, twenty-row imports and private note isolation.
The initial full run exposed an old test fixture with only eight CSV columns;
the fixture now uses the existing complete ten-column format.

A synthetic UTF-8 CSV of twenty reviewed historical rows was created under
ignored `.local-reports/stage1-demo/history-reviewed.csv`. Every historical
application date was blank. The following real CLI commands ran against the
separate demo ledger (the note marker is synthetic):

```bash
.venv/bin/python -m jobwatch.cli applications add --applications .local-reports/stage1-demo/applications.csv --job-key manual:demo --company 'Demo Ltd' --role 'Graduate Software Engineer' --url https://example.org/jobs/demo --notes SYNTHETIC_PRIVATE_NOTE
.venv/bin/python -m jobwatch.cli applications update --applications .local-reports/stage1-demo/applications.csv --job-key manual:demo --stage applied --date-applied 2026-10-22 --cv-version swe-v1 --next-action 'Review status' --next-action-date 2026-10-29
.venv/bin/python -m jobwatch.cli applications list --applications .local-reports/stage1-demo/applications.csv --stage applied --due-on-or-before 2026-10-29
.venv/bin/python -m jobwatch.cli applications import-csv .local-reports/stage1-demo/history-reviewed.csv --applications .local-reports/stage1-demo/applications.csv --dry-run
.venv/bin/python -m jobwatch.cli applications import-csv .local-reports/stage1-demo/history-reviewed.csv --applications .local-reports/stage1-demo/applications.csv --confirm
.venv/bin/python -m jobwatch.cli applications import-csv .local-reports/stage1-demo/history-reviewed.csv --applications .local-reports/stage1-demo/applications.csv --confirm
.venv/bin/python -m jobwatch.cli applications add --applications .local-reports/stage1-demo/applications.csv --posting-id ashby:incident:743b1624-7267-49a9-84a0-6223e414fbf0
.venv/bin/python -m jobwatch.cli applications add --applications .local-reports/stage1-demo/applications.csv --company 'Demo Manual' --role 'Junior Engineer' --url https://example.org/jobs/url-derived --verified-url
.venv/bin/python -m jobwatch.cli weekly --applications .local-reports/stage1-demo/applications.csv --output .local-reports/stage1-demo/weekly.md
.venv/bin/python -m jobwatch.cli report
```

Results: add/update/list succeeded; dry run previewed twenty rows; confirmed
import appended twenty; repeat import exited **2**, refusing the duplicate with
no writes. Tracked-ID and URL-derived manual adds succeeded, leaving **23 demo
rows**. A trial link to `greenhouse:riverlane:5000685101` correctly exited **2**
because that ID was not in the local observations; the README example now uses
an ID present locally. No posting was fetched to work around the refusal.
Assertions checked that the synthetic private note never appeared in captured
CLI output, the weekly report or the public report. Git confirmed all real
`data/` files unchanged. Weekly output was Git-ignored and mode **0600**.

## Limits and Stage 2

The README documents URL equivalence limits, strict duplicate refusal, CSV
formula-prefix rejection, stale-lock recovery and private backup instructions.
CLI writers cooperate through an exclusive lock. Close spreadsheet editors
before writing: they do not share the lock, and an external edit in the final
check-to-replace interval is inherently possible. This is a local-file tool,
not a distributed transaction service; atomic replacement does not replace
backups or guarantee recovery from filesystem/power failure. Command arguments
can remain in shell history/process listings. No sensitive real notes were used.

Source evidence was reused offline, not reverified live. A verified deadline
does not establish current availability or eligibility; disappeared postings
remain `not_seen`. The Git repository and backups need to remain private.

Stage 2 starts only after owner review: choose calendar event scope, owner
action versus source deadline semantics, date-only versus timestamp handling,
privacy/default fields, stable event identifiers and local ignored export
destination. Then implement deterministic offline calendar export tests. No
calendar export, notifications, submissions or other external actions were added.
