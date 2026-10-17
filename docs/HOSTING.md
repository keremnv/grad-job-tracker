# GitHub setup and hosted verification — 17 October 2026 UTC

The owner explicitly requested the private GitHub setup and activation described
in the original handoff. This records the later hosting work; the earlier local
audit in `P0_AUDIT.md` and `PHASE_REPORT.md` remains a historical snapshot.

## Repository

- [keremnv/grad-job-tracker](https://github.com/keremnv/grad-job-tracker) created
  **private**, verified through GitHub's API before pushing any project files.
- Default branch: `main`; local `origin` points to that repository.
- Existing GitHub authentication was sufficient; no token or credential was
  copied into the project. No new login or user-provided credentials needed.
- Initial commit: `364323f`; incident.io deadline evidence commit: `eda2779`.
- Explicitly staged project source/docs/tests and public observations. The
  application ledger contained only its header and remains unchanged. Known
  credential patterns were checked before committing; no matches found.
- `.venv/`, package build metadata and `.local-reports/weekly.md` are ignored and
  were not pushed. No CV, correspondence, assessment answer or personal document
  was included. No public site or repository was created.

## Actual GitHub Actions results

- [Initial push tests](https://github.com/keremnv/grad-job-tracker/actions/runs/39061154291):
  **success**, 54 offline tests passed, completed 2026-10-17T19:08:49Z.
- [First manually dispatched Job watch scan](https://github.com/keremnv/grad-job-tracker/actions/runs/39061162144):
  **success**, completed 2026-10-17T19:08:54Z. Installation, offline tests,
  live collection, Actions summary and commit/push steps all actually ran and
  succeeded; the job was not merely skipped.
- Hosted feed observation time: **2026-10-17T19:08:51Z**.
- Automatic observation commit: `6b4d4c7` (`data: refresh observed job postings`),
  successfully pushed by the workflow. The local checkout was fast-forwarded
  to include that commit without overwriting owner edits.

| Feed fetched on GitHub's runner | Returned postings | Result |
|---|---:|---|
| [Palantir / Lever](https://api.lever.co/v0/postings/palantir?mode=json&skip=0&limit=100) | 310 | success |
| [Monzo / Greenhouse](https://boards-api.greenhouse.io/v1/boards/monzo/jobs?content=true) | 71 | success |
| [incident.io / Ashby](https://api.ashbyhq.com/posting-api/job-board/incident?includeCompensation=true) | 34 | success |

415 feed postings yielded **23 screening candidates: 6 priority, 17 review**.
There were **zero provider failures and zero material change events**. The
workflow updated observation timestamps, not application stages. Candidate
availability and the owner's eligibility are not established by these counts.
The intentionally failing-provider cases in offline test logs are mocked
regressions, not live provider outages.

## Daily schedule

Both `Tests` and `Job watch scan` workflows are active. Repository Actions
permissions are enabled, and the successful data push demonstrates the scan's
contents-write permission worked. Cron remains **07:27 UTC daily**: 08:27 BST
or 07:27 GMT in the UK. No new delivery channel was added; the existing Actions
summary was enabled as part of the requested setup.

This verifies a manual `workflow_dispatch` run, **not the first cron-triggered
run**, which has not yet occurred during this work. Scheduling can be delayed
or dropped by GitHub; check the Actions page after the next expected run.
No billing plan, quota, payment method or account notification settings changed.

## incident.io deadline claim checked against official sources

The public [graduate posting](https://jobs.ashbyhq.com/incident/743b1624-7267-49a9-84a0-6223e414fbf0)
and [documented public feed](https://api.ashbyhq.com/posting-api/job-board/incident?includeCompensation=true)
identify a London Product Engineer Graduate role with a June or September 2027
start. The posting links to the official early-careers candidate hub, whose
[Dates & events page](https://incident.io/careers/candidate-hub/early-careers/events-calendar)
explicitly states **29 October 2026** as the application deadline for its 2027
internship and graduate roles.

This programme-level deadline was checked and stored separately in
`data/verifications.json`, with exact source wording, source URL, verification
time and date-only ISO value `2026-10-29`. **Expiry time and timezone are
unknown.** No midnight expiry is inferred. The generated report successfully
flags the date as within 14 calendar days using its stated UTC reference.

The individual Ashby application page still presented a JavaScript shell to the
web reader. Its individual application status therefore remains `unknown`;
no form was filled or submitted. The deadline is verified source evidence,
not a guarantee that a particular application will be accepted.

## Commands and boundaries

The requested sequence was performed using `git init -b main`, explicit staging,
`git commit`, `gh repo create ... --private --source=. --remote=origin`, an
ordinary push, and `gh workflow run scan.yml --ref main`. Remote visibility,
workflow states, job steps, logs and generated data were inspected afterwards.
Local tests also passed all 54 cases; the owner ledger matched its original
bytes. No force push or automatic conflict resolution was used.

No ChatGPT scheduled task was changed or independently validated. It remains
separate from this repository. Existing no-application, no-LinkedIn-scraping,
privacy and manual-review boundaries remain in effect.

Next: review the ranked candidates, particularly the approaching incident.io
programme deadline, and check that the first cron-triggered scan appears on
the Actions page. Source-linked manual watches still require manual review.
