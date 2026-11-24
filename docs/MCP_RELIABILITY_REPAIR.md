# Research MCP reliability repair — 24 November 2026

Repairs the 17:52 UTC cgroup OOM of `jobwatch-agent-read` (see the incident
diagnosis summarised below). Implemented and deployed the same day with owner
approval. ChatGPT-side `SELECT 1` had already succeeded after the automatic
restart, independently confirming the earlier host-side root cause.

## Incident recap (evidence)

At **17:52:40 UTC** the kernel killed the Research MCP process
(`CONSTRAINT_MEMCG`, anon-RSS ~196 MB against the 192 MiB `MemoryMax`); the read
tunnel posted 12 dispatcher 502s (all of that day's errors, 17:52:40–55) and
ChatGPT surfaced generic tool errors for every Research tool. systemd restarted
the service at 17:52:55. PostgreSQL, collection, backups, calendar and the
Owner Actions MCP/tunnel were unaffected. The same OOM had occurred four times
on 18 November without alerting.

## Memory characterisation (isolated, same OS user/groups, production data)

Bare `ReadTools` process, RSS peak (ru_maxrss):

| Scenario | Before | After repair |
| --- | ---: | ---: |
| Idle / imports | 27 MB | 27 MB |
| `SELECT 1`, schema, FTS | 36 MB | 36 MB |
| BM25 current listed (638 postings) | 84 MB peak, 71 MB retained | 72 MB peak, 41 MB retained |
| BM25 history (2,107 versions) | **198 MB peak**, 151 MB retained | 155 MB peak, 42 MB retained |
| 3× concurrent BM25 history | 288 MB peak | 157 MB peak |

Conclusions: growth is **corpus-dependent** (history ≫ current), the per-request
peak is transient but glibc/Python arenas **retained** it as resident memory, so
repeated and concurrent requests ratcheted toward the cap. FTS/SQL/schema are
cheap because ranking happens in PostgreSQL.

## Root cause in code

`research._lexical_search` (BM25 branch) held three corpus representations at
once — fetched rows including every `description_text`, a joined document
string per version, and a token `Counter` per version — inside one DB
transaction, multiplied by up to three concurrent MCP slots
(`ReadTools.slots`). A single history search could therefore exceed 192 MiB
unaided; concurrency and arena retention made it worse.

## Changes (narrow, ranking-identical)

- [search.py](../src/jobwatch/search.py): `BM25.from_counters` indexes
  pre-tokenised documents without retaining source text; the text constructor
  delegates to the same initialisation, so scores are unchanged.
- [research.py](../src/jobwatch/postgres/research.py): BM25 tokenises row by
  row, dropping each description from the fetched row before indexing (snippet
  bodies are kept separately and freed after ranking). No post-ranking query is
  issued inside the transaction. One corpus pass, one index.
- [read_tools.py](../src/jobwatch/postgres/read_tools.py): BM25 (and hybrid
  with BM25 lexical) requests are serialised with a dedicated lock so
  concurrency cannot multiply the corpus peak, and `malloc_trim(0)` runs after
  each BM25 call so the peak is returned instead of retained. Other methods
  keep the existing three concurrency slots. Security boundaries (roles,
  read-only transactions, bounds, timeouts) are unchanged.
- MemoryMax for the read MCP: **192 MiB → 256 MiB**
  ([unit](../infra/aws/integrations/systemd/jobwatch-agent-read.service)).
  Smallest justified size: worst measured post-repair peak 157 MB in a bare
  process; the real server's idle baseline is ~12–20 MB higher, leaving ~35%
  headroom for moderate corpus growth while the 10,000-version
  `working_set_too_large` guard still bounds the worst case. The ceiling is
  not a reservation; observed worst-case coincidence with PostgreSQL (peak
  240 MB), owner MCP, calendar and OS remains well within the 1.8 GiB host.
  No EC2 resize.

## Deployment evidence (host, 24 Nov 2026)

All pending unit diffs were inspected first: every integration/backup unit on
the host was byte-identical to its repository template, so
`systemctl daemon-reload` (18:23 UTC) only cleared the stale
`NeedDaemonReload` flag — it restarted nothing, PostgreSQL included.

The three patched modules were installed into the agent release
(`926d951…` site-packages) as a documented host delta matching this repository
state; originals plus SHA-256 manifest are preserved root-only in
`/root/jobwatch-repair-backup-20261124/` (rollback = restore those files and
the backed-up unit, daemon-reload, restart the read service). The unit
`MemoryMax=256M` was applied in the same step. The read service and its tunnel
restarted once at 18:23:05 UTC and again at 18:27:36 UTC for the snippet fix;
`NRestarts=0` thereafter, `MemoryMax=268435456` live.

Post-repair production verification (native socket, no tunnel): `initialize`,
five tools, `SELECT 1`, `sql_schema`, BM25 current all pass; restricted-role
`SELECT 1` as `jobwatch_agent_readonly`/`jobwatch_agent_owner` passes; a
3× concurrent history search peaked at 172 MB (no OOM, service survived) and
returned the pre-existing bounded `timeout` error rather than dying — see
limitations. Current RSS after heavy searches returns to ~42–62 MB.

## Monitoring (no new persistent service)

Reuses the backup-health pattern: a 15-minute oneshot timer
([service](../infra/aws/systemd/jobwatch-mcp-health.service) /
[timer](../infra/aws/systemd/jobwatch-mcp-health.timer)) runs
[mcp_health.py](../infra/aws/scripts/mcp_health.py) as the read identity. It
performs a native MCP `initialize` + `SELECT 1` probe over the Unix socket,
reads `NRestarts`, and emits one allowlisted
`{operation:"mcp_health", mcp_healthy, probe_ok, restarts, restarts_delta}`
summary through the existing operational log publisher. A restart delta, an
inactive service or a failed probe is unhealthy. CloudWatch metric filter
`jobwatch-mcp-health` → `JobWatch/McpHealthy`, alarm
**grad-jobwatch-mcp-health** (Minimum <1, 900 s × 3, 2 datapoints, missing
data breaching) notifies through the existing SNS topic; the topic policy
gained exactly one statement, `AllowReviewedMcpAlarm`, rendered by
[render_mcp_alert_policy.py](../infra/aws/scripts/render_mcp_alert_policy.py)
and applied via `SetTopicAttributes` (originals verified unchanged, as with
the calendar amendment). Terraform declarations live in
[integrations/main.tf](../infra/aws/integrations/main.tf) and
[backup_alerts.tf](../infra/aws/backup_alerts.tf); the infrastructure root was
not applied.

Verification: the checker succeeded at 18:24 (`mcp_healthy:1`, exit 0, timer
enabled, next 18:30); a deliberate bad-socket run emitted `mcp_healthy:0` and
exited 2. Timer datapoints: 18:30 = 1.0. Note the failure-path test published
one real `0` datapoint (18:15 period), which with missing-data-breaching held
the new alarm in ALARM until two healthy periods accumulated — it recovered
to OK at **18:55:27 UTC** after the 18:30 and 18:45 healthy datapoints,
proving both the ALARM and OK notification paths for the new statement.

## Remaining limitations

- **Native health ≠ ChatGPT routing.** The probe proves host → PostgreSQL →
  MCP over the Unix socket. It cannot prove OpenAI control-plane routing,
  tunnel association or ChatGPT session state; a green probe with a failing
  ChatGPT conversation should be retried in a fresh conversation before
  suspecting the host. Tunnel health (`ready:true`) is reported separately by
  the tunnel client and is not yet alarmed.
- BM25 **history** search (2,107 versions) exceeds the existing 5-second
  tool timeout under the service's 25% CPU quota and returns a bounded
  `timeout` error; the process survives and FTS history remains available.
  Raising the timeout or quota was not justified by this repair.
- BM25 remains an in-Python full-corpus index per request (now single-pass,
  serialised and trimmed). Corpus growth toward the 10,000-version guard will
  raise the peak; re-measure before it approaches ~4,000 versions.
- The checker detects failures within ~15–45 minutes (two-period alarm), not
  instantly; ChatGPT users remain the fastest signal for tunnel-side faults.
- Host delta: the host runs these patched modules ahead of the next packaged
  release. The next release build from this repository state supersedes the
  delta; do not hand-edit the release directory again.

## Closeout — packaged release supersedes the host delta (24 Nov 2026)

Reviewed implementation committed as `f3d18cd` (this document included).
Reproducible artifacts built from that commit — app SHA-256
`8ed0f530…`, agent SHA-256 `3b1f6c5f…` — were installed through the existing
guarded installers (collection/backup timers paused 19:44–19:47 UTC, then
restored; integration units paused for the agent install). The host now runs
`/opt/jobwatch/releases/f3d18cd…` and
`/opt/jobwatch-agent/releases/f3d18cd…`; installed module SHA-256 values match
the committed sources exactly, so the temporary site-packages delta is fully
superseded. Transfer objects in the state bucket were deleted after install.
Monitoring state was reconciled by importing the live metric filter and alarm
into the integrations Terraform state; the only applied change added the
default tags to the imported alarm (0 added/destroyed, final plan clean).
The root SNS-policy declaration for `AllowReviewedMcpAlarm` matches live, as
with the calendar amendment (no root apply). Post-deployment native probe,
restricted-role `SELECT 1`, MCP-health run (`mcp_healthy:1`, 19:49 UTC), all
six alarms OK and all pre-existing schedules were verified. Rollback evidence
is preserved: `/root/jobwatch-repair-backup-20261124/`, both `926d951…`
releases, and the S3 state versions.

## Explicit follow-up items

1. **Historical BM25 timeout.** BM25 over all retained versions (2,107 at
   repair time) exceeds the 5-second tool deadline under the read service's
   25% CPU quota and returns a bounded `timeout`. Options to evaluate later:
   raise the quota/timeout for this service only, or move corpus tokenisation
   out of the request path. FTS history is unaffected.
2. **Corpus-dependent memory scaling.** BM25 peak grows roughly linearly with
   eligible versions (155 MB at 2,107). Re-measure at ~4,000 versions and well
   before the 10,000-version `working_set_too_large` guard; revisit
   `MemoryMax` or the index strategy then.
3. **Allocator-specific mitigation.** The `malloc_trim(0)` release depends on
   glibc arena behaviour on this ARM64/Python 3.12 runtime. It is guarded
   (failure is a no-op) but must be re-verified on any Python/glibc upgrade;
   if a future runtime retains memory differently, process recycling or an
   index redesign replaces it.
4. **Tunnel monitoring limitation.** The new alarm covers native MCP
   availability and restarts only. Tunnel-client registration, control-plane
   routing and ChatGPT session state have no alarm; a green probe with a
   failing ChatGPT conversation is a client-side signal first. Alarm the
   tunnel health socket (`ready`) only if a real tunnel-side failure mode is
   observed.

## Separate coverage item (not implemented)

Workable ingestion is **not** implemented (only Greenhouse/Ashby/Lever exist
in `feeds.py`, configuration and production sources). Adding a Workable
adapter is recorded here as a separate coverage improvement for subsequent
discussion; no Workable work was done in this repair.
