# Agent/calendar integration qualification — 27 October 2026

> **Historical qualification, before deployment:** The “not deployed” statement below describes this qualification stage, not the current system. The subsequent [27 October integration release](INTEGRATION_RELEASE.md) documents deployment and operational checks. This report is retained unchanged as evidence of the predeployment tests.

Implemented on `feat/agent-calendar-integrations`, based on production main
`9d24af422858c714964d0951ddd6ba5074191ac6`. Code/artifact revision:
`41f640ba463c6bfb58eef9d63de34b27163dafd2`. Subsequent documentation commits
do not change the qualified code. **Not deployed; no real-data sharing activated.**

## Delivered

- Existing five read tools gain a narrower, optional production identity. SQL remains
  composable; all verdicts/history/evidence are retained. Notes/CV/raw lead details
  and audit images are denied; permitted field matrix is in the approval packet.
- Two owner tools execute the existing seven canonical SQL recipes, with prepared
  reviews, expected state, transactions, authenticated audit attribution, read-back
  and replay handling. No arbitrary SQL writes or collector/admin authority.
  Natural requests resolve identities through ordinary research, not a shortlist/UI.
- Separate permissioned read/write Unix HTTP sockets and OS/tunnel identities.
  Official MCP SDK exercises both research and synthetic confirmed writes. ChatGPT
  human-confirmation behavior still requires an actual account test before real writes.
- Existing ICS generator/publisher reused. SQL selects deadline evidence keys;
  adequate-date checks remain unchanged. No mandatory shortlist. Hourly UTC fallback
  plus post-commit hints; separate deadline/action feeds and optional combined feed.
- Fixed-key encrypted S3 publication, SHA-256/version receipts, private completion
  metadata uploaded last, unchanged-ICS suppression, last-good preservation and
  explicit first initialization. Lost UID/revision cache requires recovery, not reset.
- Inert AWS-only HTTPS/tunnel/systemd packaging, independently revocable capabilities,
  restricted new roles, additive protected Terraform state key and missing-heartbeat
  alarm. No existing bootstrap/user-data, migration, backup retention or live schedule
  change. An actual AWS plan/apply and CloudFront service execution remain pending.
- New optional-role metadata travels in format-2 backups. Fresh-target role/security
  prerequisites and the actual independent restore runner are tested; old archive
  formats retain their documented pinned recovery path.

## Final tests actually executed

Linux x86_64; Python **3.12.14**, psycopg **3.2.13**, libpq **17.0.6**, MCP SDK
**1.30.0**. Disposable PostgreSQL **17.11**, pgvector **0.8.7**. Terraform **1.17.0**.
No AWS/Platform credentials or production database URL were used for these tests.

| Check | Actual result |
| --- | --- |
| `PYTHONPATH=src python -m unittest discover -s tests -v` | **264 passed** |
| `HF_HUB_OFFLINE=1 JOBWATCH_REAL_MODEL_TEST=1 HF_HOME=LOCAL_MODEL_CACHE PYTHON=QUALIFIED_PYTHON bash scripts/test_postgres.sh` | **158 passed**, no skips; 132.8 seconds |
| `python -m unittest discover -s infra/aws/tests -p 'test_agent_*.py' -v` | **2 passed**, including ordinary/restrictive umasks and real Linux UID/group/NoNewPrivileges socket isolation |
| `python -m unittest discover -s infra/aws/tests -p test_installer_permissions.py -v` | **6 passed** |
| Same runner, `test_protections.py` / `test_offhost_guards.py` | **2 / 3 passed** |
| `terraform -chdir=infra/aws/integrations init -backend=false -input=false`, `fmt -check`, `validate`, `test` | Passed; **1 mocked plan test**; proposed inventory **14 additions** |
| Existing `infra/aws/` init without backend, fmt/validate/test | Passed; **5 mocked tests**, existing EBS/bucket protections retained |
| Python compileall, `git diff --check`, Markdown lint and relative documentation links | Passed |
| `systemd-analyze --root TEMP_ROOT --man=no verify ...` | Passed for all new units/two tunnel instances with isolated dependency/executable fixtures; **not host execution** |

Tests cover multi-table research, exclusion access, notes/CV/audit-image denials,
ordinary SQL mutations/functions denied by the existing read boundary, bound source
wording, stale/tampered/expired review refusal, wrong-role refusal, atomic fault
rollback, confirmed replay, manual-key linking/stages, lead evidence/linking,
private-note preservation, audit identity and refresh-hint failure after COMMIT.
Calendar tests include explicit/uncertain deadlines, programme years, no-shortlist
review actions, reschedule/clear/removal, stable revisions, privacy/size bounds,
capability routing/revocation (actual JS under Node), cache-loss refusal and transport
metadata recovery. Actual PostgreSQL dump/restore includes optional roles, views,
grants, security fingerprint, exact rows and sequences in a separate network-disabled
container; it never connects to AWS.

The Unix HTTP test initially found a Host-header mismatch. Profiles now retain the
SDK's expected loopback HTTP authority while all traffic uses the Unix socket;
DNS-rebinding protection was retained. Native ARM64 inference is deliberately absent.
Local model-free MCP initialization took 0.65 seconds, max RSS 56,196 KiB for a
single process initializing both server objects. **This is not an ARM64/load or
two-process hosted measurement.** Native headroom is an execution gate.

## Code-only artifact proof

Both builders completed from the exact code revision above. The application archive
contains 114 manifest members; the separate ARM64/Python 3.12 agent archive has 43.
Independent archive/member SHA-256 and inventory verification passed. Both contain
all four unchanged migrations and the wheel-installed canonical owner SQL cookbook.
Agent dependencies are hash-locked, model-free wheels; no torch/transformers/tokenizers/
safetensors. Official ARM64 tunnel runtime ZIP/executable hashes and ELF architecture
were verified against `requirements/tunnel-runtime.json`; local AMD64 runtime reports
0.0.18. **The ARM64 executable has not been run on the production host.**
Artifacts, detailed logs and digest receipts remain outside Git in an owner-private
local qualification directory. No data/config/reports/Git metadata/private credentials
enter either artifact. Native installation and actual account permissions are not
implied by successful cross-platform packaging.

## Awaiting one owner decision

The [approval packet](AGENT_CALENDAR_APPROVAL.md) contains the exact data-sharing
matrix, 14-resource inventory, additive IAM/state permissions, estimated marginal
cost, key/URL custody and revocation, and personal account actions. The
[runbook](../infra/aws/integrations/README.md) includes guarded installation,
fresh-target recovery, precise UI instructions and failure conditions.

Pending actual tests: native ARM64 memory/SSM/peer and unit qualification; AWS plan,
CloudFront deployment/compiler/HTTPS/revocation and email delivery; current Platform
price/permission review; synthetic ChatGPT per-write confirmation; real ChatGPT tool
calls; real authorized owner action; Proton subscription and eventual refresh.
Proton's documented 4–16-hour polling is not guaranteed deadline alerting.

Production main/tag, archived observations/owner ledgers, active PostgreSQL collector,
twice-daily backup/health schedules, AWS resources and credentials are unchanged.
No tunnel was started, no real calendar was generated/published, no production
optional login was created, and no new production record was read or written.
