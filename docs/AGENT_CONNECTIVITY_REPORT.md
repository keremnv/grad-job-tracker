# Stage 4F — local qualification results

25 October 2026. Branch `feat/agent-connectivity-qualification`, based on accepted
Stage 4E `5d5c8f88bce1789ff78638765eeb893e1c723240`.

## Implemented

- Reused Stage 4C's five tools, core, private role and SQL contracts. Added the
  optional `jobwatch-read-probe` official-SDK client, not a new server/API/query
  abstraction. Its receipts contain checks, timings, sizes/hashes and aggregate
  counts; no source/owner text, keys, SQL, URLs or credentials.
- Added a hash-locked, model-free optional MCP runtime and inert Codex config
  example. New isolated Python environments installed the lock successfully;
  the lexical runtime has neither torch nor transformers. No personal Codex
  settings, external plugins or tunnel clients were configured.
- Scoped the MCP executable's default to `--query-inference disabled`. SQL,
  BM25/FTS and embedding coverage remain usable; vector/hybrid fails explicitly
  with `query_inference_disabled`. `local` opts into the unchanged pinned float32
  EmbeddingGemma encoder. Direct SQL/Python/JSON behaviour is preserved.
- Tested composed synthetic research over stdio and loopback Streamable HTTP:
  schema, bound multi-table corpus SQL, exclusions/history, native bytea integrity,
  owner/audit reads, reports and fixed errors. Actual writable-CTE/definer mutation
  denials run only inside the verified disposable fixture; the reusable probe
  itself reads privilege metadata and never sends a mutating statement.
- Restricted the probe's HTTP target to an explicit loopback `/mcp`; it ignores
  ambient proxies and rejects redirects. Stdio child environment excludes owner,
  migrator, embedding-writer, AWS and OpenAI credentials. This is not an OS sandbox.
- Corrected the read-provisioning SQL's prerequisite **comment only**: it already
  references the 0004 embedding table. Grants, roles and migrations are unchanged.

[Bounded plan](AGENT_CONNECTIVITY_PLAN.md) and [deployment/access details](AGENT_CONNECTIVITY.md)
record the proposed arrangement, custody/revocation, monitoring and next approvals.

## Executed tests

| Check | Actual result |
|---|---|
| Offline suite, Python 3.11.16 / 3.12.14 / 3.13.12 | **237 passed on each**, no skips (232 existing + 5 connectivity tests), using the pinned model-free MCP runtime |
| Complete PostgreSQL suite in model-free runtime | **141 run: 139 passed, 2 optional real-model tests skipped**, PostgreSQL 17.11 / pgvector 0.8.7 / psycopg 3.2.13 |
| Complete PostgreSQL suite with cached local inference | **141 passed**, no skips, Python 3.12.14 / psycopg 3.2.13 / libpq 170006; includes original real-model smoke and new real vector/hybrid MCP session |
| Runtime packaging | Hash-checked installation on Python 3.11–3.13; `uv pip check` clean. Combined read/CPU-embedding locks resolve in Linux/Python 3.12 dry-run using `--torch-backend cpu` |
| Static checks | Ruff F, compileall, inert TOML parsing, relative documentation links and `git diff --check` passed |

The database launcher verified its own labelled, loopback-only Docker target,
removed ambient connection URLs, and cleaned it up. Full suites include existing
import/reconciliation/repeatability, real pg_dump/pg_restore, independent isolated
recovery, owner/audit/calendar, concurrency and role tests. No real tracker data,
private backup or hosted PostgreSQL connection was used.

Principal commands (local tools/cache, no secret input):

```sh
uv pip compile requirements/agent-read.in --generate-hashes --universal \
  --python-version 3.11 -o requirements/agent-read.lock
PYTHONPATH=src /home/kerem/.cache/jobwatch-connectivity-stage/py311/bin/python -m unittest discover -s tests -v
PYTHONPATH=src /home/kerem/.cache/jobwatch-connectivity-stage/venv/bin/python -m unittest discover -s tests -v
PYTHONPATH=src /home/kerem/.cache/jobwatch-connectivity-stage/py313/bin/python -m unittest discover -s tests -v
PYTHON=/home/kerem/.cache/jobwatch-connectivity-stage/venv/bin/python scripts/test_postgres.sh
HF_HOME=/home/kerem/.cache/jobwatch-retrieval-stage/huggingface HF_HUB_OFFLINE=1 \
  JOBWATCH_REAL_MODEL_TEST=1 PYTHON=/home/kerem/.cache/jobwatch-retrieval-stage/venv/bin/python scripts/test_postgres.sh
uv pip install --python /home/kerem/.cache/jobwatch-connectivity-stage/venv/bin/python \
  --dry-run --require-hashes --torch-backend cpu \
  -r requirements/agent-read.lock -r requirements/embeddings-cpu.lock
ruff check --select F src/jobwatch/postgres/read_mcp.py src/jobwatch/postgres/connection_probe.py \
  tests/test_agent_connectivity.py tests/postgres/test_read_tools.py
```

The redirect test caught an SDK detail: same-origin redirects are followed even
when the supplied HTTP client sets `follow_redirects=False`. A response hook now
enforces this probe's stricter no-redirect scope; the live loopback test passes.
An initial combined-lock dry-run omitted the required CPU index flag and could
not resolve the existing torch CPU wheel; the documented command above resolves.
No outstanding local test failure remains. No Terraform or hosted tests were run.

## Verified documentation; not verified account/deployment

Official [custom MCP guidance](https://developers.openai.com/api/docs/guides/custom-mcp-server)
describes Plugins creation, URL/Tunnel connections and workspace restrictions.
[Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
documents outbound private access and separate Platform/context permissions.
Neither establishes this owner's actual personal Plus entitlement or tunnel price.
The UI question remains unconfirmed; no browser account, key, tunnel or connection
was accessed/created. An SDK client is not an online ChatGPT test.

Recommended next approval: a **synthetic local database → private tunnel → actual
ChatGPT** trial, after the owner verifies UI/Platform access and any charges.
That requires OpenAI tunnel/key/connection approval but no AWS change. Do not
require a hosted upgrade merely to prove the online transport.

For later AWS access, the last committed host qualification has release `1b72d0d`
and migrations 0001–0003; it was not re-inspected here. The new read role needs its
own approved auth/HBA and current-schema provisioning. Preserve owner credentials
separately. Do not place the 1.84 GiB query process on the 2 GiB database host.
Private notes/audit exposure must be consciously approved before real sharing;
read-only access does not make those records public or erase client-retained data
when later revoked.

Proton HTTPS hosting is independent and unimplemented: approve endpoint/domain,
TLS/ingress, revocable minimal feed URLs and schedule/monitoring separately. Reuse
Stage 4E snapshots; no MCP-to-Proton bridge or duplicate event engine is proposed.
The existing £10–£20 monthly preference is retained; no recurring cloud cost was
introduced, and tunnel/HTTPS charges are not assumed zero.

AWS resources/IAM/networking, credentials, timers, GitHub workflows, production
files, migrations and `main` are unchanged. Remote main was read as observation
commit `9bbcb0149d41250cc2e56b2cbb25779bd5fafc16`; it and newer observations remain
separate for future integration. No live ATS, production import, hosting or cutover
occurred. Git remains authoritative. Remaining source-validation, recovery/custody,
owner acceptance, final freeze/import/restore and single-writer gates remain open.
