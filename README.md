# Graduate Job Watch — UK 2027

Private, personal, **read-only vacancy discovery** for UK software engineering,
developer infrastructure and AI/ML jobs relevant to a 2027 Computer Science graduate.

**Current status: private GitHub repository created; first hosted scan verified.**
The daily schedule is enabled and remains best effort. See
[`docs/HOSTING.md`](docs/HOSTING.md) for run evidence.
Offline adapter and state-transition tests are included. Enabled public ATS boards are **Palantir
(Lever), Monzo (Greenhouse), incident.io (Ashby)**, plus, from 17 October 2026, **Cambridge
Consultants and Riverlane (EU-hosted Greenhouse), Marshall Wace (Greenhouse), Scott Logic (Lever),
hyperexponential and Tracebit (Ashby)**. Each new mapping was verified from the employer's own site;
see `verification_notes` in `config/employers.json`. The other employers are a
manual watchlist; they are **not** automatically collected. The watchlist has
44 entries: 39 dated official careers sources and five explicitly unverified sources. No application
submissions or browser/LinkedIn scraping are implemented.

## What it does

- Fetches currently published postings from nine configured boards on three official, publicly readable ATS JSON APIs.
- Screens explicit non-UK, senior, placement and non-engineering posts; ambiguous
  geography and eligibility stay in `review`, with explainable role-family reasons.
- Tracks stable provider IDs and first/last observation dates in `data/jobs.json`.
- Generates [`reports/latest.md`](reports/latest.md) with URLs, changes and failures.
- Marks vanished postings `not_seen` **rather than declaring them closed**.
- Maintains a separate manual applications table, `data/applications.csv`.
- Adds, updates, lists and validates bulk imports of owner applications with a local CLI.
- Exports separate private employer-deadline and owner-follow-up calendars for manual import.
- Runs offline tests on push and a daily scheduled GitHub Actions scan on the default branch.

Important: A live ATS entry establishes only *that the feed listed the posting*.
It does not certify an open application form, suitable graduation year, salary,
visa sponsorship or eligibility. Review the employer's page before applying.

## Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows PowerShell: .venv\\Scripts\\Activate.ps1
python -m pip install -e .
python -m unittest discover -s tests -v
python -m jobwatch.cli scan
python -m jobwatch.cli report
```

Live scanning needs outbound HTTPS; tests do not. No credentials/API keys are
required to read the supported public ATS endpoints. Failed providers are
reported and never treated as zero postings. Network calls have a 25-second
timeout and an 8 MiB response cap. Source text is escaped on output; this is not a hostile-content sandbox.

## Run on GitHub Actions

1. Create a **private** empty GitHub repository, or run the `gh repo create`
   command in the local setup instructions in [`docs/CODEX_HANDOFF.md`](docs/CODEX_HANDOFF.md).
2. Push the project to its default branch (`main`).
3. On the GitHub Actions page, run **Job watch scan** using `workflow_dispatch`
   to check a first collection, then inspect the Actions summary and committed
   `reports/latest.md`.
4. The scheduled workflow checks daily around 07:27 UTC (GitHub cron timing is
   best-effort). It commits changed observations back to the private repository.
5. Make sure Actions are enabled and the repository/workflow token permits
   `contents: write`. If organizational policies disallow pushes, run locally
   and commit the results yourself. Private-repo Action minutes may be billed.

A scheduled run can be delayed; GitHub may disable schedules after inactivity.
The first run does not retroactively discover when a role originally opened.

## Managing the employer watchlist

Edit [`config/employers.json`](config/employers.json). Each enabled entry must
have a verified `{provider, board}` mapping for `greenhouse`, `lever` or `ashby`.
Do **not** turn manual entries on without confirming their actual ATS provider
and board identifier. `board` isn't a careers-site URL. EU-hosted Greenhouse boards
(posting URLs on `job-boards.eu.greenhouse.io`) need `"region": "eu"`; the API host is
unchanged. A region mismatch fails the board rather than hiding its postings.

For companies on Workday, bespoke sites, or LinkedIn, maintain a manual
watchlist until a permitted official public feed or API is identified.
Do not automate LinkedIn logins, evade rate limits or fetch restricted endpoints.

## Stored data boundaries

`data/jobs.json` contains publicly listed job metadata; it is not an independent
verification record. The Git repository's commit history is the audit trail.
`data/applications.csv` is manual and **may become personal data**, so keep the
repository private and never commit CV files, credentials, cover letters,
assessment answers or sensitive correspondence. The project never applies to jobs.

## Next steps

See [`docs/CODEX_HANDOFF.md`](docs/CODEX_HANDOFF.md) for a phased Codex plan.
The 17 October 2026 evidence pass is summarised in
[`docs/CV_MARKET_BRIEF.md`](docs/CV_MARKET_BRIEF.md): a selected 15-description
requirements sample (`data/role_sample.json`), a dated lead and programme-cycle
registry, and a provisional CV-variant recommendation. Infrastructure expansion has
stopped; the next milestone is owner review and applications. **No dashboard.**

## Application management (Stage 1)

### Operating through a coding agent

Open this repository in Codex or another agent with local execution access;
you can give natural-language instructions instead of typing the commands below.
The agent should read `AGENTS.md`, inspect local changes, and execute the existing
CLI using `.venv/bin/python -m jobwatch.cli` from the repository root.
[Codex supports local repository execution](https://learn.chatgpt.com/docs/codex/cli).
ChatGPT's GitHub connection can inspect pushed repository files, but that
connection itself does not execute local Python commands or access ignored local
reports. Use the agent's repository environment for tracker operations.

| Your request | Agent operation |
| --- | --- |
| “Record that I've applied to this graduate job: [URL].” | Identify the vacancy; use `applications add` or `update --stage applied`. Record only supplied dates; unknown dates stay blank. |
| “Move [application] to assessment and follow up on Friday.” | Resolve its stable key and Friday's explicit `YYYY-MM-DD` in Europe/London; update stage/action/date, preserving unspecified fields. Clarify ambiguous dates or vacancy matches. |
| “Which applications need attention this week?” | Generate `weekly`, then read the private local report and distinguish owner actions from source deadlines. |
| “Which new vacancies haven't I applied to?” | Read recent `jobs.json` observations and join by posting key or exact URL identity to validated application rows; bookmarked/preparing rows remain candidates. Flag uncertain matches and stale feeds. |
| “Generate my Proton Calendar exports.” | Run `calendar export`, verify its exit status and report both counts/paths and any warnings. |
| “Import these historical applications after checking duplicates.” | Run `applications import-csv --dry-run`; review validation/conflicts, then use `--confirm` only for an owner-authorised, reviewed import. |

Application changes must be explicitly requested by you, never inferred from
scans or source-page instructions. Agents must use the CLI for writes;
direct GitHub edits to `applications.csv` bypass validation and locking. For
read-only joins, use `jobwatch.applications.read_applications` and normal JSON
reading, selecting only fields needed for the answer. After a write, check the
exit status and reread the relevant record; report errors instead of claiming
success. Never submit applications, send emails or contact employers.

Keep application records in the private repository and generated reports and
calendars in ignored `.local-reports/`. Avoid repeating private notes in replies;
never commit credentials, CVs or sensitive correspondence. Follow-up calendar
titles intentionally contain the entered action. Proton exports still require
manual import/reimport; they are not live synchronisation (see Stage 2 below).

### Direct CLI use

Run these commands from the repository root with `.venv` activated. No command
in this section fetches vacancy URLs, scrapes LinkedIn or submits applications.
The ten CSV columns stay unchanged:

```csv
job_key,company,role,url,stage,date_applied,cv_version,next_action,next_action_date,notes
```

Save a manually checked vacancy, including one absent from all feeds:

```bash
python -m jobwatch.cli applications add \
  --company 'Example Ltd' --role 'Graduate Software Engineer' \
  --url 'https://example.org/careers/graduate-2027' --job-key manual:example-grad-2027
python -m jobwatch.cli applications list
```

The example uses your explicitly chosen stable key. Replace `--job-key ...`
with `--verified-url` to derive the key from a manually checked URL.
`--verified-url` is your confirmation that you checked a vacancy-specific URL;
it does **not** perform independent verification or mark an application form open.
It creates `manual:` plus the first 24 hex digits of SHA-256 of the URL identity.
Identity lowercases the host, removes port 443 and fragments, and treats an empty
path as `/`. It preserves path, trailing slashes and query parameters; review
alternate URLs yourself.
Duplicate keys **or** URL identities are refused, even for identical rows.
Reused/generic URLs cannot safely distinguish vacancies: use a vacancy-specific
URL rather than merging separate roles. Existing keys never change on update.

Link to a posting already in `data/jobs.json`, using its exact dictionary key:

```bash
python -m jobwatch.cli applications add --posting-id 'ashby:incident:743b1624-7267-49a9-84a0-6223e414fbf0'
```

That uses the posting ID as `job_key` and copies company, title and URL; it also
accepts manually supplied `--company` and `--role`. A conflicting URL/key is
rejected. This only links records; it does not infer eligibility, stage or dates.

Update an existing record and filter the list:

```bash
python -m jobwatch.cli applications update --job-key manual:example-grad-2027 \
  --stage applied --date-applied 2026-10-22 --cv-version swe-v1 \
  --next-action 'Review application status' --next-action-date 2026-10-29
python -m jobwatch.cli applications list --stage applied \
  --due-on-or-after 2026-10-22 --due-on-or-before 2026-10-29
python -m jobwatch.cli weekly
```

Use the key printed by `add`/`list` if you chose the URL-derived identity. Update
supports `--stage`, `--date-applied`, `--cv-version`, `--next-action`,
`--next-action-date` and `--notes`. Unspecified fields are preserved exactly;
`--notes ''`, `--date-applied ''` or `--next-action-date ''` explicitly clears
an optional field. All dates must be `YYYY-MM-DD` or blank; unknown historical
application dates stay blank. The stages are `bookmarked` (default on add),
`preparing`, `applied`, `assessment`, `interview`, `offer`, `rejected`, `withdrawn`.
List date filters are inclusive owner next-action dates and exclude blank dates.
Notes, CV versions and next-action text are omitted from console listings.
Avoid placing sensitive correspondence in command arguments/shell history; use
a private reviewed CSV for private notes. Public reports never read the ledger.

### Import about 20 historical applications

1. Make a private backup as described below. Export or manually prepare roughly
   20 rows in UTF-8 CSV with the exact ten columns above. Use unique stable keys
   such as `history:example-grad-2026` and checked vacancy-specific HTTPS URLs.
   Set the actual known stage; leave `date_applied` blank wherever unknown.
   Keep the source outside Git, for example `.local-reports/history-reviewed.csv`.
2. Review every row yourself (including dates, keys and URLs). Do not import CVs,
   assessment answers or correspondence. A spreadsheet must export actual ISO
   date strings, not localised dates or formula cells.
3. Validate and preview, then explicitly approve the import:

   ```bash
   python -m jobwatch.cli applications import-csv .local-reports/history-reviewed.csv --dry-run
   python -m jobwatch.cli applications import-csv .local-reports/history-reviewed.csv --confirm
   python -m jobwatch.cli applications list
   python -m jobwatch.cli weekly
   ```

Import defaults to a dry run even without `--dry-run`. Preview shows keys,
company, role, stage and application dates, never notes. `--confirm` revalidates
and appends all rows in one transaction. Any duplicate ID/URL, malformed row,
unknown/extra/missing column, stage or date aborts the entire batch. It never
updates existing applications; remove conflicting source rows after review or
use `applications update` deliberately. Reimporting the same file is refused.
`--dry-run` and `--confirm` are mutually exclusive.

All four commands accept `--applications PATH` after `add`, `update`, `list` or
`import-csv`, so you can rehearse against a private scratch ledger first.
Use each subcommand's `--help` for its exact options.

### Integrity, privacy and review order

Writes use a mode-0600 temporary CSV in the same directory, flush/fsync and
atomic replacement, with an exclusive `applications.csv.lock`. A concurrent
CLI writer is refused. Byte/file-identity checks refuse owner edits detected
before replacement, including edits made during import preview. Interrupted
writes leave the old complete ledger or the new complete ledger; an abandoned
lock needs manual recovery only after confirming no writer is running.
No network filesystem/distributed lock guarantees are provided. Spreadsheet
editors do not honour the CLI lock: close them before using a write command;
an edit in the final check-to-replace interval cannot be excluded. Atomic
replacement is not a backup or a guarantee against power-loss/filesystem failure.

CSV quoting preserves commas, Unicode and multiline notes. To prevent spreadsheet
formula execution without silently altering owner fields, validation rejects
values whose first non-whitespace character is `=`, `+`, `-` or `@`, and unsafe
control characters. Rephrase such text yourself; existing invalid rows must be
reviewed before any write. Errors identify fields/row numbers without echoing
private values. Ledger symlinks are refused. Keep the repo and backups private;
Git history still contains any application data you choose to commit.

The weekly report remains ignored `.local-reports/weekly.md` (mode 0600), with:

1. Overdue owner actions, oldest first.
2. Owner actions due today through seven days ahead, earliest first.
3. Source-verified application deadlines within 14 days, passed dates needing
   recheck, and undated verified wording needing manual review.
4. Applications by stage, plus later/undated owner actions.
5. Relevant vacancies first observed within seven days.
6. Stale feeds and collection failures.

Owner dates use Europe/London calendar days. Source date-only deadlines retain
unknown expiry time/timezone; timestamps retain their supplied offsets. Only
independently dated deadline evidence in `verifications.json` or official checked
manual leads is included in this weekly section; feed-only observations remain
in the public discovery report. Overlapping verified deadlines are deduplicated.
Application deadlines are omitted for already-applied/terminal applications;
bookmarked/preparing vacancies remain visible. Rejected/withdrawn rows have no
active owner follow-up actions. `not_seen` and a passed deadline never establish
closure. Notes and CV versions are omitted even from the private weekly report.

The scheduled workflow only stages `data/jobs.json` and `reports/latest.md`,
never generates the weekly report, and rejects a concurrent owner push without
force or automatic merge. Scanner output paths are checked against the owner
application, lead and verification files. No collector adapters were changed.

Stage 1 review evidence is in [`docs/STAGE1_REVIEW.md`](docs/STAGE1_REVIEW.md).
Stage 2 calendar export is the final planned feature; see below and
[`docs/STAGE2_REVIEW.md`](docs/STAGE2_REVIEW.md). Notifications remain unimplemented.

## Proton Calendar export (Stage 2)

With `.venv` activated, from the repository root:

```bash
python -m jobwatch.cli calendar export
python -m jobwatch.cli calendar export --help
# Optional: rehearse with a synthetic ledger and separate local exports.
python -m jobwatch.cli calendar export \
  --applications .local-reports/stage2-demo/applications.csv \
  --output-dir .local-reports/stage2-demo
```

The default writes `.local-reports/deadlines.ics` and
`.local-reports/followups.ics`. Input overrides are `--state`, `--verifications`,
`--leads` and `--applications`. Outputs must remain inside the repository's
Git-ignored `.local-reports/`; tracked files, symlinks, hardlinks and collisions
with selected inputs are refused. Files are UTF-8 plain text with mode 0600;
keep them private. The command prints counts, paths and non-sensitive warnings.
It never uploads, imports, subscribes, or connects to Proton.

- **Deadlines** use explicit dates from structured ATS `application_deadline`
  fields with observation timestamps, independently dated verification evidence,
  or checked official manual leads. Prose-only/missing/rolling dates are omitted.
  Posting IDs and exact vacancy URLs reconcile duplicates, including verification
  IDs absent from the feed. URL identity lowercases the host, removes default
  HTTPS port, and preserves paths/query parameters/fragments. Similar titles and
  shared deadline pages never merge different vacancies. Unresolved contradictory
  dates omit the vacancy and print a conflict fingerprint. Recheck the existing
  evidence files manually; no newest-source precedence or inferred resolution.
  Source URLs, check/observation times and wording remain in descriptions. Past
  recorded dates are retained; a missing posting never establishes closure.
- **Follow-ups** use the existing validated ledger's `job_key`, `next_action`
  and `next_action_date`, excluding rejected/withdrawn applications and rows
  missing either action or date. Titles include the entered action, company
  and role. Notes, CV versions and other application fields are omitted.

Date-only values are all-day **date markers**, with the following day as the
exclusive end. They imply neither a midnight closing time nor a timezone.
Explicit ISO timestamps with `Z` or numeric UTC offsets become instantaneous
UTC markers without an assumed duration; timezone-free, unknown `-00:00`, named
zones and fractional-second values are warned about and omitted. Owner dates
stay all-day because the ledger contains dates only. No alarms are generated;
check your destination calendar's own notification defaults. A recorded deadline
is never independent proof that an application form is currently open.

### Manual import and reimport

Create a **dedicated private calendar** in Proton, preferably separate calendars
for deadlines and personal follow-ups. On Proton Calendar's web app, open
**Settings → All settings → Import/export → Import from ICS**, select one file
and its destination calendar, then click **Import** and wait for completion.
These steps follow [Proton's import instructions](https://proton.me/support/how-to-import-calendar-to-proton-calendar).
Check the dates and descriptions after import; Proton import compatibility has
**not** been tested in an account during development.

Hashed UIDs use a fixed Jobwatch namespace and stay stable when dates, titles or
actions change (follow-ups use the application key; deadlines prefer a posting
ID, otherwise the exact vacancy URL). Keep keys/URL-only identities stable.
These IDs support matching events on reimport; Proton documents that importing
the same events again overwrites them. Verify changes in your dedicated calendar
when reimporting. Exports require **manual reimport**, not live sync.
**An event removed from a later file is not guaranteed to disappear from Proton.**
Delete obsolete imported events manually, or recreate the dedicated calendar
before importing a fresh snapshot.

Troubleshooting: exit 0 means exports completed without warnings; exit 1 means
files were written with omissions/conflicts to review; exit 2 means an error.
Fix the ledger/source evidence and rerun. Do not infer a missing date. Skip
0-event files if Proton reports nothing to import. Proton currently documents
10 MB/15,000-event import limits and supported event years 1970–2037; consult
its linked guide if an import fails. A stale `.calendar-export.lock` can be
removed only after confirming no export is running. Each output replacement
is atomic, but the two-file pair is not one transaction: rerun after a disk or
interruption error and check both counts before importing. File permissions are
not encryption; never commit or publish these exports.

### Public API documentation

- Greenhouse: https://docs.greenhouse.io/job-board
- Lever: https://github.com/lever/postings-api
- Ashby: https://developers.ashbyhq.com/docs/public-job-posting-api

## Local audit and recovery

The first local live audit and its limits are recorded in
[`docs/P0_AUDIT.md`](docs/P0_AUDIT.md). Three boards were fetched on 17 October
2026. The subsequent GitHub setup and first hosted run are recorded in
[`docs/HOSTING.md`](docs/HOSTING.md). Install into `.venv`
and use `.venv/bin/python` (or activate it) for the commands above.

Before a scan or manual ledger edit, make a local private backup outside Git:

```bash
mkdir -p "$HOME/jobwatch-backups"
chmod 700 "$HOME/jobwatch-backups"
tar -czf "$HOME/jobwatch-backups/data-$(date -u +%Y%m%dT%H%M%SZ).tar.gz" data
```

Inspect a backup with `tar -tzf PATH`. To recover, stop collectors, extract to a
separate temporary directory, inspect the files locally, then copy the selected
files into `data/`. Do not overwrite newer owner notes blindly. Backups may
contain private application state; keep them local and private.

A scan uses `data/jobs.json.scan.lock`. If interrupted, confirm no scan process
is running before removing an abandoned lock. State/report edits detected while
collecting cause a refusal to overwrite. Scanning never writes the manual
application ledger or the separate `data/verifications.json` inspection record.
`report` retains the actual last scan time and failures; it makes no network calls.

The default schedule is 07:27 UTC (07:27 GMT / 08:27 BST), best effort. GitHub's
60-day inactivity schedule disabling rule is documented for public repositories.
The scan workflow is restricted to a private repository's default branch. No
repository was created and no Actions were executed during the initial local audit;
the later authorised GitHub setup and successful run are recorded in `docs/HOSTING.md`.

## Review commands and evidence

With `.venv` activated:

```bash
python -m jobwatch.cli scan
python -m jobwatch.cli report
python -m jobwatch.cli watchlist
python -m jobwatch.cli weekly
python -m jobwatch.cli requirements
python -m jobwatch.cli leads
```

`weekly` writes **only** to ignored `.local-reports/weekly.md` (mode 0600).
It reads the owner ledger and includes next-actions, so keep that report local.
It is never generated by Actions. The ledger's rows may refer to manual-only
vacancies; collection does not infer or edit any application stage.
`leads` renders the hand-maintained `data/leads.json` (manually checked vacancy
leads and graduate-programme cycles) to `reports/leads.md`. Its validator requires
any stated intake/cycle year to appear verbatim in the quoted source wording, keeps
previous-year programmes in a separate section, and never infers a 2027 opening.
`requirements` writes `reports/requirements.md` using only description snapshots
with matching independent inspection hashes in `data/verifications.json`.
Counts are attributed *mentions*, not automatically proven requirements. The
manually classified 15-description sample in `data/role_sample.json` and
`docs/CV_MARKET_BRIEF.md` is the main CV-variant evidence; it is still a selected sample.

The public queue is `reports/latest.md`; the source-linked employer list is
`reports/watchlist.md`. Exact deadline dates are used only when supplied by a
structured source field or a manual evidence record. Prose deadline wording is
preserved for review. Missing dates remain unknown; date-only values never
acquire a guessed midnight expiry. Historical field changes stay in
`data/jobs.json`; `not_seen` never certifies closure. Board observations more
than three days old are flagged when a report is generated.

`data/verifications.json` is separately maintained after read-only inspection of
an official posting/application page. A checked application record includes
`status`, `url`, `verified_at` and `method`. Description checks additionally
include `description_url`, `description_verified_at`, and the SHA-256 of the
stored description string (`description_sha256`). A changed string invalidates
its use for requirements analysis until checked again. These checks do not
establish the owner's eligibility or a successful application submission.
The scanner reads these records to display status; it never writes them.

Manual deadline evidence may be recorded under a checked posting's `deadlines`
array, with exact `wording`, source `url`, `verified_at` and, only if explicitly
stated, ISO `value` (date or timezone-aware timestamp). Keep the source timezone.
Never turn an unknown/no-deadline statement into a rolling deadline.

Stage outcomes and the next steps are in [`docs/PHASE_REPORT.md`](docs/PHASE_REPORT.md).
Notification options remain a proposal in [`docs/NOTIFICATIONS.md`](docs/NOTIFICATIONS.md).
