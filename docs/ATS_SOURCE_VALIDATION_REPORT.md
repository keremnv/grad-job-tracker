# Nine-source validation — 26 October 2026

**Nine production sources validated, activated and successfully collected** under
the owner's final GO. [The handover](AWS_PRODUCTION_HANDOVER.md) records the SQL
single-writer switch and recovery. Earlier local checks below remain dated evidence;
they did not themselves activate anything.

## Production receipts and controlled publication

Actual production endpoint checks ran **21:13:48–21:14:11 UTC** using the installed
source-check interface, before activation through each latest successful same-source
receipt and preserved employer-association evidence. No mapping time was reused as
an endpoint-check time. All nine checks passed; no source was added or removed.
The separate controlled collection ran **21:23:29–21:23:55 UTC**.

| Source | Provider region | Valid records at production check | Controlled positives | Absence contract for these responses |
|---|---|---|---|---|
| `ashby:hyperexponential` | global | 32 | 32 | Safe, validated single response |
| `ashby:incident` | global | 33 | 33 | Safe, validated single response |
| `ashby:tracebit` | global | 3 | 3 | Safe, validated single response |
| `greenhouse:cambridgeconsultantslimited` | eu | 91 | 91 | Safe, complete total |
| `greenhouse:monzo` | global | 70 | 70 | Safe, complete total |
| `greenhouse:mw-tech-grad` | global | 5 | 5 | Safe, complete total |
| `greenhouse:riverlane` | eu | 36 | 36 | Safe, complete total; scoped official-URL rule |
| `lever:palantir` | global | 307 | 307 | **Positive-only**, multiple offset pages |
| `lever:scottlogic` | global | 43 | 43 | Safe for this short single page |

All 620 positive observations published. No positive-only absence event occurred;
feed absence is still not proof of closure, and coverage is bounded to these feeds.
Private receipts preserve actual run IDs, timestamps, hashes and provenance.

## Baseline and method

Release base: `19f04c59c3048291223676169b9c3f49b59789bc`.
Fetched remote `main`: `9bbcb0149d41250cc2e56b2cbb25779bd5fafc16`.
`config/employers.json` matches byte-for-byte between them: exactly these nine
enabled sources, with no duplicate namespaces. Region defaults were resolved using
the existing configuration semantics, not inferred from an employer's location.

Initial API checks ran **14:49:51–14:50:46 UTC**; official mapping pages/assets were read
**14:50:35–14:53:33 UTC**, all on 26 October 2026. Used the actual
`postgres.endpoints.check_endpoint` interface with the unchanged provider adapters
and request contracts. Its collector identity wrote only `mode='validate'` receipts
in a newly created **local, network-isolated disposable PostgreSQL 17.11** database
with migrations 0001–0004 and vector 0.8.7. No importer, publisher, activation,
rescreening or scheduled collection entry point was called.

The initial round made **12 API GETs**, all HTTP 200 with no redirects/rate limits or retries.
The existing 25-second timeout, 8 MiB response bound, structure/ID/URL checks and
Lever one-second inter-page pause remained; an additional ten-request-per-source
cap was never reached. Official mapping HTML/assets were bounded separately,
read without executing JavaScript, logging in or following application forms.
Raw response/page hashes, exact URLs, timestamps and database receipts remain
private; no job descriptions or operational receipt bodies are committed.

## Dated results

PASS means the current parser accepted the bounded response and the employer
association is supported **at this check time**. It does not mean monitored,
verified open, suitable for the owner, or complete market coverage. The absence
column records the existing contract's permission for a future atomic publication;
**no positive/absence observations or `not_seen` events were published here**.

| Source | Region | API receipt UTC | HTTP / records / requests | Employer-mapping evidence | Result / absence inference |
|---|---|---|---|---|---|
| `ashby:hyperexponential` | global | 14:49:52 | 200 / 33 / 1 | [Official openings page](https://www.hyperexponential.com/careers/openings) → its published Framer route module explicitly configures `hyperexponential` and calls the Ashby posting API. | **PASS** / safe under API-v1 single-response contract |
| `ashby:incident` | global | 14:49:53 | 200 / 34 / 1 | [Official careers page](https://incident.io/careers): all 34 distinct `ashby_jid` references match the feed. | **PASS** / safe under API-v1 single-response contract |
| `ashby:tracebit` | global | 14:49:54 | 200 / 3 / 1 | [Official careers page](https://tracebit.com/careers) → its published inline-script asset calls the exact `tracebit` Ashby endpoint. | **PASS** / safe under API-v1 single-response contract |
| `greenhouse:cambridgeconsultantslimited` | eu | 14:49:56 | 200 / 90 / 1 | [Official careers page](https://www.cambridgeconsultants.com/careers/): all 50 distinct `gh_jid` references match the feed; posting URLs validate against the configured EU board. | **PASS** / safe: total=90, one complete response |
| `greenhouse:monzo` | global | 14:49:57 | 200 / 69 / 1 | [Official careers page](https://monzo.com/careers) links the exact Greenhouse `monzo` board. | **PASS** / safe: total=69, one complete response |
| `greenhouse:mw-tech-grad` | global | 14:49:58 | 200 / 5 / 1 | [Marshall Wace's official programme page](https://www.mwam.com/technology-early-talent-scheme/) links the exact `mw-tech-grad` Greenhouse board. | **PASS** / safe: total=5, one complete response |
| `greenhouse:riverlane` | eu | **15:52:21 revalidation** | 200 / 35 / 1 | [Official jobs page](https://www.riverlane.com/jobs) embeds `boards.eu.greenhouse.io/...for=riverlane`. All 35 custom URL query IDs equal their native posting IDs; original `#vacancies` anchors preserved. | **PASS after scoped fix** / safe: total=35, one complete response |
| `lever:palantir` | global | 14:50:40 | 200 / 309 / 4 | [Official careers page](https://www.palantir.com/careers/open-positions/) identifies its Lever jobs module; the [Palantir Technologies board](https://jobs.lever.co/palantir) links back to the official homepage. | **PASS** / **positive-only**, pages 100/100/100/9 |
| `lever:scottlogic` | global | 14:50:46 | 200 / 43 / 1 | [Official vacancies page](https://www.scottlogic.com/careers/vacancies): all 40 distinct embedded Lever application-link IDs match the feed. Links were inspected, not submitted. | **PASS** / safe for this single short page |

No accepted record was intentionally skipped in the initial eight or Riverlane revalidation.
There is no demonstrated renamed, missing or ambiguous employer/board mapping.
The official-page matching subsets for Cambridge Consultants and Scott Logic do
not prove that those pages list every API posting.

[Greenhouse](https://docs.greenhouse.io/job-board.html) documents its public jobs
response and total; [Ashby](https://developers.ashbyhq.com/docs/public-job-posting-api)
documents published postings/API v1; [Lever](https://github.com/lever/postings-api)
documents offset pagination. Offset pages are not a proven atomic snapshot:
Palantir's missing records cannot establish absence. Scott Logic's short single
page is absence-safe only under the current request contract, not a permanent
property of that employer. Even safe `not_seen` means feed absence, not closure.

## Riverlane correction and revalidation

The initial 14:50 UTC check returned HTTP 200 with `meta.total=35` and 35 records,
but the old Greenhouse-host-only URL policy rejected it. That failed receipt is
preserved; it was not rewritten as success or treated as an empty board.
The initial short summary omitted the actual `#vacancies` anchor. Replaying the
hashed response exposed that detail before any new live request.

Tested code commit: `254e2a6cd8a9157540780e417f6f224108ac32fb`.
Six added lines in `feeds._link` accept only the canonical URL
`https://www.riverlane.com/jobs?gh_jid=<decimal-native-id>`, optionally followed by
the observed literal `#vacancies`, **only** for `greenhouse:riverlane`, region `eu`.
The existing native-ID equality check still runs. Exact URL bytes, anchors and
`greenhouse:riverlane:<id>` identities are preserved. Existing hosted Greenhouse
URLs remain valid under their original board/region checks. The custom rule accepts
no other host, query encoding, extra parameter or fragment. Source configuration,
normalizer output, screening rules and schema did not change.

After all final tests passed, the actual `check_endpoint` interface made **one**
new GET to `https://boards-api.greenhouse.io/v1/boards/riverlane/jobs?content=true`.
HTTP 200 at **26 October 2026, 15:52:21 UTC**; no redirect, rate limit or retry.
All **35** records passed, with unique native identities, exact original URLs,
`meta.total=35`, one response and `absence_safe=true`. This permits absence inference
under the existing future-publication contract; it is not closure or market-wide
completeness evidence. No postings or absence events were published.

The fresh local qualification database recorded one `mode='validate'` /
`status='validated'` run. Its candidate source row, endpoint activation timestamp,
publication pointer and audit count stayed unchanged; postings, versions, events,
applications, triage, leads, verifications and embeddings remained zero. The
private receipt/archive were preserved and the owned container stopped. Other
sources were not requested again. All nine dated public checks now pass. Subsequent
authorised [code-only AWS maintenance](AWS_RIVERLANE_MAINTENANCE_REPORT.md) installed
release `e3807a0` containing the fix and passed native tests. No source was activated;
no Terraform or schema change was needed.

### Executed regression checks

- **258 offline tests on each Python 3.11.16 / 3.12.14 / 3.13.12**, no skips.
  Six new tests cover the precise scope/host/path/scheme, spoofing/userinfo,
  missing/wrong/duplicate IDs, duplicate postings, malformed/encoded queries,
  legacy URLs, exact fragments and rejection after an earlier valid row.
- **144 disposable PostgreSQL tests**, no skips: PostgreSQL 17.11/vector 0.8.7,
  Python 3.12.14/psycopg 3.2.13. Three new tests exercise actual publisher/checker
  code with synthetic data: stable identity/application links and retained evidence;
  malformed responses with no partial publication or false `not_seen`; and
  candidate validation/completeness without activation or publication. Existing
  import/recovery, role, history, concurrency, MCP and cached offline model tests
  passed too. No real-data import or live test collection occurred.
- Offline replay of the previously rejected **35-record** captured response passed
  with its saved SHA-256 verified, before the one new live request. Ruff F and
  `git diff --check` passed. Commands: `PYTHONPATH=src <python> -m unittest discover
  -s tests -v`; `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 JOBWATCH_REAL_MODEL_TEST=1
  HF_HOME=<existing-cache> PYTHON=<existing-model-venv>/bin/python bash
  scripts/test_postgres.sh`. The latter creates/verifies its own disposable target.

The first PostgreSQL test iteration failed in a new test referring to a nonexistent
view; it was corrected to join the existing posting/version tables. Initial URL
replay also rejected the omitted anchor, prompting the precise optional-fragment
case above. No live revalidation occurred until the final corrected runs passed.
All failed/final local logs and live receipts remain private; no AWS installation,
credential, timer, source activation, owner acceptance or production change occurred.

## Initial-round persistence, checks and evidence custody

- Initial qualification database: nine candidate sources, **eight validated / one failed**
  validation receipts. Source rows, activation facts, publication pointers and
  audit count were unchanged by the checks.
- Postings, versions, material events, applications, triage, leads, verifications
  and embeddings all remain **zero** in that local target. No intended AWS database
  was contacted or seeded. All local target addressing was explicit; ambient
  runtime/admin connection settings were discarded.
- **48 saved-evidence checks passed**, including response hashes/no redirects,
  exact mapping identities and zero publication. **Five feed tests + four config
  tests passed** on Python 3.12.14/psycopg 3.2.13, without further live requests:
  `PYTHONPATH=src <python> -m unittest discover -s tests -p test_feeds.py -v`, then
  the same command with `test_config.py`. Full unrelated suites were not rerun;
  that initial stage changed no runtime code, migrations or source configuration.
- Private receipts and a validated custom-format qualification archive were
  retained; the labelled disposable container was stopped. Evidence index SHA-256:
  `1ea751601fd5ab284aadb35e02ed75984ebbfa4ec79b5e719e5d78005a824e90`.
  It references exact HTTP/page/asset objects and hashes, not employer edit times.
- No AWS, IAM, Terraform, host, S3, monitoring, timer, credential, owner-state,
  GitHub Actions or `main` change. No live scan, source activation or history import.

## Migration readiness

**The bounded nine-source public-validation gate is satisfied.** No intended
coverage was removed. This is not production activation: the Riverlane fix is now
installed/qualified on AWS, but the owner must explicitly
review [the historical-evidence acceptance record](LEGACY_EVIDENCE_ACCEPTANCE.md).
Both historical limitations remain **unaccepted**. Production is still NO-GO.

These local validation receipts are not transferable activation tokens for AWS.
After a separately approved final import, rerun/persist genuine same-source checks
against the imported source rows and record reviewed mapping evidence before
owner-authorised activation. Today's historical config mapping timestamps were
never used as endpoint-check times. Final writer freeze/commit pin, full
reconciliation, independent real-data restore, production access and single-writer
GO remain separate approvals in [the release checklist](V1_RELEASE_CHECKLIST.md)
and [cutover runbook](AWS_CUTOVER.md).
