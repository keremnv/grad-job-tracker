# Stage 4C implementation results

Implemented the [five-tool contract](CHATGPT_READ_TOOLS.md) on a separate branch
from the complete AWS/recovery baseline b3ba87f. No architecture/table/index,
collector/normalizer, migration, existing-role grant or audit-trigger change.

New components: read_tools.py (ordinary synchronous core and JSON-lines harness),
read_mcp.py (optional official MCP SDK adapter), separately reviewable
sql/postgresql/chat_readonly.sql, integration tests and optional dependency/entry
points. The existing research/search/reports code supplies domain semantics.

Executed commands:

```sh
PYTHON=/tmp/jobwatch-chat-read-env/bin/python scripts/test_postgres.sh
PYTHON=/tmp/jobwatch-chat-read-env/bin/python scripts/test_postgres.sh --rehearse-import "$PWD" 90434ea6d9a3457f9c8ba40f01ad25fa2ceffca3 "$PWD/../.local-reports/chat-read-interface-rehearsal"
PYTHONPATH=src /tmp/jobwatch-chat-read-env/bin/python -m unittest discover -s tests -v
PYTHONPATH=src /tmp/jobwatch-ops-py311/bin/python -m unittest discover -s tests -v
PYTHONPATH=src /tmp/jobwatch-ops-venv/bin/python -m unittest discover -s tests -v
uvx ruff check --select F src/jobwatch/postgres/read_tools.py src/jobwatch/postgres/read_mcp.py tests/postgres/test_read_tools.py
python3 -m compileall -q src/jobwatch/postgres/read_tools.py src/jobwatch/postgres/read_mcp.py tests/postgres/test_read_tools.py
git diff --check
```

Results: **116 PostgreSQL tests** (105 existing plus11 new), full frozen629-posting
import/repeat/real pg_dump/pg_restore rehearsal, and **211 offline tests** on each
supported Python baseline. Python3.11.16/3.12.14/3.13.12; PostgreSQL17.11,
psycopg3.2.13/libpq170006; MCP1.30.0. Tests use only the harness-created labelled
local PostgreSQL container; provider feeds and owner records are synthetic.

Verified actual complex parameterized SQL/CTEs/JSON and duplicate columns,
exclusions, schema/FKs, historical/native NUL evidence/base64/provenance,
private applications and audit reads, BM25/FTS, all four report transformations,
pagination/byte limits, malformed input, sanitized errors, wrong-login refusal,
connection limit and timeouts including attempted GUC bypass. Actual SQL/mutation
refusals include DML, writable CTEs, DDL/TEMP, TRUNCATE, role/grant/admin commands,
file reads, sequences, large-object creation, session/trigger bypass, multi-command
injection and a deliberately executable SECURITY DEFINER writer. No write occurred.
Existing ACL comparison accounts for PostgreSQL materializing implicit owner ACLs
when a new SELECT grantee is added; previous effective entries remain unchanged.

Official MCP ClientSession initialization, five annotated tools, calls and errors
passed over stdio and local Streamable HTTP. This proves local interoperability,
not an authenticated online ChatGPT connection. No personal test transcripts,
credentials or production data are committed. Private execution logs remain in
.local-reports/chat-read-interface/. First pass found only the test's implicit-vs-
explicit owner-ACL comparison issue; correcting that test preserved the grants.

PostgreSQL defaults can be overridden by raw credential holders, so the forced
read-only transaction and single-query interface are essential; credentials stay
server-side. Resource bounds do not make arbitrary SQL a complete resource sandbox.
The contract records these practical limits and owner-data sharing implications.

No AWS operation or deployment, role creation on the hosted DB, permanent secret,
live ATS call, timer/workflow change, merge to main or production cutover occurred.
Git-backed production remains authoritative. Online transport/authentication,
private-data approval, plan/workspace access and live role provisioning remain
explicit follow-on approvals.
