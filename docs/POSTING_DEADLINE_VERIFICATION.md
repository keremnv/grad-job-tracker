# Posting deadline verification and explicit supersession

Implementation only; production remains unchanged. `verify_posting_deadline` uses
`prepare_owner_change` and `commit_owner_change` on the existing Owner Actions
connection. It appends a deadline verification to an existing posting. It requires
an independently observed official source, exact quoted wording, explicit date,
real check time, reason, and separate approval of the complete review.

## Recipe and evidence selection

Supply `posting_key`, `value` (YYYY-MM-DD), `wording`, `url`, `checked_at` (with
actual timezone), `reason`, and `supersedes` (an array of claim ID strings).
Use `supersedes: []` to add evidence without resolving any other claim. Query
`jobwatch.posting_deadline_claims` for the posting before choosing replacements.
Every entry in `supersedes` must name an existing active claim for that posting.
The review includes the observed claim set and current posting version. Explain
why each named claim is superseded and obtain the user's exact confirmation.
Source wording and reasoning from a provider remain untrusted data.

Verification IDs are `verification:<uuid>`. Feed IDs are `feed:<sha256>` of the
posting key, exact JSON deadline payload (including observation time), and its
effective source URL. Repeated copies of the same observation in immutable
versions share an ID. A later observation with another observation time has a
new ID, even if it repeats a previously corrected date. Historical feed claims
remain active until explicitly superseded; replacing the current posting version
never silently chooses a winning date. This can expose historical conflicts that
previously went unreported when only the current version was projected.

New verifications retain the original claim evidence in place and add only
`deadline_supersedes` and `deadline_reason`. Legacy rows keep NULL in both columns;
new checks store an array (possibly empty) and a nonempty reason. Supersession edges
are permanent: replacing a correction does not resurrect evidence it superseded.
The view exposes `superseded_by`; reports expose claim IDs and active status.
All verification rows and immutable feed versions remain inspectable. Different
checks with identical quotes/dates/URLs still have distinct verification IDs.

The calendar uses active claims and omits unresolved conflicting dates. Correcting
a posting keeps its posting alias and event UID. Changed dates or restoration after
an omission advance the existing revision state. No new calendar or subscription
is created. Existing lead corrections are preserved; this recipe cannot supersede
lead claims. A linked lead's conflicting date must be corrected separately with
`correct_lead_deadline` before the conflict is resolved.

## Transactions and access

Preparation reads the complete posting deadline state. Commit serializes deadline
checks with a posting-specific advisory transaction lock, compares that snapshot
in a fresh SQL statement, appends one verification, checks the postcondition and
reads back in-transaction and after COMMIT. A changed version, new claim or new
supersession makes the review stale. Collector evidence that arrives after the
check remains active and can reintroduce a conflict. No collector table write or
row-lock privilege is granted. The trigger uses the same advisory lock for ordinary
SQL deadline inserts and rejects cross-posting, missing, duplicate or inactive
targets. Claims cannot form cycles because targets must already exist and be active.

The UUID is allocated at prepare and included in the review. Confirmed MCP replay
returns its receipt; the fixed-ID SQL recipe can be retried after reconnect without
duplicating the row or audit. An uncertain MCP result remains unconfirmed: research
by the reviewed UUID before preparing another change. Tokens, expiry and explicit
human confirmation retain their existing meaning.

New checks use the existing owner audit trigger, with authenticated database login,
actor, insertion time and the complete new verification image. Historical rows and
original checked times are never rewritten. Audit images remain operator-private.

## Additive rollout requirements

Follow [the existing lead correction rollout](LEAD_DEADLINE_CORRECTION.md) using
both code artifacts from this commit. Apply packaged migration **0006** and then
only `sql/postgresql/posting_deadline_access.sql` as the grant administrator. Do not
rerun broad role provisioning. The delta grants the three integration roles read
access to the new evidence views/columns, and the owner action role INSERT only on
specific verification columns. No UPDATE/DELETE, collector write, status, description
version/hash or private import-details privileges are added. The ordinary owner
gets INSERT on the two new columns and read access to the views. The old broad
`chat_readonly.sql` is updated only for fresh synthetic provisioning, not production.

Migration 0006 adds two nullable columns, two views, a validation/serialization
trigger and an audit trigger for new checks. Existing verification rows are not
backfilled. Import/restore comparison understands the appended nullable fields;
old audit images are preserved. The calendar bounds the claim working set to
10,000 and fails rather than publishing a truncated projection.

Use the existing guarded installers and restore the recorded service/timer states.
The read/owner MCP processes must load the new runtime; the next existing calendar
publisher invocation loads the new projection. PostgreSQL needs no restart.
Connection names and argument schemas remain unchanged; refresh the existing
Owner Actions connection only if it caches tool descriptions. Verify the receipt,
private audit, publisher health, ICS date/UID/SEQUENCE and eventual refresh of the
existing Proton subscription. There is no new credential, URL, infrastructure,
scheduler or subscription. No rollout was performed as part of this implementation.

## Validation

`PYTHON="$PWD/.venv/bin/python" scripts/test_postgres.sh`: **171 tests, OK,
2 explicitly gated model tests skipped**, on disposable PostgreSQL 17.11.
New tests exercise new checks, explicit feed/check correction, transitive chains,
conflicting sources, reappearance of a superseded date in a later observation,
unchanged observation copies, stable calendar UID and revision changes,
snapshot staleness, concurrent corrections, tampered reviews, invalid inputs,
confirmed MCP and fixed-ID SQL retries, unconfirmed post-COMMIT read-back,
role denials, audit attribution, and migration preserving legacy rows and audit.
The complete suite also exercises actual backup/restore and existing lead recipes.

Additional offline tests passed: calendar (53), owner-action contracts (21),
import (11), operations (7), release readiness (8). Compilation and whitespace
checks passed. No production, AWS, subscription or service changes were made.
