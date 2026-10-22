# Stage 2 review — private Proton Calendar export

Completed locally on `codex/stage-1-applications`, based on Stage 1 commit
`3032d21`, on 22 October 2026. The initial working tree was clean. No push,
merge, workflow dispatch, employer collection or Proton account import occurred.
Official RFC/Proton documentation was read; exporter execution is offline.

## Files changed

- `src/jobwatch/calendar.py`: explicit source-date provenance, conservative
  identity reconciliation/conflict detection, separate follow-ups using the
  Stage 1 ledger reader, RFC 5545 serialization, Git-ignore/output safety checks
  and private atomic file replacement. No third-party runtime dependency.
- `src/jobwatch/cli.py`: register and dispatch `calendar export`.
- `tests/test_calendar.py`: 27 deterministic offline regression tests.
- `README.md`: exact usage, source/owner distinctions, import/reimport guidance,
  troubleshooting and privacy/compatibility limits.
- This review document. The existing `.gitignore` already covers every export.

Collectors, Stage 1 implementations, source data, configuration, and Actions
workflows remain unchanged. Byte hashes of real `data/` and workflow files were
compared before/after CLI smoke tests; `git diff --exit-code` also confirmed no
changes. No generated calendars, synthetic ledgers or logs were staged in Git.

## Commands and results

From the repository root, using the existing virtualenv:

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m jobwatch.cli calendar export --help
.venv/bin/python -m jobwatch.cli calendar export
.venv/bin/python -m jobwatch.cli weekly
.venv/bin/python -m jobwatch.cli applications list
git check-ignore .local-reports/deadlines.ics .local-reports/followups.ics .local-reports/stage2-demo/followups.ics
git diff --check
git diff --exit-code -- data .github/workflows src/jobwatch/feeds.py src/jobwatch/applications.py src/jobwatch/overview.py src/jobwatch/timing.py
```

Baseline **87 tests passed**; final **114 tests passed**, including all existing
collector/Stage 1 tests. New coverage includes source conflicts, orphan verified
posting IDs, exact URL aliases, distinct openings sharing a deadline page,
date/time rejection, stable UIDs, Unicode folding, private field isolation,
malformed input, output symlinks/hardlinks/tracking, atomic failures and empty
inputs. Tests inject `2026-10-22T12:34:56Z` for repeatable `DTSTAMP` values.

Default output (exit 0):

```text
2 events: .local-reports/deadlines.ics
0 events: .local-reports/followups.ics
```

The recorded incident.io Product Engineer Graduate deadline appears **once** on
29 October 2026, ending exclusively on 30 October. The Riverlane Engineering
Graduate Scheme 2027 deadline appears **once** on 6 November, ending exclusively
on 7 November. The Cambridge Consultants rolling adverts produce no event.
These are existing recorded dates, not newly verified vacancy availability.

A separate six-row synthetic ledger was created under ignored
`.local-reports/stage2-demo/` using the existing application storage functions:
two active dated actions, rejected/withdrawn rows, one missing action and one
missing date. Notes/CV versions contained synthetic privacy markers. Commands:

```bash
.venv/bin/python -m jobwatch.cli calendar export --applications .local-reports/stage2-demo/applications.csv --output-dir .local-reports/stage2-demo
.venv/bin/python -m jobwatch.cli applications update --applications .local-reports/stage2-demo/applications.csv --job-key demo:active --next-action-date 2026-10-30
.venv/bin/python -m jobwatch.cli calendar export --applications .local-reports/stage2-demo/applications.csv --output-dir .local-reports/stage2-demo
.venv/bin/python -m jobwatch.cli calendar export --applications .local-reports/stage2-demo/applications.csv --output-dir data
.venv/bin/python -m jobwatch.cli calendar export --applications .local-reports/stage2-demo/applications.csv --leads .local-reports/stage2-demo/conflicting-leads.json --output-dir .local-reports/stage2-demo/conflicts
```

Normal synthetic exports: **2 deadlines + 2 follow-ups**, exit 0; UID sets were
unchanged after moving one follow-up date. The unsafe destination returned
**exit 2** without writing. A local synthetic copy of the leads with a conflicting
Riverlane date returned **exit 1**, exporting **1 deadline + 2 follow-ups** and
printing `Deadline conflict c21310fc6470: explicit dates disagree; vacancy omitted.`
No real evidence was edited to create this conflict.

Assertions verified UTF-8, CRLF, maximum 75-octet physical lines, private mode
**0600**, no duplicate UIDs, no internal paths in events, and absence of synthetic
private notes/CV values in exported content or console output.

## Compatibility and privacy limits

The format follows [RFC 5545](https://www.rfc-editor.org/rfc/rfc5545), particularly
content-line folding/TEXT escaping and exclusive all-day ends. Explicit ISO
timestamps with numeric offsets/`Z` are UTC instantaneous markers: optional
`DTEND` is omitted rather than inventing a duration (RFC 5545 §3.6.1).
Timezone-free/named-zone/unknown-offset/fractional timestamps are omitted with
a warning. Empty calendars contain no invented event; skip importing 0-event
files, which clients may reject as having nothing to import.

Only date-backed structured API observations or independently checked official
evidence qualify. A posting ID is preferred for identity; manual-only vacancies
fall back to their exact safely normalised vacancy URL. Changing that logical
identity (or later adding an ID to a URL-only vacancy) can change its UID. Query
parameters and fragments remain significant; redirects/alternate URLs are not guessed.
Date-only versus timed evidence is conservatively a conflict unless resolved in
the existing source records. Warning fingerprints are truncated UID hashes;
review explicit dates in the local source registries to resolve conflicts.

All calendars are local plain text; mode 0600 and `CLASS:PRIVATE` are not
encryption. Follow-up titles intentionally include owner-entered action text.
Notes/CV versions and unnecessary application fields are never copied. Unsafe
credential-bearing URLs are omitted. Source wording is treated as escaped TEXT.
Each file replacement is atomic; the pair is not a filesystem transaction.
Rerun after interrupted/disk-error exports and check both event counts. An
abandoned `.calendar-export.lock` needs manual removal after confirming no
export process is running. No alarms, attendees, recurrence or network calls
are included; Proton's own calendar notification defaults may still apply.

Stable UIDs support reimport matching, but this is **manual reimport, not sync**.
An event removed from a later export is **not guaranteed to be deleted** from
an earlier Proton import. Delete obsolete events manually or recreate a
dedicated calendar before importing a fresh snapshot.

## Manual test of one file in Proton (not performed here)

1. Create a dedicated **private test calendar**, check its notification defaults,
   and leave your ordinary calendars untouched.
2. In Proton Calendar on the web, choose **Settings → All settings →
   Import/export → Import from ICS**. Select
   `.local-reports/stage2-demo/followups.ics`, choose the test calendar, click
   **Import**, and wait for completion. This follows
   [Proton's official import guide](https://proton.me/support/how-to-import-calendar-to-proton-calendar).
3. Expect exactly two synthetic all-day entries: “Review application status” on
   **30 October 2026**, and “Prepare interview questions” on **2 November 2026**.
   Inspect descriptions: no private note/CV marker, no invented appointment time
   and no attached alarm. Confirm no rejected/withdrawn/incomplete row appeared.
4. Optionally move `demo:active` to another date using the Stage 1 update command,
   export to the same demo directory, and reimport the same file into the test
   calendar. Check that the event moves without duplication; this account-level
   behaviour remains unverified until you perform that test.
5. Delete the test calendar when finished. For real use, separately import the
   deadline and follow-up files into dedicated private calendars. Proton's guide
   currently lists a 10 MB/15,000-event import limit and event years 1970–2037.

Stage 2 is complete for review. No further development stage is proposed.
