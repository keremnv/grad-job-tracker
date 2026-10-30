# Deadline correction integration review — 30 October 2026

The lead and posting deadline extensions have been reviewed together with the
current main branch and its synthetic MCP demonstration. This document records
local qualification, not a production deployment or a Proton refresh claim.

## Integration behavior

Both extensions retain the existing Research connection and the two Owner Actions
prepare/commit tools. Lead corrections update only deadline fields, with independently
attributable provenance. Posting checks append immutable verification rows, naming
individual active feed/check claims for supersession. Linked lead claims are not
implicitly superseded by posting checks. Conflicts across these evidence types
omit the calendar event until separately authorized corrections resolve them.
A new cross-feature test covers correcting a lead, linking it, verifying/correcting
the posting, omission, restoration with the existing UID and increased revision,
and preserving a later unrelated contradiction.

A real integration defect was corrected: when a lead contains both native and
legacy deadline source metadata, full-owner reads now use the same source priority
as the sanitized shared view. Explicit corrected provenance still takes priority.
No archived ledger, immutable evidence or applied migration was edited.

## Schema and security

Packaged migration checks enforce the recorded prefix and checksums. Apply 0005,
then 0006, followed by their separate `lead_deadline_access.sql` and
`posting_deadline_access.sql` grant deltas. Do not rerun role provisioning.
A new test provisions the old four-migration schema and agent grants, upgrades
through both migrations, compares every added column privilege to the exact
intended matrix, proves existing grants survive, and repeats the deltas without
expansion. Existing migration, stale-state, replay, concurrency, audit and
post-COMMIT read-back tests remain in the full suite.

Historical feed claims remain active unless explicitly superseded. Duplicate
copies of the same observation retain their claim identity; later observations
remain independent. Calendar publication preserves the existing UID/revision
state and privacy rules, and fails rather than truncating an oversized working set.
No collector table write privilege or new scheduler is introduced.

## Completed local validation

- `PYTHON="$PWD/.venv/bin/python" scripts/test_postgres.sh`: 175 executed,
  **173 passed, 2 skipped**. The skipped cases require explicitly enabled cached
  real-model qualification; they are not counted as passes. The harness also ran
  the five-step synthetic official-MCP demonstration successfully.
- `.venv/bin/python -m unittest discover -s tests -v`: **268 passed**.
- Real disposable installer/socket permission tests: **7 passed**.
- Agent-runtime installer tests: **1 passed**.
- Signed pinned AWS CLI permission tests: **8 passed**, after supplying the
  required official archive and verifying its detached signature. The initial
  discovery invocation lacked that test-only archive setting; the configured
  rerun completed successfully.

Artifact builds and installed-wheel qualification use the committed revision and
both existing builders. Native ARM64 execution on the actual host, live MCP probes,
calendar publication and eventual Proton refresh remain deployment qualification
steps, subject to the owner's explicit production gate. See the two implementation
runbooks for exact schema/grant inventory and restart requirements.

## Production gate

Before changing production, inspect the actual installed manifests, schema prefix,
role grants, backups, services and timers read-only. Compare live 0001–0004 hashes
with the packaged prefix and confirm neither extension is already partially
installed. Record both artifact checksums, then request approval for the exact
install/migration/grant/service actions. Preserve private configuration, tunnel
profiles, capability URLs, calendar revision state and existing subscriptions.
No real owner write is authorized by merging or deploying this code. Live write
qualification requires a separately approved specific change; disposable synthetic
qualification and read-only production checks do not fabricate production records.
