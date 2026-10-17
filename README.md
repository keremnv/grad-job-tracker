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
The next milestone is owner review of the candidate queue and more independently
checked entry-level descriptions, especially ML/AI. **Do not build a dashboard yet.**

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
current two-description sample does not justify selecting CV variants.

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
