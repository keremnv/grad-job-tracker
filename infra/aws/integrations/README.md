# Optional private agent and calendar integration

**Prepared and locally tested only. Not installed, published or connected.**
The existing SQL-first production collector, backups, timers, EBS, state and
private V1 release remain unchanged. This directory is a separate Terraform root
so the known older EC2 user-data replacement proposal is never applied incidentally.
Do not run it through `infra/aws/` or remove EC2/EBS deletion protections.

## Why this deployment

Use the existing EC2 for model-free MCP and calendar projection. A separate,
feed-only private S3 bucket and CloudFront default HTTPS hostname serve ICS.
Exposing the host directly would add an inbound web listener, certificate/hostname
maintenance and database-host attack surface. Reusing the backup bucket would
unnecessarily mix downloadable feeds and recovery archives. The small derived-data
bucket avoids that risk; no new VM, domain, NAT, load balancer or inference server.
Direct presigned S3 URLs expire with their authorization and would require replacing
the Proton subscription URL. Anonymous S3 object access would require relaxing
public-access protections. CloudFront keeps the origin private while providing a
stable, separately revocable capability path.

The module proposes **14 managed resources**: bucket, versioning, encryption,
ownership, public-access block, lifecycle, bucket policy; CloudFront OAC, function,
cache policy and distribution; one additive existing-instance role policy;
one log metric filter and one alarm using the existing SNS topic. The managed
security-headers policy is a read-only data lookup. Regional resources remain
Stockholm; the aliased `us-east-1` provider is only for global CloudFront APIs.

CloudFront can GET only `deadlines.ics`, `actions.ics`, `combined.ics` through OAC
with source distribution/project conditions. It cannot list the bucket, read object
versions or retrieve `_private/snapshot.json`. The publisher can only PUT these
four exact SSE-S3 keys, not list/read/delete them. Existing backup grants/retention
are unchanged. Bucket/distribution destruction is guarded; the feed bucket retains
noncurrent derived versions for 30 days, with no expiry of current feed objects.

## Local preparation, no AWS mutation

```sh
terraform -chdir=infra/aws/integrations init -backend=false -input=false
terraform -chdir=infra/aws/integrations fmt -check
terraform -chdir=infra/aws/integrations validate
terraform -chdir=infra/aws/integrations test
PYTHONPATH=src python -m unittest discover -s tests -p test_calendar_transport.py -v
python -m unittest discover -s infra/aws/tests -p 'test_agent_*.py' -v
```

Only mocked Terraform tests run here; they are not an actual AWS plan or native
CloudFront execution test. Actual project permissions, compiled CloudFront function,
ARM64 runtime/RSS and account-specific OpenAI/Proton UI require approved qualification.

Build two code-only artifacts from the same full committed revision, outside Git:

```sh
python infra/aws/scripts/build_release.py --repo . --commit FULL_COMMIT --output /PRIVATE/app.tgz
python infra/aws/scripts/build_agent_release.py --repo . --commit FULL_COMMIT --output /PRIVATE/agent.tgz
```

The first uses the existing guarded application installer, including updated backup
role metadata. The second contains a hash-locked ARM64/Python 3.12 model-free
wheelhouse, inert units and official pinned tunnel-client **runtime** 0.0.18
(source revision in `requirements/tunnel-runtime.json`), with release ZIP and
executable SHA-256 checks. No torch/model, data, credentials or private URLs.
`install_agent_runtime.py` verifies the artifact and refuses active/enabled integration
units or partial/differing releases. Root-owned code is read/execute-only to service
users. It installs inert units and reloads systemd; it never creates identities,
changes SQL/authentication or starts/enables anything.

## Approved execution order — not executed here

1. Obtain the [single sharing/deployment approval](../../../docs/AGENT_CALENDAR_APPROVAL.md).
   Inspect current host and receipts. Take/verify the normal pre-maintenance backup.
   Pause only the existing units required by the guarded app installer between runs;
   restore collection, backup and health schedules exactly afterward. No schema
   migrations or collector/source changes are involved in this integration.
2. Install the reviewed code artifacts with recorded full commit/digests using the
   existing private artifact/SSM procedure. Run native ARM64 import/help and peak-RSS
   checks without inference. Memory limits: read/owner MCP 192 MiB each, tunnel
   128 MiB each, calendar 192 MiB while running. These are limits, not measurements:
   verify combined memory/CPU headroom before enabling on the 2 GiB host. Stop if
   the existing database/collector is affected. No automatic instance resize.
3. Create the five distinct system OS users/groups `jobwatch-agent-read`,
   `jobwatch-agent-owner`, `jobwatch-tunnel-read`, `jobwatch-tunnel-owner`,
   `jobwatch-calendar`, with no password/login shell/home. Only MCP/calendar
   identities join `jobwatch-db` for the existing database socket. Each tunnel
   joins **only its matching** MCP group, never `jobwatch-db`/owner/collector.
   The read/write runtime directories are owned by their MCP identity, mode 0750;
   MCP sockets 0660. Tunnel health sockets have private 0700 runtime directories.
4. Once, run `sql/postgresql/agent_roles.sql`, then `agent_access.sql` as the
   existing administrator/grant authority in `jobwatch`. New roles have NULL
   passwords, no role memberships and finite limits. Existing names fail atomically;
   do not overwrite/adopt unexpectedly existing roles. No old grants/triggers change.
   Back up HBA/ident bytes privately; add only the three exact Unix peer entries in
   `peer-authentication.example`. Validate `pg_hba_file_rules` and reload, not restart.
   Test the matrix in the tool contract and deny all mutation paths from read access.
5. Run a fresh synthetic backup/independent restore with all optional roles first.
   New format-2 backups carry explicit role/security prerequisites; old archives
   and the pinned format-1 recovery bundle remain valid. On a genuinely fresh target,
   `prepare_recovery_roles` can reconstruct the named restricted roles/settings
   before `restore`; it does not silently repair an existing operational database.
   Preserve recovery access and confirm the next normal receipt. No real owner test
   records need be written until the owner authorizes a particular action.
6. Prepare the new Terraform state **without** touching old resources. Use the same
   protected state bucket and new `integrations/terraform.tfstate` key. The existing
   policy protects only its original key; add the reviewed new-key deletion deny
   using `render_integration_state_policy.py` and preserve every original statement.
   Read back/compare the policy before backend initialization. Keep native `.tflock`
   create/read/delete permissions; never deny deletion of an active lock object.
   `state-access-policy.json.template` is the restricted operator addition, not a
   policy to attach to runtime users. No force-unlock or state deletion.
7. Create owner-private capability custody (0700 directory, 0600 file) outside Git
   with `calendar_capabilities.py --help`. Default separate deadline/action tokens;
   combined is optional. Export **hashes only** to private Terraform variables.
   Supply private project/bucket/role/SNS inputs, initialize the protected S3 backend,
   save and review a plan: expected 14 additions, no changes/deletions/replacements.
   If not, stop. Apply only under the approved reviewed scope; never apply the old
   infrastructure root to deploy these additions. Keep plans/state/URLs private.
8. Test synthetic feeds first: actual CloudFront function execution and HTTPS fetch,
   no/query/wrong/revoked tokens, wrong feed/method/path and private-manifest denial.
   Verify S3 protections, independent state protection and publisher IAM. Bind the
   CloudFront hostname into the private URL custody file. Do not print URLs in
   SSM output, CI, chat, Git, screenshots or logs. No CloudFront URI access logs:
   those would record bearer tokens. Use aggregate metrics and control-plane audit.
9. Install root-owned `/etc/jobwatch/calendar.json` and SQL deadline policy, mode 0640
   readable only by the calendar identity's group. Existing `/etc/jobwatch/aws.json`
   must be readable for redacted operational logging; no credential copying.
   Set the intended SQL reader identity in the supplied unit. Run synthetic/native
   qualification. The first approved publication must explicitly pass `--initialize`
   as the calendar identity; no scheduled unit passes it. Missing transport cache
   after host loss must be recovered from private S3 metadata, never initialized
   over an existing feed. Then enable the approved calendar timer/path and projection.
   Hourly at minute 10 UTC (up to 30 s jitter), plus committed-owner refresh hints;
   ordinary psql owner writes appear through the hourly fallback.
10. Create/select exactly the two intended personal Platform tunnels/connections,
    using the current account UI and its permissions/billing information. Each
    runtime key: **Tunnels Read + Use only**. Setup creator: Read + Manage only as
    needed. No model API/admin/AWS/database credentials in the tunnel service.
    Enter keys privately into separate root-only `tunnel-read.env`/`tunnel-owner.env`
    files, never arguments or YAML. YAML uses `env:CONTROL_PLANE_API_KEY` only;
    make each root-owned profile 0640 with its corresponding tunnel OS group so
    the process can read it. The systemd manager reads the 0600 environment file.
    Separate keys permit revocation but are **not** per-tunnel database ACLs:
    actual isolation also relies on personal workspace association and Unix/SQL
    identity. Inspect available Platform scope behavior rather than assuming it.
11. Follow the precise ChatGPT/Proton UI steps below. Confirm write UI rejection
    and approval on synthetic data before enabling real owner operations. Research
    real records only after sharing approval. Execute only a specifically authorized
    real owner action; check audit, calendar appearance/reschedule/completion and
    eventual Proton refresh. Do not claim success from curl or SDK tests alone.

## Personal UI steps and secrets

ChatGPT: Settings → Apps/Connectors → Advanced/developer mode if required by the
actual account → Add custom MCP server. Create **Graduate Job Watch Research** and
**Graduate Job Watch Owner Actions** separately. In Connection select the matching
private Secure MCP Tunnel; use its platform-provided connection value, not a public
EC2 URL. No MCP OAuth is configured: “No authentication” is safe only behind this
authenticated private tunnel plus Unix permissions. Read description: “Private
read-only SQL research; no notes/CV or audit images.” Owner description: “Confirm
each exact owner change; never allow blanket writes.” Enable only in the intended
personal context. Current UI labels/plan entitlement must be checked in that account.
Never share read or write access with another workspace implicitly.

Proton: Settings → All settings → Calendars → Other calendars → Add calendar from
URL (or the plus by My calendars). Open the private custody file locally and paste
the **actions** URL, then **deadlines** URL as separate subscribed calendars if the
plan permits. Configure calendar-wide notifications separately; combined is optional.
Subscription is read-only and normally refreshes every **4–16 hours**. It does not
provide immediate event-specific alerts. Pending/uncertain dates are not invented.
Actual subscription and eventual observed changes must be confirmed by the owner.

## Revocation, failure and recovery

- Stop the matching tunnel/MCP services; disconnect the personal app and revoke its
  Platform runtime key. Check Platform associations and confirmation instead of
  claiming key revocation from merely stopping a local client. No SQL write rollback
  occurs on disconnect; use a separately authorized audited correction if necessary.
- Revoke one calendar by changing only its capability hash and waiting for global
  CloudFront deployment, then confirm the old URL is rejected even with a warm cache.
  Viewer authorization precedes cache lookup. Update the Proton URL privately or
  remove that subscription. Previously fetched calendar copies cannot be revoked.
- Anyone possessing a capability URL can read its minimal feed. No OAuth/UI login is
  possible for a normal Proton URL subscription; keep URLs in the owner's password
  manager. Raw tokens are not in Terraform state, JS, metrics or ICS.
- A failed SQL projection preserves last-good snapshots; upload failure preserves
  prior complete metadata/versions. Separate feeds can briefly differ during upload;
  use combined when a single-object view matters. Private completion metadata is
  uploaded last and retains exact feed versions/hashes and UID/revision cache.
- Redacted calendar operation health is emitted hourly to the existing operational
  log. A new alarm treats missing heartbeat as failure after two hourly windows;
  existing operation-failure alarm handles nonzero status. Test delivered email and
  recovery under approval. An alarm is not proof that Proton fetched a feed.
- To recover transport metadata, an authorized operator downloads the exact private
  completion version and referenced ICS versions, verifies hashes and runs
  `restore_transport_state` into a new private directory. This preserves UID/SEQUENCE
  continuity; it does not reconstruct owner facts from a calendar. PostgreSQL remains
  the authority. Leave last-good feeds available during recovery.

Official references: [Secure MCP Tunnels](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels),
[pinned client configuration](https://raw.githubusercontent.com/openai/tunnel-client/274a5588414819f063d27100a4fb1096aae3830f/docs/configuration.md),
[ChatGPT write confirmation](https://developers.openai.com/api/docs/guides/custom-mcp-server),
[Proton subscription behavior](https://proton.me/support/subscribe-to-external-calendar),
[CloudFront private S3 origins](https://docs.aws.amazon.com/AmazonCloudFront/latest/DeveloperGuide/private-content-restricting-access-to-s3.html).
