# Lead deadline correction: review and rollout

Implementation only; no production operation has been performed. The new
`correct_lead_deadline` recipe uses the two existing Owner Actions tools and their
existing confirmation, expiry, audit, refresh and replay behavior. It updates
only `deadline_date`, `deadline_wording` and four independent provenance columns.
Neither collector history nor other deadline claims are changed.

Migration `0005_lead_deadline_provenance.sql` appends nullable `deadline_source_url`,
`deadline_official`, `deadline_checked_at` and `deadline_checked_by`. The all-or-none
constraint requires an explicit date and wording when provenance is recorded.
It updates the sanitized `agent_leads` view in place if that view exists, preserving
its existing grants. Existing records are unchanged and need no backfill.
Fresh provisioning uses the amended view definition in `agent_access.sql`.

The separate `sql/postgresql/lead_deadline_access.sql` delta grants only SELECT on
the four new lead columns to the three integration identities and UPDATE on the
six deadline columns to `jobwatch_agent_owner`. It grants ordinary `jobwatch_owner`
read access to the sanitized view used by the SQL concurrency guard. It grants no
new INSERT, DELETE, private-field access, role membership or collector authority.
Do not rerun `agent_access.sql` or role provisioning on an existing installation.

## Smallest safe production rollout (operator procedure)

1. Review and authorize this recipe/permission extension before production use.
   Build both code-only artifacts from the committed revision using
   `infra/aws/scripts/build_release.py` and `build_agent_release.py`, as described
   in [the existing procedure](../infra/aws/integrations/README.md). Record full
   commit and artifact checksums. Take and verify the normal backup receipt.
2. Use the existing guarded maintenance/installer procedure. Record enabled and
   active states, wait for active jobs, and pause/disable exactly the units the
   two installers require. Pending Owner Actions reviews are in memory: finish
   or discard them before stopping the server. Preserve calendar revision state,
   URLs, credentials, configuration and installed tunnel profile drop-ins.
3. Install both artifacts with `deploy_release.py` and `install_agent_runtime.py`.
   Apply packaged migrations through `jobwatch-db-migrate` with the existing
   migrator capability, then apply **only** `lead_deadline_access.sql` as the
   schema grant administrator. The application code's exact migration check now
   requires 0005; do not resume jobs with mixed code/schema versions.
4. Start `jobwatch-agent-read.service` and `jobwatch-agent-owner.service` on the
   new runtime. Restore the paused tunnel services and all previous timer/path
   states. The next `jobwatch-calendar.service` invocation loads the new projection;
   run it once through the existing publisher procedure and restore its hourly
   timer and refresh path. PostgreSQL needs no restart; collection and backup
   units need only the resumption required by installer guards. No Terraform,
   authentication, URL or credential changes are required.
5. Check the existing Owner Actions tool description exposes the recipe. Connection
   names and tool argument schemas are unchanged, so no new connection is needed.
   Refresh/reconnect the existing connection only if it caches the old tool list.
   Use synthetic qualification before any specifically authorized real correction.
6. For an authorized correction, check COMMIT receipt/read-back and operator-only
   audit old/new values. Check the existing calendar service and health receipts,
   generated ICS date, stable UID and increased SEQUENCE. Fetch using the existing
   private subscription URL without recording it in logs. Confirm the date moves
   in the existing Proton subscription after Proton's polling delay (the existing
   runbook records 4–16 hours). A successful publisher run alone does not prove
   Proton has refreshed; observe the subscribed event. Do not reimport the ICS.

A conflicting external claim can still omit the event; inspect the calendar warning
and evidence rather than treating publication success as conflict resolution.
Do not roll back by dropping provenance or restoring a stale database. Preserve
committed corrections and audit data; use a reviewed forward fix if necessary.

## Validation

- `PYTHON="$PWD/.venv/bin/python" scripts/test_postgres.sh`: 162 tests, OK,
  2 explicitly gated model tests skipped; disposable PostgreSQL 17.11.
- Calendar tests (`test_calendar*.py`): 53 passed; owner-action contracts
  (`test_agent_contract.py`): 21 passed; import unit tests: 11 passed;
  operations unit tests: 7 passed; release readiness tests: 8 passed.
- Python compilation and `git diff --check` passed.

New database tests cover correction/read-back, legacy and corrected provenance,
private-field preservation, audit actor/login and old/new images, confirmed replay,
tampered reviews, changed legacy source, competing prepared changes, invalid/future
checks, non-official evidence omission, role denials, calendar UID/SEQUENCE, and
migration over an existing provisioned view without changing evidence or audit.


The subsequent [posting verification extension](POSTING_DEADLINE_VERIFICATION.md)
adds migration 0006 and its separate grant delta. The lead recipe remains available;
posting supersession never silently overrides a linked lead's evidence.
