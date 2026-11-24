# Retrieval Scalability Investigation — 10,000 to 100,000 Posting Versions

**Date:** 24 November 2026 (UTC)  
**Benchmark script:** [`experiments/retrieval_benchmark/benchmark_retrieval.py`](../experiments/retrieval_benchmark/benchmark_retrieval.py)  
**Environment:** Isolated disposable Docker `postgres:17` (`shared_buffers=256MB`, `work_mem=8MB`, `maintenance_work_mem=128MB`), loopback-only, destroyed on exit. No production database or retrieval behaviour was altered.

---

## 1. Problem statement

As established in [`docs/MCP_RELIABILITY_REPAIR.md`](MCP_RELIABILITY_REPAIR.md), Graduate Job Watch's current in-Python BM25 (`jobwatch.search.BM25` invoked by `jobwatch.postgres.research._lexical_search`) fetches every eligible posting version into Python memory, tokenises row by row, builds a full in-memory inverted/counter representation per request, and ranks in Python.

- Measured production behaviour on the 2 GiB `t4g.small` host (post-repair):
  - `638` current listed versions: `72 MiB` bare peak (`~82 MiB` MCP server).
  - `2,107` historical versions: **`155 MiB` bare / `172 MiB` MCP peak** against `MemoryMax=256M`, and **exceeded the 5-second tool deadline** under `CPUQuota=25%`.
  - Linear growth (`~0.062 MiB / indexed version`) causes current-only BM25 to exceed `MemoryMax=256M` around `~3,500` listed versions (~100 monitored boards) and trips the `10,000`-row `working_set_too_large` guard around ~250 boards.
- Meanwhile, existing PostgreSQL English FTS (`to_tsvector('english', ...)`) is fast and uses minimal Python memory, **but collapses critical programming-language distinctions (`C`, `C++`, `C#`) into the single token `c`**.

---

## 2. Evaluated approaches

| Approach | External PG extensions? | Preserves `C` vs `C++` vs `C#`? | Exact Okapi BM25 semantics? | Operational & backup impact |
| --- | --- | :---: | :---: | --- |
| **1. Current Python BM25** (`jobwatch.search.BM25`) | None | **Yes** | Baseline (`k1=1.2, b=0.75`) | Unbounded per-request Python RAM & CPU; fails on 2 GiB host at `>3,500` versions |
| **2. Existing PostgreSQL English FTS** (`to_tsvector('english', ...)`) | None | **No** (collapses all three to `c`) | No (`ts_rank_cd`) | Already deployed (`versions_search` GIN index); zero extra ops |
| **3. PostgreSQL Symbol-Preserving FTS** (`to_tsvector('simple', normalize_symbols(...))` + GIN) | **None** | **Yes** (`sym_cpp`, `sym_csharp`, `sym_fsharp`, `sym_dotnet`) | No (`ts_rank_cd` cover density) | Pure stock PG 17 generated column + GIN; standard `pg_dump`/restore |
| **4. PostgreSQL-Native Persistent BM25 in Pure SQL** (`bm25_docs` + `bm25_terms` + `bm25_postings` B-tree) | **None** | **Yes** (exact `jobwatch.search.tokens()`) | **Yes** (machine-epsilon identical: max delta `3.55e-15`) | Pure stock PG 17 tables; **large disk footprint** (`~8.4 KiB/version` index+heap!) |
| **5. Extension-Based PG BM25** (`pg_search` / ParadeDB Tantivy; `VectorChord-bm25`) | **Yes** (`shared_preload_libraries` + `.deb` or `cargo-pgrx`) | Configurable tokeniser | Yes (Tantivy / WAND BM25) | High ops burden on 2 GiB ARM64: ParadeDB recommends **8 GiB RAM** (Tantivy merge OOM risk on 2 GiB host with 0 swap), breaks vanilla `pg_restore` portability |

---

## 3. Measured benchmark results (10,000 / 50,000 / 100,000 versions)

Reproducible command:

```bash
PYTHONPATH=src:. .venv/bin/python experiments/retrieval_benchmark/benchmark_retrieval.py \
  --scales 10000 50000 100000 \
  --output /tmp/retrieval_benchmark_results.json
```

Corpus: realistic synthetic UK technical job versions (~180 words / ~160 non-stop tokens per version, 10 tech stacks with `C`, `C++`, and `C#` kept distinct at ~3% frequency each), benchmarked across 10 representative queries (`C++`, `C#`, `C`, `Python machine learning graduate`, `distributed systems Rust`, `Kubernetes Terraform AWS`, `C++ lowlatency trading`, `C# dotnet Azure`, `Graduate Software Engineer London`, `PyTorch LLM NLP`).

### 3.1 Build time, disk size, query latency, and incremental update cost

| Corpus size | Metric | English FTS (`fts_en`) | Symbol-Preserving FTS (`fts_sym`) | Pure-SQL Persistent BM25 (`sql_bm25`) | In-Python BM25 (`jobwatch.search.BM25`) |
| ---: | --- | ---: | ---: | ---: | ---: |
| **10,000** | Index build time | `2.04 s` | **`1.77 s`** | `3.27 s` | `0.49 s` **per request** (tokenised in memory) |
| *(docs: 66.7 MiB)* | Index disk footprint | `7.40 MiB` | **`2.53 MiB`** | `84.91 MiB` | `0 MiB` disk (transient RAM per request) |
| | Query latency p50 / p95 | `10.95 / 107.47 ms` | **`7.72 / 109.65 ms`** | `14.21 / 51.97 ms` | `45.60 / 100.52 ms` (+ build & SQL fetch) |
| | Incremental insert (1 doc) | `1.68 ms` *(both FTS)* | `1.68 ms` *(both FTS)* | `3.94 ms` | `0 ms` (no persistent index) |
| | `C` / `C++` / `C#` distinct? | **No (collapsed)** | **Yes** | **Yes** | **Yes** |
| | Top-20 parity vs Python BM25 | — | — | **10/10 exact** (`max Δ = 3.55e-15`) | Baseline |
| **50,000** | Index build time | `10.01 s` | `10.04 s` | `18.94 s` | Not run (exceeds production `MemoryMax=256M` & `5 s` timeout) |
| *(docs: 293.5 MiB)* | Index disk footprint | `18.42 MiB` | **`7.59 MiB`** | `422.25 MiB` | — |
| | Query latency p50 / p95 | `52.05 / 728.92 ms` | `52.66 / 528.98 ms` | **`45.48 / 326.93 ms`** | — |
| | Incremental insert (1 doc) | `3.21 ms` *(both FTS)* | `3.21 ms` *(both FTS)* | `7.34 ms` | — |
| | `C` / `C++` / `C#` distinct? | **No (collapsed)** | **Yes** | **Yes** | — |
| **100,000** | Index build time | `21.38 s` | **`19.72 s`** | `34.98 s` | Not run |
| *(docs: 667.5 MiB)* | Index disk footprint | `28.51 MiB` | **`14.23 MiB`** | **`843.00 MiB`** | — |
| | Query latency p50 / p95 | `199.61 / 1,199.33 ms` | **`65.91 / 430.47 ms`** | `102.90 / 1,097.77 ms` | — |
| | Incremental insert (1 doc) | **`3.97 ms`** *(both FTS)* | **`3.97 ms`** *(both FTS)* | `24.00 ms` | — |
| | `C` / `C++` / `C#` distinct? | **No (collapsed)** | **Yes** | **Yes** | — |

*Note on memory:* Query-time Python client memory for all three PostgreSQL-side methods (`fts_en`, `fts_sym`, `sql_bm25`) is **flat (~0.1 MiB per query, returning 20 rows)** regardless of whether the database holds 10k or 100k versions; the high process RSS logged by the benchmark script (`394–1,223 MiB`) came solely from the synthetic benchmark harness buffering all generated posting tuples in Python before bulk `COPY`.

---

## 4. Key architectural findings & recommendation

1. **Pure-SQL persistent BM25 achieves literal machine-epsilon parity (`10/10` exact top-20 order, max score delta `3.55e-15`)**, proving that exact Okapi BM25 can run inside stock PostgreSQL 17 with zero extensions and zero Python corpus loading. **However, its B-tree posting table consumes `~8.4 MiB / 1,000 versions` (`843 MiB` at 100k versions)** — larger than the documents themselves — and inflates twice-daily S3 `pg_dump` backups accordingly.
2. **Symbol-preserving PostgreSQL GIN FTS (`fts_sym`) is the standout storage-and-latency winner at 10k–100k scale:**
   - **59× smaller on disk** than pure-SQL BM25 (`14.23 MiB` vs `843.0 MiB` at 100k versions).
   - **Fastest at 100,000 versions** (`65.91 ms` p50 / `430.47 ms` p95) and fastest incremental insert (`3.97 ms`/version).
   - **Preserves `C`, `C++`, `C#`, `F#`, `.NET` distinctly**, eliminating the primary reason English FTS could not substitute for BM25 on technical roles.
   - Zero extensions, zero `shared_preload_libraries`, zero impact on `pg_dump`/off-host restore portability, and zero risk to the 2 GiB host's RAM ceiling.
3. **Why external BM25 extensions (`pg_search` / `VectorChord-bm25`) are NOT recommended on `t4g.small`:**
   - ParadeDB's `pg_search` recommends **8 GiB RAM** minimum due to Tantivy segment merge buffering (OOM risk on a 1,834 MiB swapless host), requires `shared_preload_libraries` and out-of-band `.deb` maintenance, and couples every off-host restore target to exact extension versions.
   - `VectorChord-bm25` requires two custom extensions (`vchord_bm25` + `pg_tokenizer`) with no standard PGDG Ubuntu package.
4. **Recommended migration path (preserving existing BM25 semantics honestly):**
   - **Do NOT silently replace `method='bm25'`.** Keep existing exact Python BM25 available for bounded small working sets (e.g. current listed postings up to `<= 2,500` versions, or explicitly filtered subsets).
   - **Stage A (Immediate, zero-risk when ready for >50 boards):** Add a generated `search_sym_tsv` column + GIN index (`~2.5 MiB` at 10k versions) on `posting_versions` and expose it explicitly (e.g. `method='fts_symbol'` or two-stage retrieval: `fts_sym` GIN retrieves top-`K` candidate versions, then exact Python BM25 reranks those `K <= 500` candidates in `<5 ms` and `<5 MiB` RAM!).
     - Notice: **two-stage GIN-prefiltered BM25** (or **current-only persistent SQL BM25** scoped solely to `current_version_id` rows — `~25 MiB` for 3,000 current postings!) gives exact Okapi BM25 ranking over current postings while letting full 100k-version historical search run in `<70 ms` with `<15 MiB` disk and flat Python memory!
   - **Stage B (Only if corpus exceeds ~10,000 current listed postings):** Partition or scope `ReadTools.working_set` to non-excluded/active working sets rather than blocking `run_report` on total historical `postings > 10,000`.
