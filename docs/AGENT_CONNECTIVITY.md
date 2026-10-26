# Agent connectivity qualification (Stage 4F)

**Current production boundary, 26 October 2026:** PostgreSQL is now authoritative
on AWS; [the handover](AWS_PRODUCTION_HANDOVER.md) records the qualified release,
vector/migrations, recovery and owner SQL access. The synthetic MCP results below
do not grant production sharing: no production chat-read login, hosted MCP process
or tunnel has been activated. Dated host comparisons below are historical.

The existing five read tools passed local qualification, followed by a real
synthetic-only ChatGPT `sql_query` call over a private OpenAI tunnel. It is not
hosted production access; see the [precise result](AGENT_CONNECTIVITY_REPORT.md#online-synthetic-milestone--complete).
Ordinary psql/psycopg SQL
remains primary and independent of MCP. No mandatory query sequence or research
policy is introduced. [Implementation scope](AGENT_CONNECTIVITY_PLAN.md).

## Recommended first connection

Run the read MCP process on an existing trusted operator/development machine.
After separate hosted-access approval, use the existing authenticated SSM
loopback PostgreSQL forwarding design. Keep the database private. Use only the
dedicated `jobwatch_chat_readonly` identity for this process; owner SQL, collector,
embedding writer, migrator and AWS administration credentials remain separate.

```text
ChatGPT (if eligible) -> authenticated OpenAI Secure MCP Tunnel
  -> tunnel-client on trusted machine -> private stdio/loopback MCP
  -> encrypted, authenticated SSM forwarding -> PostgreSQL read identity

Codex/operator -> ordinary psql/psycopg over SSM -> appropriate SQL identity
Proton -> separately approved HTTPS minimal ICS endpoint (not the MCP tunnel)
```

The private tunnel route has now worked in the owner's intended personal context
with a disposable fixture. That test runtime is stopped; plugin disconnection and
key revocation are owner-reported, not independently verified. Production access
still needs its own approval, scope and credentials. The route avoids a public MCP endpoint and new
cloud compute. The trusted machine must stay awake, connected and patched while
ChatGPT calls it. Disconnects fail closed; they must not fall back to owner SQL.
An always-on private MCP process on EC2 with **inference disabled** is a possible
later lexical route, but its runtime/deployment/access still need approval.

## Current OpenAI documentation versus account access

Checked 25 October 2026 using the official sources below. The subsequent online
synthetic test established this owner's creation/use path; it does not establish
other workspaces' entitlement, hosted access or tunnel pricing. The table separates
documented support from observed qualification and remaining limits:

| Documented | Qualification and remaining limits |
|---|---|
| Web Plugins → + → Add custom MCP server; URL or Tunnel; configure authentication, create/install and invoke the plugin. Streaming HTTP/SSE supported; arbitrary tools need not be renamed search/fetch. Workspace restrictions apply. [Custom MCP](https://developers.openai.com/api/docs/guides/custom-mcp-server) | Creation and a real synthetic SQL call worked in the owner's personal context. This does not establish every Plus workspace's entitlement or qualify production data. |
| Plugins can contain MCP servers and/or skills. Installation and service connections have separate lifecycles. [Plugins](https://learn.chatgpt.com/docs/plugins) | Installed connector/catalog tools do not establish permission to add this custom server. No plugin was installed or suggested by this stage. |
| Secure MCP Tunnel forwards private stdio/HTTP through outbound HTTPS, without inbound access. It requires a tunnel ID, runtime API key, Platform permissions and target-workspace associations. Creation needs Read+Manage; runtime/use needs Read+Use. Personal contexts are described. [Tunnels](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels) | Actual private tunnel/runtime/ChatGPT routing succeeded for the synthetic fixture. Runtime stopped; owner reports plugin/key withdrawal. Pricing and future real-data approval are separate. |

The fetched custom-server/Plugins/tunnel documentation does **not establish this
owner's personal Plus entitlement or tunnel price**. The
[pricing page](https://learn.chatgpt.com/docs/pricing) lists Codex plugin/MCP
availability on Plus; that is not proof of this particular ChatGPT custom-server
or tunnel control. No upgrade or API credit purchase is recommended on that basis.
The stage report now records the successful synthetic UI/tunnel/tool call.

For a different, not-yet-approved context, check creation/Tunnel controls without making a
connection. If absent, retain direct Codex SQL and local MCP; confirm current
eligibility with OpenAI before paying for a different plan. With separate approval,
complete the documented tunnel/client setup and then make a synthetic ChatGPT
tool call. Only that observed call can qualify **online ChatGPT**. Local SDK tests
do not test ChatGPT model tool selection, account restrictions or tunnel routing.

## Reproducible local runtime and probe

```sh
uv venv --python 3.12 /PRIVATE/jobwatch-read-venv
uv pip install --python /PRIVATE/jobwatch-read-venv/bin/python --require-hashes \
  -r requirements/agent-read.lock
uv pip install --python /PRIVATE/jobwatch-read-venv/bin/python --no-deps .
# Provision migrations/optional read role ONLY in the disposable development DB.
# Set JOBWATCH_CHAT_DATABASE_URL via private environment/libpq config, never argv.
/PRIVATE/jobwatch-read-venv/bin/jobwatch-read-mcp --transport stdio
/PRIVATE/jobwatch-read-venv/bin/jobwatch-read-probe
# Or test an already running LOOPBACK-only server:
/PRIVATE/jobwatch-read-venv/bin/jobwatch-read-mcp --transport streamable-http --port 8765
/PRIVATE/jobwatch-read-venv/bin/jobwatch-read-probe --http http://127.0.0.1:8765/mcp
```

The lock pins MCP 1.30.0, psycopg 3.2.13 and all dependencies/hashes for a model-free
runtime. No OpenAI SDK, API key, AWS SDK or model is needed for local tests. MCP
remains optional; no existing AWS release builder/host is modified. Hash-lock
refreshes should be reviewed/tested, not silently updated at service startup.

The probe uses the official MCP SDK to negotiate/list/call existing tools. It
composes bound multi-table SQL, all-verdict corpus/history reads, BM25/FTS,
bytea evidence checksum verification, owner/audit aggregate reads and existing
weekly/calendar reports. It checks absent mutation/file privileges without
sending a potentially mutating command; actual denial attempts run only in the
verified disposable tests. It tests bounded errors and explicit inference state.
Receipts contain check names, timings, response hashes/sizes and aggregate counts,
not source text, keys, notes, URLs or credentials. Exit 0 is local protocol/read
qualification; 2 is failure. No receipt implies production deployment readiness.

Stdio launch forwards an explicit environment allowlist; owner/migration/embedding
DSNs, AWS/OpenAI keys and ambient PG endpoint/options are omitted. This is not an
OS sandbox: the process retains its OS user's filesystem privileges. Use a trusted
machine/user, private credential files and a separate runtime environment. The
probe starts no tunnel, login, migration, collector, calendar publication or cloud
API call. Its HTTP option refuses non-loopback/credential-bearing URLs.
It also disables ambient HTTP proxies and redirect following for that connection.

An [optional Codex config example](../examples/agent-connectivity/codex-mcp.toml.example)
uses the documented [stdio configuration](https://learn.chatgpt.com/docs/extend/mcp).
Review/install it locally only after approving the actual database connection;
this stage does not edit personal Codex settings. The five tools and SQL contract
are unchanged. A complete `sql_schema()` can exceed response bounds; scope one
relation or paginate ordinary catalog SQL.

## Query inference placement

EmbeddingGemma 2's measured **1.84 GiB process RSS** is not a safe fit beside
PostgreSQL/OS on the current 2 GiB host. Keep the pinned text-only float32 model
and document indexing/query inference on a suitably sized existing local machine.
No resize, remote model API or inference microservice is introduced. Model/RAM and
cache preparation are independent of database hosting.

`jobwatch-read-mcp` now defaults to `--query-inference disabled`; vector/hybrid
requests return `query_inference_disabled`, never silently invoke a model or rank
lexically under a vector label. SQL/BM25/FTS and embedding coverage remain available.
The tool description and server instructions disclose the mode. Ordinary SQL can
still inspect/join stored vectors. Existing direct Python/JSON tools retain their
local encoder behaviour; the default change is scoped to the MCP executable.

For a suitably sized local runtime, install both reviewed locks together:

```sh
uv pip install --python /PRIVATE/jobwatch-read-venv/bin/python --require-hashes \
  --torch-backend cpu -r requirements/agent-read.lock -r requirements/embeddings-cpu.lock
# After existing model-access/cache preparation; no token is created by this stage.
jobwatch-read-mcp --query-inference local
jobwatch-read-probe --query-inference local
```

Reuse the exact profile/revision/formatting in [retrieval](POSTGRESQL_RETRIEVAL.md).
Do not put embedding-writer credentials in the query server. Cold model load and
inference are outside the SQL timeout; require bounded local concurrency and
measure end-to-end latency against the documented tunnel's 120-second budget
before online activation. Losing the local machine loses vector query availability,
not SQL/source evidence. If an always-on EC2 lexical server is chosen, vector/hybrid
availability must remain explicitly incomplete until an approved inference route
exists; no new service is justified by that gap alone.

## Deployment/authentication approval gates

| Boundary | Required configuration/custody and withdrawal |
|---|---|
| Hosted schema/runtime | Last committed hosted qualification is release `1b72d0d`, migrations 0001–0003, empty domain tables. It was **not rechecked here**. Stage 4D/E/F is local. Separately approve compatible release, pgvector/0004 and optional runtime provisioning; no automatic migration on connection. |
| Database read identity | `sql/postgresql/chat_readonly.sql` now references 0004's embedding table. Approve the existing explicit private-data scope, then deliberately create/authenticate this login. Existing host HBA permits loopback **owner**, not this new role: approve a narrowly scoped same-database SCRAM rule/credential (or separately reviewed local peer identity). Keep credentials server-side in owner-controlled 0600 passfile/private service config. Never use owner/migrator as fallback. Revoke login/rotate credential and terminate existing read sessions to withdraw access. |
| SSM | Reuse the existing scoped authenticated port-forwarding design after approval; no public PostgreSQL/SSH. SQL-only access must not gain shell, RunCommand or deployment administration. Close/terminate the session and revoke temporary AWS access when unused. Tunnel access is not SQL owner authority. |
| OpenAI tunnel | Confirm eligibility/cost first. Owner/admin creates/associates the tunnel and restricted runtime Read+Use key through approved local secret storage; management privileges stay operator-only. Keep the tunnel key out of MCP's environment and transcripts. Separate private MCP process from tunnel-client secrets where possible. Stop client, revoke key/tunnel association and disconnect the custom plugin to revoke. Uninstall alone need not remove its connection. |
| MCP auth/scope | Private stdio/loopback relies on trusted local access plus authenticated/authorized tunnel context. A No-auth **local upstream** is not a public unauthenticated service. Limit to the intended personal context; broader users require separately designed per-user authorization. A public HTTPS fallback needs OAuth/token validation and standard discovery; the existing server has none and must never simply be port-forwarded publicly. |
| Data sharing | The read role can read private applications/triage/leads/verifications and **audit before/after notes**, not just minimal calendar fields. Approve that scope and ChatGPT/workspace data controls before real connection. Read-only is not a confidentiality boundary: results go to the authorized client/OpenAI context. Tool annotations/trust labels are not prompt-injection protection by themselves. |
| Monitoring | Watch MCP/client readiness, SSM loss, fixed error codes, bounds, concurrency, schema/checksum/profile availability and latency. Log tool/status/latency only; no SQL/arguments/results, credentials, source text or private URLs. Define recipient and retention. Review local logs/doctor exports before sharing. Do not add high-cardinality query logs or an observability service here. |

Before real records are shared, check the actual ChatGPT Data Controls, model
improvement, retention/history and any workspace policies with the owner; no
particular setting is assumed enabled/disabled. Revocation prevents future reads,
not retrieval of previously shared text from a client's chat/cache. Likewise,
rotating a calendar URL cannot erase events already fetched by Proton/clients.

## Independent Proton feed arrangement

Reuse [Stage 4E's atomic minimal snapshots](PROTON_CALENDAR.md); no second event
generator or owner ledger. Proton cannot consume OpenAI's authenticated MCP tunnel
or a private SSM SQL forward. It needs a stable, revocable HTTPS ICS endpoint.

Smallest conventional proposal: serve only fixed current ICS files through one
small TLS/static reverse proxy on the existing host, with independently revocable
unguessable feed URLs, no directory listing/manifest/database access, conditional
ETag/Last-Modified and redacted access logs. Snapshot generation is a separate
read-only local operation; retain its private UID/sequence metadata for recovery.
No OAuth login page should be required for Proton's background fetch; the capability
URL itself is a secret, disclosed to Proton and potentially synced clients.

This is **not deployed** and would require separate approval of stable DNS/domain,
TLS, HTTPS ingress/host maintenance, token custody/rotation, privacy scope and
publication/freshness monitoring. The existing zero-inbound security group is not
changed. Do not use the backup bucket or a short-lived presigned URL. Evaluate an
already owned HTTPS host instead if it avoids new public ingress/cost, but approve
that provider/data destination first. No new service/provider is selected here.
Any future Terraform plan must also address the known bootstrap user-data
replacement proposal without replacing the protected instance.

Proton refreshes approximately 4–16 hours and has no event-specific subscription
notifications; separate calendars allow calendar-wide defaults. This is not
guaranteed urgent alerting. Actual subscription, update/removal and revocation tests
remain gates. Hosting, DNS/transfer and tunnel/product charges are unverified;
no new recurring AWS/model/API cost was incurred. Preserve the £10–£20/month target;
credits do not turn uncertain new charges into an approved budget.

## Next authorised qualification to request

1. Synthetic online UI/routing is complete. Before any real-data connection,
   confirm current terms, trusted-gateway availability and explicit private-data scope.
2. Re-authorise any runtime credential/connection for that new scope; the synthetic
   runtime is stopped. Do not treat the earlier test as approval to share owner or
   audit records, restart a tunnel or run inference on the database host.
3. Then separately approve hosted schema/runtime/read identity/HBA and scoped SSM
   access before testing against AWS; approve real private-data scope before use.
   Independently approve Proton HTTPS hosting and test a synthetic subscription.
4. Retain all V1 activation gates: [AWS-only backup/restore policy](AWS_BACKUP_POLICY.md), notifications,
   nine genuine ATS checks, historical-evidence acceptance, integration preserving
   newer main observations, final writer freeze/commit, import/reconciliation and
   independent restore, then explicit single-writer cutover approval.
