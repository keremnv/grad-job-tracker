# Historical-evidence limitations — owner decision pending

Prepared 26 October 2026. **NOT ACCEPTED: no owner decision recorded.**
This records two existing data limitations; it grants no import, coverage change,
source activation, writer freeze or production-cutover permission.

Reviewed configuration/source snapshot: remote `main`
`9bbcb0149d41250cc2e56b2cbb25779bd5fafc16`. The final frozen import commit **C has
not been selected**. Acceptance must be associated with the actual final import
manifest, including any additional discrepancies discovered then.

| Limitation | Plain-English consequence | What migration preserves / does not claim |
|---|---|---|
| **H1 — native ATS responses missing from older history** | Old normalized job records and retained descriptions cannot tell us every original ATS field or prove exactly how an earlier employer response differed. Git commit dates are not employer edit dates. | Preserve available exact text, normalized snapshots, original events and Git provenance. Recover genuine native records where they actually survive. Label legacy evidence honestly; never reverse-engineer missing native records. The first new native observation of a legacy-only posting establishes an evidence baseline, rather than inventing an employer edit. |
| **H2 — pre-schema-2 exclusions were never indexed** | Some roles discarded by the old screener left no retained posting record. Their identities/counts and earlier wording cannot be reconstructed reliably. The old corpus is therefore incomplete. | Import every retained posting, including later exclusions; do not manufacture missing opportunities or historical observations. Future collection retains all valid postings from monitored feeds regardless of screening verdict. This is bounded feed coverage, not complete market coverage. |

These limitations are documented and tested in the [import report](POSTGRESQL_IMPORT_REPORT.md)
and [import/reconciliation guide](POSTGRESQL_IMPORT.md). They are different from
an unexpectedly missing referenced description, corrupt hash, lost owner record
or unexplained reconciliation difference: those remain failures or separately
reported limitations and must not be hidden under this acceptance.

## Proposed owner acknowledgement — not a recorded decision

The owner may explicitly accept H1 and H2, decline, or request clarification.
A possible acknowledgement is:

> I understand and accept H1 and H2 as limitations of the retained legacy data,
> subject to review of the final frozen-commit reconciliation manifest. This does
> not accept unexplained data loss or authorise production migration or cutover.

Record the owner's actual decision, date and the applicable final commit/manifest
reference in the private cutover record. Do not fill those fields on the owner's
behalf or treat approval of source validation as historical-data acceptance.

No decision about these two limits resolves the [Riverlane compatibility blocker](ATS_SOURCE_VALIDATION_REPORT.md#riverlane-blocker-and-required-approval)
or any other production gate.
