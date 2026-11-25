# Stage B Proposal: Local-Only Agent Evidence Ingestion & Network-Isolated Corroboration

> **STATUS: QUALIFIED PROPOSAL ONLY — NOT DEPLOYED TO PRODUCTION.**
> Migration `0012_agent_evidence_hardened.sql` and role `jobwatch_agent_evidence` are NOT deployed
> on the production EC2 host, remote evidence ingestion remains disabled, and NO third MCP service
> is introduced.

## 1. Network-Isolated Corroboration Worker Architecture

Application-level SSRF guards (`validate_public_evidence_url`, connection-time IP pinning, hop-by-hop
redirect validation) are necessary but **never sufficient alone**. Any future corroboration worker
fetching agent-supplied URLs MUST run under OS-level privilege and network isolation outside
PostgreSQL, `jobwatch_owner`, `jobwatch_migrator`, and `jobwatch-backup`.

### Dedicated unprivileged OS service boundary

- Dedicated OS account: `jobwatch-corroborator` (member of `jobwatch-db` for peer DB socket ONLY;
  zero read access to `/etc/jobwatch/owner*`, `/etc/jobwatch/migrator*`, `/etc/jobwatch/aws.json`,
  `/var/lib/jobwatch-backup`, or AWS CLI profiles).
- Dedicated PostgreSQL role (or existing `jobwatch_collector` scoped strictly to `SELECT` on
  `agent_evidence_observations` / `postings` / `posting_versions` / `discovery_candidates` and
  `INSERT` ONLY on `agent_evidence_corroborations`).
- Hardened systemd unit template (never run as root or owner):

```ini
[Unit]
Description=Graduate Job Watch isolated public evidence corroborator
After=postgresql.service

[Service]
Type=oneshot
User=jobwatch-corroborator
Group=jobwatch-db
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictNamespaces=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
CapabilityBoundingSet=
MemoryMax=192M
CPUQuota=30%
TimeoutStartSec=90
Environment=JOBWATCH_DATABASE_URL=host=/run/jobwatch-postgresql dbname=jobwatch user=jobwatch_collector
```

### Kernel-level egress firewall (`nftables` per-UID output policy)

Even if Python TLS/HTTP libraries or DNS resolution were compromised, Linux kernel `nftables`
rules keyed on `meta skuid jobwatch-corroborator` block ALL private, loopback, link-local,
and AWS Instance Metadata Service (IMDS) traffic at the IP layer:

```nft
table inet jobwatch_corroborator_egress {
  chain output {
    type filter hook output priority 0; policy accept;
    meta skuid != "jobwatch-corroborator" return

    # Block ALL loopback, private RFC1918, CGNAT, link-local and AWS IMDS (169.254.169.254).
    ip daddr { 127.0.0.0/8, 10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, 100.64.0.0/10, 169.254.0.0/16, 224.0.0.0/4 } drop
    ip6 daddr { ::1/128, fc00::/7, fe80::/10, ff00::/8 } drop

    # Permit ONLY DNS to configured system resolver + outbound TCP 443 (HTTPS) to public Internet.
    udp dport 53 accept
    tcp dport 53 accept
    tcp dport 443 accept
    ct state established,related accept
    drop
  }
}
```

### AWS host defense-in-depth

- EC2 IMDSv2 (`HttpTokens=required`, `HttpPutResponseHopLimit=1`) prevents unauthenticated
  v1 metadata access and container/namespace hop access.
- Instance IAM role has `s3:PutObject` ONLY on backups (no `GetObject`/`DeleteObject`), no
  Secrets Manager owner credentials, and no write access to DB passwords.
- Local PostgreSQL access uses Unix domain socket `/run/jobwatch-postgresql` (`AF_UNIX`), so
  blocking all TCP `127.0.0.0/8` / `::1` traffic from `jobwatch-corroborator` simultaneously
  blocks local MCP/HTTP/admin ports while preserving peer PostgreSQL access.

## 2. Minimal Pathway Into Ordinary Evidence Infrastructure (Avoiding a Silo)

Corroborated observations NEVER silently supersede native `posting_versions`, never auto-insert
into `verifications` or `calendar_selections`, and never activate `sources`. Instead, three narrow,
auditable pathways bridge corroborated evidence into ordinary infrastructure:

1. **New external vacancy (`kind='new_vacancy'`)**:
   - At submission: `submit_agent_vacancy` atomically creates a `discovery_candidates` row
     (`state='new'`, `rules_version='unscreened-agent-submission'`, `verdict='review'`,
     `codes=ARRAY['evidence.agent_submitted_unscreened']`) plus its initial snapshot in
     `discovery_candidate_snapshots` and its `agent_evidence_observations` row.
   - After independent corroboration (`overall_state='claim_corroborated'` with
     `target_binding_status IN ('bound_exact_url', 'bound_host_and_identity')`):
     the trusted collector runs deterministic `discovery.rescreen_candidates(conn)`, replacing
     `unscreened-agent-submission` with the pinned `filtering.RULES_VERSION` verdict/codes.
     The vacancy is then immediately searchable in `research_discovery_candidates` and
     `research_discovery_evidence`, and may be promoted to `leads` via explicit owner action
     (`discovery.promote_to_lead`).
2. **Unknown ATS board (`kind='source_identity'` / `'official_source_link'`)**:
   - After independent corroboration proves strict bidirectional link corroboration
     (`overall_state='claim_corroborated'`), the board may be registered into `discovered_boards`
     as a normal unmonitored board, where it enters the existing bounded discovery & 3-phase
     auto-activation pipeline (strict caps + live `absence_safe` endpoint validation).
3. **Supplemental deadline / posting wording (`kind='deadline_wording'` / `'posting_evidence'`)**:
   - Immediately inspectable in `research_agent_evidence` alongside `posting_deadline_claims`,
     with automatic SQL conflict detection (`has_conflict`).
   - Enters `verifications` (and optionally `calendar_selections`) ONLY on explicit owner
     instruction, citing the `corroboration_id` and `body_sha256` as provenance.

## 3. Minimal Local-Only Stage B Deployment Proposal (Deferred Pending Approval)

When/if approved for a controlled local-only trial:

1. Promote `sql/postgresql/proposals/0012_agent_evidence_hardened.sql` to
   `src/jobwatch/postgres/migrations/0012_agent_evidence_ingestion.sql` and apply via
   `jobwatch-db-migrate`.
2. Provision `jobwatch_agent_evidence` strictly for **local peer/SSM CLI sessions** via
   `sql/postgresql/proposals/agent_evidence_roles.sql` (no public HTTPS endpoint, no ChatGPT
   write connector, no third MCP service).
3. Expose a single local Python CLI (`jobwatch-evidence submit-observation` / `submit-vacancy`
   for `jobwatch_agent_evidence`, plus `jobwatch-evidence corroborate` for `jobwatch-corroborator`).
4. Measure actual yield and utility over 2–4 weeks of local research sessions before deciding
   whether any MCP tool exposure is warranted.

## 4. Example Practical Workflow (Local Research Session)

1. **Discovery during a local research session:** An agent researching a UK employer's careers
   page spots an explicit closing date (`"Applications close 12 December 2026 at 23:59 GMT"`)
   that is absent from the ATS JSON feed for `greenhouse:acmeuk:42`.
2. **Controlled local submission (`jobwatch_agent_evidence`):** The session invokes
   `AgentEvidenceIngestor.record_observation(...)` (calling `jobwatch.submit_agent_observation`).
   PostgreSQL computes `payload_sha256` and `submission_sha256`, records `db_login='jobwatch_agent_evidence'`,
   `logical_contributor='local-cli:2026-11-25'`, `overall_state='submission_recorded'`, and enforces
   hourly/daily budgets. Zero owner tables or calendar selections are touched.
3. **Independent isolated corroboration (`jobwatch-corroborator`):** The network-isolated worker
   fetches the exact HTTPS URL (IP-pinned, `nftables`-jailed, 512 KiB cap), confirms
   `target_binding_status='bound_host_and_identity'` (same employer domain + posting ID/title),
   confirms verbatim wording + parsed date `2026-12-12`, and appends a `claim_corroborated` row
   with `body_sha256` and `evidence_completeness='complete'` to `agent_evidence_corroborations`.
4. **Research visibility & explicit owner decision:** Research SQL (`research_agent_evidence`)
   surfaces the corroborated deadline alongside `posting_deadline_claims` (flagging `has_conflict`
   if it disagrees with any existing claim). Only if the owner explicitly instructs it does an
   audited owner workflow write a `verifications` row and/or add `calendar_selections`.
