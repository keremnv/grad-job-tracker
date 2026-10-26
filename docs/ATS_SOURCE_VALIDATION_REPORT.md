# Nine-source validation — 26 October 2026

**Eight PASS, one FAIL; production remains NO-GO.** All nine employer mappings have
supporting official evidence. Riverlane's public API works, but the current client
rejects its custom careers URLs. No source was activated, removed or renamed.
Git remains authoritative; this stage did not connect to AWS.

## Baseline and method

Release base: `19f04c59c3048291223676169b9c3f49b59789bc`.
Fetched remote `main`: `9bbcb0149d41250cc2e56b2cbb25779bd5fafc16`.
`config/employers.json` matches byte-for-byte between them: exactly these nine
enabled sources, with no duplicate namespaces. Region defaults were resolved using
the existing configuration semantics, not inferred from an employer's location.

API checks ran **14:49:51–14:50:46 UTC**; official mapping pages/assets were read
**14:50:35–14:53:33 UTC**, all on 26 October 2026. Used the actual
`postgres.endpoints.check_endpoint` interface with the unchanged provider adapters
and request contracts. Its collector identity wrote only `mode='validate'` receipts
in a newly created **local, network-isolated disposable PostgreSQL 17.11** database
with migrations 0001–0004 and vector 0.8.7. No importer, publisher, activation,
rescreening or scheduled collection entry point was called.

There were **12 API GETs**, all HTTP 200 with no redirects/rate limits or retries.
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
| `greenhouse:riverlane` | eu | 14:50:00 HTTP response; no accepted fetch receipt | 200 / **35 raw, rejected** / 1 | [Official jobs page](https://www.riverlane.com/jobs) embeds `boards.eu.greenhouse.io/...for=riverlane`. All 35 custom URL query IDs equal their native posting IDs. | **FAIL: client URL policy** / unsafe, response rejected |
| `lever:palantir` | global | 14:50:40 | 200 / 309 / 4 | [Official careers page](https://www.palantir.com/careers/open-positions/) identifies its Lever jobs module; the [Palantir Technologies board](https://jobs.lever.co/palantir) links back to the official homepage. | **PASS** / **positive-only**, pages 100/100/100/9 |
| `lever:scottlogic` | global | 14:50:46 | 200 / 43 / 1 | [Official vacancies page](https://www.scottlogic.com/careers/vacancies): all 40 distinct embedded Lever application-link IDs match the feed. Links were inspected, not submitted. | **PASS** / safe for this single short page |

No accepted record was intentionally skipped in these eight successful checks.
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

## Riverlane blocker and required approval

The captured response has `meta.total=35` and 35 records. Every `absolute_url` is
`https://www.riverlane.com/jobs?gh_jid=<native-id>`. All query IDs match their record
IDs. The current `_link` validator accepts only the configured Greenhouse-hosted
board URLs, so it correctly fails closed with `posting URL does not match
configured provider and board`. The validation receipt is `failed`, with no
successful fetch timestamp or vacancy count. This is **not** an empty board or
proof the jobs disappeared. The generic error is supplemented by the separately
hashed HTTP receipt, not rewritten as a successful run.

**No code/configuration correction was made.** Retain provider, board, employer,
EU region and stable `greenhouse:riverlane:<id>` keys. Merely changing region or
board slug would not fix this response. Recommended next approval: a narrowly
scoped Riverlane custom-URL validation policy, with exact HTTPS host/path, one
`gh_jid` equal to the native ID, and negative identity/host/query tests. Preserve
the exact provider URL and existing posting/application relationships. Do not
allow arbitrary custom hosts for all Greenhouse boards. No schema change is
indicated. Test locally, then separately approve one bounded revalidation.

Dropping/pausing intended coverage or choosing another source instead would need
an explicit owner coverage decision; neither occurred. The remaining eight need
no provider/board/region configuration change based on today's evidence.

## Persistence, checks and evidence custody

- Qualification database: nine candidate sources, **eight validated / one failed**
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
  no runtime code, migrations or source configuration changed.
- Private receipts and a validated custom-format qualification archive were
  retained; the labelled disposable container was stopped. Evidence index SHA-256:
  `1ea751601fd5ab284aadb35e02ed75984ebbfa4ec79b5e719e5d78005a824e90`.
  It references exact HTTP/page/asset objects and hashes, not employer edit times.
- No AWS, IAM, Terraform, host, S3, monitoring, timer, credential, owner-state,
  GitHub Actions or `main` change. No live scan, source activation or history import.

## Migration readiness

**The nine-source gate is not closed.** Eight feeds pass, Riverlane needs the scoped
compatibility correction/revalidation, and the owner must explicitly review
[the historical-evidence acceptance record](LEGACY_EVIDENCE_ACCEPTANCE.md).
Do not silently reduce nine-source coverage to make readiness pass.

These local validation receipts are not transferable activation tokens for AWS.
After a separately approved final import, rerun/persist genuine same-source checks
against the imported source rows and record reviewed mapping evidence before
owner-authorised activation. Today's historical config mapping timestamps were
never used as endpoint-check times. Final writer freeze/commit pin, full
reconciliation, independent real-data restore, production access and single-writer
GO remain separate approvals in [the release checklist](V1_RELEASE_CHECKLIST.md)
and [cutover runbook](AWS_CUTOVER.md).
