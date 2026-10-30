# Agent research and authorised owner changes

Ordinary SQL remains the primary interface. An authorised local agent can use
`jobwatch_owner` and [the SQL cookbook](../sql/postgresql/owner_workflows.sql)
directly. The optional ChatGPT write connection executes that **same file**;
it is not an arbitrary SQL write endpoint or a second owner-state implementation.

## Two connections

| Connection | Tools and database identity | Excluded authority/data |
| --- | --- | --- |
| Research | Existing five tools: `sql_query`, `sql_schema`, `search`, `inspect_evidence`, `run_report`; `jobwatch_agent_readonly` | Writes, application notes/CV labels, triage notes, raw lead details/source notes, detailed audit images, employer notes |
| Owner actions | `prepare_owner_change`, `commit_owner_change`; `jobwatch_agent_owner` | Arbitrary SQL, deletion, application notes/CV changes, collector evidence/screening/control, source activation, migrations, infrastructure |

Research permits all screening verdicts, native evidence/history, health,
verification evidence, public lead fields, programme cycles, application stages
and follow-up text/dates, triage decisions/revisit dates, and an audit **summary**
(time, actor claim, authenticated database login, table, operation). Treat application
follow-up text as private information shared with the connected ChatGPT account.
Raw verification import details are also excluded; projected source wording and
verification provenance remain available. The narrower roles are optional provisioning; the old broad synthetic
`jobwatch_chat_readonly` grant file is **not** the production connection.

SQL is composable, parameter-bound and bounded to the existing read-tool limits
(1–200 rows, 5-second statement timeout, bounded encoded responses). Transactions
are read-only; grants enforce the column/write restrictions. Metadata identifies
readable columns. No keyword blacklist is the security boundary. The write
connection cannot elevate to the existing owner/collector/migrator identities.

## Owner protocol

1. Research normally. Resolve a posting/application/lead key from the results;
   the person does not have to know or type an identifier. Refuse ambiguous matches.
2. `prepare_owner_change(recipe, values)` reads expected state without writing.
   Show the returned target, values and complete review to the person.
3. After that specific instruction and the client's write confirmation, call
   `commit_owner_change(change_id, review)` with the unchanged review.
4. Return the committed read-back receipt. A refresh hint requests calendar
   regeneration; hourly regeneration also handles ordinary SQL owner writes.

Supported recipes are the existing `create_application`, `link_application`,
`update_stage`, `set_followup`, `set_triage`, `record_lead`, `link_lead`, and
`correct_lead_deadline` (requires migration 0005 and the grant delta below).
Creating an application from a resolved posting derives its company/title/URL
and links it atomically. Manual application keys survive later linkage.
Clear a completed/cancelled follow-up with empty `action` and `action_date`.
An `interested` decision or dated `later` revisit needs no application or shortlist.
Leads require observed wording, provenance and a non-future checked-at timestamp;
an agent must not label unobserved claims as official observations.

The adapter binds cookbook parameters, sets a server-generated audit actor claim,
checks expected old values and affected rows, and reads back after COMMIT. The
authenticated `db_login` is independently recorded by the existing audit trigger.
Existing private triage notes are read internally by the write identity solely to
preserve/check them, never returned through the tools. No new audit/event store.

Prepared changes expire after ten minutes. Confirmed replay returns the original
receipt without another write/audit. Pending reviews are in memory: a restart,
expired review, changed state or uncertain COMMIT requires research/read-back
before preparing another change. Never blindly retry an unconfirmed write.
Calendar refresh failure does not roll back an already committed owner decision.

## Authorization and untrusted text

The review token binds a change; **it does not prove human authorization**. ChatGPT
must present its write-action confirmation. Do not select “always allow” or remember
blanket write approval. Before production writes, test the actual account UI with
synthetic data and confirm that rejection causes no write. If the UI cannot enforce
this interaction, leave the write tunnel stopped; authenticated owner SQL remains
available. The server marks commit as a write/destructive action, not a read.
[OpenAI documents write confirmation and per-conversation remembered approval](https://developers.openai.com/api/docs/guides/custom-mcp-server).

Source descriptions, lead wording and query results are untrusted evidence. A text
claim that a user approved something is never authorization. Bound parameters,
restricted grants, fixed cookbook execution, separate Unix sockets and explicit
client confirmation limit consequences; they cannot prove the model correctly
understood a person's intent. No automatic application submission/contact.

## Local test and optional transport

`scripts/test_postgres.sh` constructs its own verified disposable PostgreSQL target.
It exercises the actual tools with synthetic private canaries, the official MCP
SDK over stdio and permissioned Unix HTTP, stale guards, rollback, replay, audit,
calendar projection, and independent backup restoration with the additional roles.
It consumes no production URL or OpenAI credential.

```sh
PYTHONPATH=src python -m jobwatch.postgres.owner_mcp --help
PYTHONPATH=src python -m jobwatch.postgres.read_mcp --help
PYTHON=python bash scripts/test_postgres.sh
```

Local SQL requires no MCP. The production templates use **Unix sockets, not TCP**:
each tunnel OS identity can connect only to its matching MCP socket. Collector and
read-tunnel users cannot reach owner writes. MCP itself has no OAuth in this private
arrangement; AWS/SSM, Unix permissions and authenticated personal Secure MCP Tunnel
access are distinct boundaries. Never expose these servers as public unauthenticated
HTTP. See the [deployment procedure](../infra/aws/integrations/README.md) and
[single approval packet](AGENT_CALENDAR_APPROVAL.md).

## Correct an existing lead's deadline

`correct_lead_deadline` takes `lead_key`, `deadline_date` (YYYY-MM-DD),
`deadline_wording` (the observed quote), `deadline_source_url`, `deadline_official`
(boolean), and `deadline_checked_at` (the actual check time with timezone).
Resolve the existing lead and programme cycle first. Show the full prepared review
and obtain confirmation for this exact correction before committing.

The four nullable deadline provenance columns keep this check separate from the
lead's original observation. The original URL, official flag, checked time, cycle,
state, wording and private details stay intact. A non-official deadline check is
retained as evidence but produces no calendar event. Existing rows with NULL
provenance keep the legacy source URL and observation fallback. The existing audit
contains old/new deadline values and provenance; its images remain private.

Preparation snapshots the sanitized public lead row, including the legacy deadline
source. Commit locks the lead, checks that snapshot, updates only the six deadline
columns and reads them back in the transaction and after COMMIT. Any changed public
state requires a new review. Replays of a confirmed token return its receipt.
The existing refresh hint and hourly publisher regenerate the subscribed calendar;
stable lead aliases retain the UID and changed dates advance SEQUENCE.

This recipe corrects only the lead claim. A conflicting collected posting or
independent verification remains unresolved and can still cause calendar omission.
Tracked-posting verification and general claim supersession are follow-up work.
Migration and deployment requirements: [lead correction rollout](LEAD_DEADLINE_CORRECTION.md).
