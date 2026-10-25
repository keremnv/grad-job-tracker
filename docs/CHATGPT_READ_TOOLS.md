# Stage 4C — PostgreSQL read tools (local/test implementation)

Base: latest AWS/recovery branch `b3ba87faf1dabfdf2ce39d83f15797631a8badaf`,
which includes reference report `3f8f7a4a7d28e4d34a92819eac96f974f5a898f9`.
Implementation branch: `feat/chatgpt-read-interface`. No AWS access, hosted role,
credential, endpoint, tunnel, workflow switch or production-data import occurred.
The Git-backed tracker remains authoritative.

## Contract

`jobwatch.postgres.read_tools.ReadTools.call(tool, arguments)` is synchronous,
transport-independent and uses ordinary psycopg connections. It returns
`{schema:"jobwatch.read/1",ok:true,trust:...,data:...}` or
`{schema:"jobwatch.read/1",ok:false,error:{code,sqlstate}}`. Errors never echo SQL,
DSNs, driver DETAIL or database values. Every data response labels database text
as untrusted data, never instructions. Private owner data remains private.

| Tool | Exact semantics |
|---|---|
| `sql_query(sql, limit, parameters=null, offset=0)` | Any single row-producing PostgreSQL query expression: SELECT, WITH, VALUES or TABLE; joins, aggregates, JSON operators and functions work normally. Psycopg `%s` positional lists or `%(name)s` named objects bind values; use explicit SQL casts (e.g. JSON strings with `::jsonb`). SQL identifiers remain ordinary SQL, not interpolated parameters. One optional final semicolon. Validated outer LIMIT/OFFSET bounds fetches. Administrative/session/COPY/EXPLAIN commands are not query expressions. |
| `sql_schema(table=null)` | Jobwatch tables/views, column types/nullability/generated/identity flags, indexes, constraints (including composite/deferred FKs), and view definitions. Use one relation name if the full schema exceeds the response limit. |
| `search(query, method="bm25", history=false, include_not_seen=false, limit=20)` | Calls existing `research.search` unchanged. Broad all-verdict research; current listed versions by default, explicit history/absence options. Returns actual ranking method, snippets, source freshness and untrusted-text labels. C++/C#/C distinctions remain the BM25 baseline; FTS is English PostgreSQL tokenisation. |
| `inspect_evidence(posting_key, version_id=null, events_limit=50)` | Existing same-posting evidence helper returns current or exact historical persisted version, provenance/normalizer/current flag and parsed native values. Adds original bytea as base64/length/SHA-256 and latest posting events, ordered by descending event ID. Use ordinary SQL for complete event/version pagination. |
| `run_report(kind, since=null, limit=50)` | Existing daily/weekly/requirements Markdown or calendar ICS transformations. Same joins, deadlines, uncertainty and privacy rendering; no files, re-screening, network checks or writes. |

SQL results use `columns:[{name,type_oid}]` and positional `rows:[[...]]`, preserving
duplicate column labels. Dates/UUIDs/decimals use strings; integers beyond JS-safe
precision and nonfinite floats use explicit typed text objects. Other PostgreSQL
types use text with column OID metadata. Binary values use
`{encoding:"base64",data,bytes,sha256}`. Parsed evidence retains escaped NUL in JSON;
its original bytes are always available. It is parsed provider evidence, not an
HTTP-byte archive. Source wording is never executed or followed as instructions.

Limits: explicit SQL limit **1–200**, offset **0–1,000,000**, SQL **16 KiB**, tool
arguments **32 KiB**, JSON result **128 KiB**. `has_more`/`next_offset` reflect the
extra fetched row; provide ORDER BY for meaningful pagination. Separate calls
observe separate snapshots, not a persistent pagination session. Oversized results
fail with `response_too_large`, without a silently truncated evidence version.
Read smaller projections or bytea/text slices through SQL. MCP can contain both
text and structured copies of that bounded result. HTTP request bodies cap at64KiB.

SQL statement timeout is **5 s**, lock timeout **1 s**, idle transaction timeout
**10 s**, proposed role transaction timeout **15 s**. A separate cancellation
watchdog prevents SQL from bypassing the deadline with set_config. Server cursors
fetch one row at a time. These are execution/result controls, not a complete
PostgreSQL CPU/memory sandbox for adversarial SQL; one huge expression can still
consume resources before cancellation. Authenticate trusted owner-approved users
and apply deployment-level concurrency/rate controls before internet exposure.

BM25 needs its full matching corpus: >10,000 eligible versions returns
`working_set_too_large`, not a filtered ranking. Reports refuse >10,000 rows in
any fully read posting/application/lead/verification working set. Indexed FTS and
bounded ordinary SQL remain available. No new index, ORM or storage is added.

## Database identity and security

[chat_readonly.sql](../sql/postgresql/chat_readonly.sql) is **optional administrative
provisioning**, not migration 0004. Run only after migrations 0001–0003 as a role
administrator with schema-grant authority. It fails on an existing role; no silent
adoption or password reset. It has been executed only in disposable PostgreSQL.

The proposed `jobwatch_chat_readonly` login has a NULL password and connection
limit3. It may SELECT explicitly listed application/research/history/evidence,
private applications/triage/leads/verifications/audit, checksum ledger and all four
existing views. Only url_identity gets application-function EXECUTE. No write,
sequence, ownership, schema/database CREATE, role membership/admin, server-file,
replication or migration privileges are granted. Existing owner/collector/migrator
and PUBLIC grants, migrations and audit triggers are unchanged. New future tables
are not automatically exposed.

The core refuses every other authenticated identity, role memberships or elevated
flags. Every call gets a fresh REPEATABLE READ/READ ONLY transaction, always rolls
back and closes, and has a per-process concurrency bound3 in addition to the login
limit. The SQL cursor uses PostgreSQL's extended protocol, so multiple commands
fail Parse; query-expression grammar rejects commands. Database privileges and
READ ONLY independently stop writes, including invoked SECURITY DEFINER functions.
There is no SQL keyword blacklist. MCP read-only annotations are hints, not the
security boundary.

PostgreSQL USERSET role defaults are not irrevocable. A person with the raw
credential can change those defaults; inherited PUBLIC temp/builtin rights are
not revoked by this proposal because that would change established grants. The
credential stays server-side: the actual tool interface forces read-only mode,
rejects transaction-control commands and discards all session settings afterward.
Do not describe this login as an immutable server-wide write firewall or expose
its password to ChatGPT/end users. No existing role permissions are weakened.

ChatGPT tools can read the approved private scope, including notes and audit
images; they cannot write owner decisions or collector observations. Codex using
this same core gets the same restrictions. Ordinary authorized Codex/psql/psycopg
work remains independent of MCP: owner SQL can change owner data with required
actor attribution; collector operations retain source locks/transaction invariants;
migration credentials remain separately controlled. Neither owner nor chat tools
can rewrite immutable evidence under their normal grants.

## Local usage and MCP interoperability

```sh
python3 -m venv /PRIVATE/read-tools-venv
/PRIVATE/read-tools-venv/bin/pip install -e '.[postgres]'   # no MCP required
# JOBWATCH_CHAT_DATABASE_URL comes from private environment/libpq service config.
# No fallback to JOBWATCH_DATABASE_URL, owner, collector or migration credentials.
printf '%s\n' '{"tool":"sql_query","arguments":{"sql":"SELECT verdict,count(*) FROM jobwatch.postings GROUP BY verdict","limit":20}}' \
  | /PRIVATE/read-tools-venv/bin/jobwatch-read-tools

/PRIVATE/read-tools-venv/bin/pip install -e '.[chat-mcp]'
/PRIVATE/read-tools-venv/bin/jobwatch-read-mcp --transport stdio
/PRIVATE/read-tools-venv/bin/jobwatch-read-mcp --transport streamable-http --port 8765
# HTTP binds ONLY 127.0.0.1, endpoint /mcp. No authentication is supplied here.
# Do not publish/forward this unauthenticated listener directly.
```

The official Python MCP SDK is an optional dependency (`mcp>=1.30,<2`); tested
version1.30.0. It owns initialization, negotiation, list/call tools and framing.
The five tools advertise readOnlyHint=true/destructiveHint=false. Core failures
become standard MCP isError results; stdio stdout is reserved for protocol output.
Local tests used the official SDK client against both stdio and Streamable HTTP,
including all tools, errors and bounded output. They are **not an online ChatGPT
connection test**. Nothing was added to personal Codex/MCP settings.

For Codex, optionally configure an MCP stdio command pointing to the installed
read-mcp executable and pass the private environment through the host's secret
configuration. Ordinary SQL/Python is equally supported; MCP is never mandatory.

## Online ChatGPT options and current access restrictions

Checked25October2026 against [official custom MCP guidance](https://developers.openai.com/api/docs/guides/custom-mcp-server)
and [private Secure MCP Tunnel guidance](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels).
Current custom connections support Streamable HTTP/SSE and authentication choices;
this implementation supplies stdio and Streamable HTTP. Use either:

1. A separately approved stable HTTPS MCP endpoint with OAuth/access control in
   front of this private listener. Keep PostgreSQL private; never publish5432 or
   expose the current unauthenticated HTTP server with private records.
2. An approved OpenAI Secure MCP Tunnel from a machine already able to reach the
   private MCP server/database. It uses outbound connectivity and can forward
   stdio/HTTP. It needs Platform tunnel permissions, a runtime API credential and
   association/access in the target ChatGPT workspace; those are separate from
   ChatGPT workspace/developer-mode permissions. An operator workstation could use
   the existing approved SSM SQL-forwarding design, with the new database identity.
   No such tunnel or credential was created.

The current web guide uses Plugins → Add custom MCP server → Server URL or Tunnel,
then authentication, risk review, creation/installation and selecting the plugin.
Workspace permissions and Lockdown can prohibit access. Enterprise/Edu developer
permissions require administrator approval. The fetched documentation does **not
establish this owner's Plus account eligibility** for custom creation or a tunnel.
Check the actual account/workspace controls and current plan access before choosing
or purchasing anything; a Plus subscription alone is not verified access. This
repository is not already connected to ChatGPT, including Plus.

No OpenAI model/API call is made by these tools. Local use adds no AWS resources
or service cost. Online serving adds compute/runtime and potentially tunnel/API
or subscription usage costs according to the selected product; no flat tunnel
price or inclusion in Plus is assumed. The existing AWS artifact builder does not
bundle the new optional MCP dependencies: a future transport deployment needs its
own reviewed package/runtime. No hosted resource sizing or cost claim is made.

## Approvals still required

Before live use: approve private-data scope (including historical owner/audit
images) and sharing with the chosen ChatGPT workspace; provision the dedicated
SQL role/authentication and scoped SSM/service access; choose and approve transport
hosting/tunnel, OAuth/authentication, secret custody and access revocation; verify
actual plan/workspace eligibility; review any AWS/IAM/network/cost changes and
new runtime package. Test live identity, grants, authentication, bounded reads and
revocation with synthetic data before exposing real records. Keep the current
production/cutover and migration gates separate. No new role on AWS, credential,
public listener, tunnel, production import or authority switch is authorized here.

## Local retrieval extension

Migration 0004 and [the retrieval contract](POSTGRESQL_RETRIEVAL.md) add the owner-selected text-only EmbeddingGemma 2 profile, exact cosine and opt-in hybrid search. `search` accepts vector/hybrid methods and `lexical_method`; lexical defaults and SQL freedom remain. Query inference is a separate bounded local process before the SQL transaction; read-only response/SQL limits still apply. No online deployment or hosted login has occurred.
