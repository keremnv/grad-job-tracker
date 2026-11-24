# Stage 1 Production Rollout Report & Isolated Stage 2/3 Readiness

**Date:** 24 November 2026 (UTC)  
**Target host:** `i-0a21a9da641e3f22e` (`t4g.small`, ARM64, `eu-north-1`)  
**Production release commit:** `16c9b0cbe7f5cb9ceaf0e9109205be009c1d0cc3` (deployed via guarded `fetch_release.sh` + `install_agent_runtime.py`)  
**Recurring Workable-wide discovery:** **Disabled** (no timer or schedule created)

---

## 1. Deployment evidence

| Step | Evidence | Outcome |
| --- | --- | --- |
| **GitHub push** | `origin/main` updated through `16c9b0cbe7f5cb9ceaf0e9109205be009c1d0cc3` | Passed |
| **S3 artifacts uploaded (`AES256`)** | `artifacts/app-16c9b0cbe7f5cb9ceaf0e9109205be009c1d0cc3.tar.gz` (`sha256: 827b1f8f…b8af54ba`, 5.0 MiB); `artifacts/agent-16c9b0cbe7f5cb9ceaf0e9109205be009c1d0cc3.tar.gz` (`sha256: 54eb4f7f…20da0d324`, 20.9 MiB, model-free) | Passed |
| **Pre-maintenance S3 backup** | `backups/2026/11/24/8ffabc604e5a4015a458df5247802064.dump` (`16,906,241` B / `16.12 MiB`) + `.json` (`7,291` B), `11 s`, `22:35:41Z` | Passed |
| **Guarded app & agent install** | Writers and integration units stopped/disabled; `/opt/jobwatch/current`, `/opt/jobwatch-agent-runtime`, `/opt/jobwatch-tunnel` atomically switched to `16c9b0cbe7f5cb9ceaf0e9109205be009c1d0cc3` | Passed |
| **Migration `0008`** | `migrate_local.sh` as `jobwatch-migrator` applied `0008_workable_and_discovery.sql` at `22:37:02.957714Z`; repeat invocation confirmed no-op (`Database schema is up to date`) | Passed |
| **Read-only discovery grants** | `sql/postgresql/discovery_access.sql` granted `SELECT` on `discovery_candidates` and `research_discovery_candidates` to `jobwatch_agent_readonly`, `jobwatch_agent_owner`, `jobwatch_calendar_reader`; `DELETE` refused over Research MCP | Passed |
| **14 board validations & activations** | All 14 boards passed live `check_endpoint(..., fixture=False)` (`status='validated'`, `absence_safe=true`, 781 vacancies observed) and transitioned via `activate_source` (`actor='owner:stage1-rollout-20261124'`) | **14 / 14 Passed** |
| **Controlled 23-board collection** | `jobwatch-collect.service` (`22:39:37Z`–`22:40:16Z`): `38 s` wall (`17.954 s` CPU), `23/23 published`, `22 absence_safe`, `1 positive_only` (Palantir Lever >100), `exit_code=0` | **23 / 23 Passed** |
| **Post-rollout S3 backup** | `backups/2026/11/24/e6796b9d77054d3793ca91b77eba0f12.dump` (`20,909,205` B / `19.94 MiB`) + `.json` (`7,293` B), `13 s`, `22:42:02Z` | Passed |
| **Services, MCP health & alarms** | All 11 systemd units `active`/`enabled`; `jobwatch-mcp-health` `{"mcp_healthy": 1, "probe_ok": 1, "restarts": 0, "restarts_delta": 0}`; Owner MCP `/run/jobwatch-agent-owner/mcp.sock` verified read-only (`prepare_owner_change`, `commit_owner_change`); all 6 CloudWatch alarms `OK` | Passed |

---

## 2. Activated boards (14 new → 23 monitored total)

| Source ID | Wave | Employer | Provider & Board | Region | Validation Run ID | Vacancies | Absence-safe |
| ---: | --- | --- | --- | --- | --- | ---: | --- |
| 19 | 1A | Graphcore | `greenhouse:graphcore` | `global` | `25e6e4c1-dee3-4bc3-9803-4e0d9c8b4e50` | 185 | `true` |
| 20 | 1A | Canonical | `greenhouse:canonical` | `global` | `c098acf5-7857-460e-b0e6-a28807310acd` | 316 | `true` |
| 21 | 1A | Faculty | `ashby:faculty` | `global` | `23b897bd-b4df-4a73-bd31-4a368c7c4002` | 91 | `true` |
| 22 | 1A | Thought Machine | `ashby:thought-machine` | `global` | `c860f28d-668c-4174-b818-2befcb2fbe30` | 34 | `true` |
| 23 | 1A | Snyk | `ashby:snyk` | `global` | `e51e1235-de5c-426e-b4bf-4bc38125c28f` | 37 | `true` |
| 24 | 1A | Autotrader | `greenhouse:autotrader` | `eu` | `79413f8e-3520-4b9e-86e0-9f71bc0d5422` | 12 | `true` |
| 25 | 1B | Stalis | `workable:stalis` | `global` | `d22adb6d-cd4e-4362-a63e-f6eb75a839e0` | 2 | `true` |
| 26 | 1B | LMAX Group | `workable:lmax` | `global` | `44868e2e-0fb5-4528-bef5-5a2bf5648b5e` | 8 | `true` |
| 27 | 1B | DC Thomson | `workable:dcthomson` | `global` | `b32bdf54-0229-4d59-b1e3-f1ccbddda43f` | 3 | `true` |
| 28 | 1B | Grayce | `workable:grayce` | `global` | `f6302a18-14cb-4d5a-8357-db1e9288563a` | 14 | `true` |
| 29 | 1B | TidalSense | `workable:tidalsense` | `global` | `03fbc179-b426-4750-8891-4256919ca09f` | 2 | `true` |
| 30 | 1B | iwoca | `ashby:iwoca.co.uk` | `global` | `1a556c23-e562-433b-a1f4-5e9dd6777378` | 50 | `true` |
| 31 | 1B | Edra | `ashby:edra` | `global` | `796b7418-61b9-437c-b11c-d6f5d92b3323` | 23 | `true` |
| 32 | 1B | Ably | `greenhouse:ably30` | `eu` | `52859dd6-d504-4b6a-a23b-81f1fdcd4b44` | 4 | `true` |

---

## 3. Measured vs projected resource consumption

| Metric | Pre-rollout (9 boards) | Projected (23 boards) | Measured (23 boards) | Assessment |
| --- | ---: | ---: | ---: | --- |
| Monitored boards | `9` | `23` | **`23`** | Exact match |
| Registered employers | `44` | `52` | **`52`** | Exact match |
| Listed postings (`total`) | `638` (`778`) | `~1,400` | **`1,416`** (`1,560`) | Exact match |
| Listed `priority` / `review` | `6` / `47` | — | **`21`** (`+15`) / **`179`** (`+132`) | High relevant yield |
| `posting_versions` rows | `2,107` | — | **`2,892`** (`+785`) | Clean |
| PostgreSQL DB size | `67.8 MiB` | `~155 MiB` (1-mo) | **`83.6 MiB`** (`+15.8 MiB`) | Well under projection |
| S3 encrypted backup size | `16.12 MiB` | `~37 MiB` (1-mo) | **`19.94 MiB`** (`+3.82 MiB`, `13 s`) | Well under projection |
| Full collection wall time | `22.4 s` | `~38 s` | **`38 s`** (`17.95 s` CPU) | Exact match |
| Research MCP BM25 peak RSS | `~82 MiB` | `~129 MiB` | **`120.5 MiB`** (`126.3 MB`; `63.6 MiB` post-trim; `NRestarts=0`) | Safe inside `MemoryMax=256M` |
| Host available RAM / root disk | `~1.1 GiB` / `42%` | — | **`1,118 MiB`** free (`0` swap) / **`42%`** (`6.3 GiB` free) | Healthy |

---

## 4. Live operational limitations surfaced during Research MCP verification

Direct probes against `/run/jobwatch-agent-read/mcp.sock` confirmed that Stalis (`workable:stalis:1C9CCEA6B4`, including multi-location UK + Ireland Workable evidence) and all 10 Graphcore 2027 Graduate `priority` vacancies are persisted and queryable via `sql_query`, `search(method='fts')` (`59.2–64.7 ms`), and `inspect_evidence`. However, two latency regressions appeared at `1,416` listed postings under `ReadTools`'s 5-second timeout:

1. **Python full-corpus BM25 (`search(method='bm25')`) timed out at `6,951–7,477 ms`** (`error.code = 'timeout'`, exceeding `timeout_ms=5000`) on `t4g.small` ARM64, while `search(method='fts')` completed in `59.2 ms` (Stalis) and `64.7 ms` (Graphcore).
2. **`run_report('daily')` and `run_report('requirements')` hit PostgreSQL `statement_timeout = 5000 ms` (`sqlstate 57014`)**. Read-only `EXPLAIN (ANALYZE, BUFFERS)` on `jobwatch.operational_postings` traced this directly to the unindexed correlated `EXISTS(... v2.url_exact = v.url_exact)` self-join (`SubPlan 1`: `25,650` buffer hits / `1.644 ms` per row across `1,560` rows in `research.deadlines`).

---

## 5. Isolated local Stage 2/3 development (NOT deployed to production)

Developed and verified locally against offline unit tests and disposable PostgreSQL 17 (`scripts/test_postgres.sh`), **not applied to production**:

- **Custom Greenhouse host support (`src/jobwatch/feeds.py`, `tests/test_feeds.py`, `tests/postgres/test_riverlane.py`):** Replaced Riverlane's one-off branch with an explicit `(board, region)` allowlist (`CUSTOM_GREENHOUSE_URLS`) supporting `riverlane` (`eu`), `stripe` (`global`), `janestreet` (`global`, enforcing path-ID = query-ID via regex backreference), and `ocadogroup` (`eu`, enforcing exact backreference match on duplicated `gh_jid` parameters). Standard hosted Greenhouse URLs remain accepted alongside custom hosts, and all mismatches/spoofing/wrong regions fail closed.
- **Scalable PostgreSQL-native retrieval & operational indexes (`src/jobwatch/postgres/migrations/0009_scalable_retrieval_and_indexes.sql`, `src/jobwatch/search.py`, `research.py`, `read_tools.py`, `read_mcp.py`):**
  - Adds pure `IMMUTABLE` `jobwatch.symbol_search_text(text)` (matched 1:1 in Python) and stored generated column `posting_versions.search_sym_tsv` + GIN index `versions_search_sym`, keeping `C`, `C++`, `C#`, `F#`, and `.NET` distinct at sub-100ms latency and flat RAM.
  - Exposes explicit `method='fts_symbol'` and two-stage `method='bm25_prefiltered'` (PostgreSQL symbol-preserving GIN top-500 prefilter → exact Python BM25 rerank) without altering existing `bm25`/`fts` contracts.
  - Adds `versions_url_exact` on `posting_versions(url_exact)` and `postings_current_version_idx` on `postings(current_version_id)` to eliminate the `O(N^2)` sequential scan behind the `run_report` timeout.
