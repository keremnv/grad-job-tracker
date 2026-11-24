# Stage 2/3 Production Rollout Report — Retrieval Repair & Custom Greenhouse Expansion

**Date:** 24 November 2026 (UTC)  
**Host:** `i-0a21a9da641e3f22e` (`t4g.small`, ARM64, `eu-north-1`)  
**Deployed production commit:** `55fa7a275c8d2a7aba4682738dd6615dd2617c48` (pushed to `origin/main` prior to rollout)  
**Recurring production discovery jobs:** **None enabled** (Workable-wide discovery remains disabled; directory discovery migration `0010` is staged locally for review, not deployed)

---

## 1. Deployment evidence

| Step | Evidence | Outcome |
| --- | --- | --- |
| **Packaged ARM64 artifacts (`AES256`)** | `artifacts/app-55fa7a275c8d2a7aba4682738dd6615dd2617c48.tar.gz` (`sha256: 98817262…4e1cf9faa`, 5.0 MiB); `artifacts/agent-55fa7a275c8d2a7aba4682738dd6615dd2617c48.tar.gz` (`sha256: bdb292bf…ee75e4dc`, 20.9 MiB, model-free) | Passed |
| **Pre-maintenance backup** | Reused verified S3 snapshot `backups/2026/11/24/e6796b9d77054d3793ca91b77eba0f12.dump` (`19.94 MiB`, `22:42:02Z`, zero DB writes between snapshot and rollout) to preserve `jobwatch-backup.service` `StartLimitBurst=3/6h` budget ahead of the scheduled `00:00:00Z` automatic backup | Passed |
| **Guarded deploy & migration `0009`** | Maintenance window `23:38:36Z`–`23:39:08Z` (`32 s`): app + model-free agent runtime installed; `migrate_local.sh` applied `0009_scalable_retrieval_and_indexes.sql` at `23:39:01.703033Z` (repeat call no-op); all 11 units restarted cleanly (`NRestarts=0`) | Passed |
| **Wave 2 custom Greenhouse validations** | Live `check_endpoint(..., fixture=False)` passed `3/3` (`absence_safe=true`): `Stripe` (`source_id=33`, `770` jobs, `0.70 s`, run `4cb3330f-…`), `Jane Street` (`source_id=34`, `217` jobs, `0.84 s`, run `f6dd9408-…`), `Ocado Group` (`source_id=35`, `59` jobs, `0.18 s`, run `00a24c8a-…`) | **3 / 3 Passed** |
| **26-board production collection** | `jobwatch-collect.service` (`23:39:58Z`–`23:40:47Z`): `48 s` wall (`27.24 s` CPU), `26/26 published`, `25 absence_safe`, `1 positive_only` (Palantir Lever >100), `exit_code=0` | **26 / 26 Passed** |
| **Calendar & MCP health** | `jobwatch-calendar.service` `{"events": 12, "exit_code": 0, "feeds": 3, "fresh": true, "healthy": 1, "warnings": 0}` (`2.32 s` CPU); `jobwatch-mcp-health.service` `{"mcp_healthy": 1, "probe_ok": 1, "restarts": 0, "restarts_delta": 0}`; all 6 CloudWatch alarms `OK` | Passed |

---

## 2. Operational report repair (`run_report`)

Migration `0009` added `versions_url_exact` (`posting_versions(url_exact)`) and `postings_current_version_idx` (`postings(current_version_id)`), eliminating the `O(N^2)` sequential scan inside `jobwatch.operational_postings` (`SubPlan 1`):

- **`EXPLAIN (ANALYZE, BUFFERS)` on `operational_postings` (50-row slice):**
  - Before `0009` (23 boards / 1,560 postings): `259.2 ms`, `SubPlan 1 = 1.644 ms/row`, `25,650` shared buffer hits.
  - After `0009` (26 boards / 2,606 postings): **`10.6 ms`**, `SubPlan 1 = 0.013 ms/row` (**`126×` per-row speedup**), `592` shared buffer hits.
- **Live Research MCP `run_report` results (26 boards / 2,462 listed postings):**
  - `daily` (`limit=50`): **`ok: true`**, `1,568 ms`, `96,943 B` (surfaces Stalis, Graphcore, Stripe/Ocado)
  - `daily` (`limit=20`): **`ok: true`**, `1,492 ms`, `44,915 B`
  - `weekly` (`limit=20`): **`ok: true`**, `1,536 ms`, `50,999 B`
  - `requirements` (`limit=50`): **`ok: true`**, `1,501 ms`, `3,579 B`
  - `calendar` (`limit=50`): **`ok: true`**, `1,485 ms`, `9,652 B`

---

## 3. Retrieval verification & production measurements (2,462 listed / 3,938 versions)

Verified directly over `/run/jobwatch-agent-read/mcp.sock`, confirming the updated MCP `search` tool description explicitly documents all methods, limitations, and when each is appropriate, and clearly distinguishes `ranking_scope='candidate_pool_rerank'` (`bm25_prefiltered`) from `ranking_scope='full_corpus'` (`bm25`) and `ranking_scope='full_corpus_indexed'` (`fts_symbol`, `fts`).

| Method | Sample query | Measured latency | `ranking_scope` | Top match / Verification note |
| --- | --- | ---: | --- | --- |
| `fts_symbol` | `C++` | **`50.0 ms`** | `full_corpus_indexed` | `#1` Canonical *C++/Rust Mir*, `#2` Canonical *Cross-platform C++* (no plain-C false positives) |
| `fts_symbol` | `C` | **`75.0 ms`** | `full_corpus_indexed` | `#1` Canonical *C, Golang Software Engineer (dqlite)* (cleanly separated from `C++`) |
| `fts_symbol` | `C#` | **`31.5 ms`** | `full_corpus_indexed` | `#1-3` Jane Street *Windows Engineer* |
| `fts_symbol` | `.NET` | **`28.4 ms`** | `full_corpus_indexed` | `#1` Tracebit *Founding Engineer*, `#2-3` Jane Street *Windows Engineer* |
| `fts_symbol` | `Stalis Data Science` | **`31.4 ms`** | `full_corpus_indexed` | `#1` `workable:stalis:1C9CCEA6B4` (*Data Science & AI Graduate*) |
| `fts_symbol` | `Ocado Software Engineering Graduate 2027` | **`29.4 ms`** | `full_corpus_indexed` | `#1` `greenhouse:ocadogroup:5018802101` |
| `fts_symbol` | `Stripe New Grad London` | **`25.8 ms`** | `full_corpus_indexed` | `#1` `greenhouse:stripe:8130930` |
| `bm25_prefiltered` | `C++ graduate` | **`1,842 ms`** | `candidate_pool_rerank` (`353` rows) | `#1` Cambridge Consultants *Graduate SWE 2027*, `#2-3` Graphcore *Graduate SWE 2027* |
| `bm25_prefiltered` | `Stalis Data Science` | **`2,998 ms`** | `candidate_pool_rerank` (`500` rows) | `#1` `workable:stalis:1C9CCEA6B4` (`score=11.536`) |
| `bm25_prefiltered` | `Graphcore 2027 graduate` | **`1,988 ms`** | `candidate_pool_rerank` (`352` rows) | `#1` Graphcore *Graduate Cloud Platform Engineer 2027* |
| `fts` (baseline) | `C++` | `44.7 ms` | `full_corpus_indexed` | Returned *C, Golang dqlite* at `#3` (confirming English FTS collapses `C++` → `C`) |
| `bm25` (full-corpus) | `Stalis Data Science` | `11,253 ms` | `full_corpus` (preserved) | Returned `error.code='timeout'` (`>5s` ReadTools budget) at `2,462` listed postings |

### Production memory & storage footprint (`26` boards, `3,938` versions)

- **`jobwatch-agent-read.service` (`MemoryMax=256M`, `NRestarts=0`):**
  - Idle baseline: `44.4 MiB` (`46.5 MB`)
  - Peak across ALL 5 `run_report` calls + 7 `fts_symbol` + 4 `bm25_prefiltered` + 2 `fts`: **`74.6 MiB`** (`78.3 MB` — `3.4×` headroom!)
  - Peak after full-corpus `bm25`: `155.8 MiB` (`163.4 MB`), trimmed back to `68.9 MiB` (`72.3 MB`).
- **PostgreSQL storage (`jobwatch`, `/dev/nvme1n1` 16 GiB EBS at `364 MiB` / `3%` used):**
  - Total DB size: **`145.7 MiB`** (`152,770,227 B`, up from `83.6 MiB` at 23 boards — Stripe's 770 postings accounted for most of the `+62.1 MiB` delta)
  - `versions_search_sym` (symbol GIN): **`9.01 MiB`** (`9,445,376 B`)
  - `versions_search` (English GIN): `7.80 MiB` (`8,183,808 B`)
  - `versions_url_exact`: **`328 KiB`** (`335,872 B`)
  - `postings_current_version_idx`: **`208 KiB`** (`212,992 B`)
- **Host RAM:** `1,059 MiB` available / `1,834 MiB` total (`0` swap).

---

## 4. Wave 2 employer yield (`Stripe`, `Jane Street`, `Ocado Group`)

| Employer | Board | Listed total | `priority` | `review` | `excluded` | Key UK graduate / early-career highlights |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| **Ocado Group** | `greenhouse:ocadogroup` (`eu`) | `59` | `0` | **`7`** | `52` | *Software Engineering Graduate 2027* (`5018802101`, Hatfield, UK), *Hardware Engineering Graduate - Robotics & Automation 2027* (`5036245101`, Hatfield, UK), *Data Engineer*, *Analytics Engineer* |
| **Stripe** | `greenhouse:stripe` (`global`) | `770` | `0` | **`10`** | `760` | *Software Engineer, New Grad* (`8130930`, London, UK), *Backend/API Engineer* (UK), *Full Stack / SWE Money Movement* (London, UK) |
| **Jane Street** | `greenhouse:janestreet` (`global`) | `217` | `0` | **`14`** | `203` | *Software Engineer* (3 London postings), *Machine Learning Researcher / Engineer / Compiler / Performance Engineer* (London), *Compiler Engineer*, *Data Engineer* |
| **Wave 2 subtotal** | **3 boards** | **`1,046`** | **`0`** | **`31`** | **`1,015`** | Total monitored corpus now **`26` boards**, **`2,462` listed** (`21 priority`, `210 review`, `2,231 excluded`) |

*Note:* Ocado's 2027 graduate roles and Stripe's London New Grad role were screened as `review` (not `priority`) due to screening experience/location wording heuristics (`screen-2026-10-23.1`), while remaining directly surfaced in the attention queue and `fts_symbol`/`bm25_prefiltered` searches.
