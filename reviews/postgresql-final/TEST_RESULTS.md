# Executed test record — 23 October 2026

All database work used disposable local Docker containers. No hosted PostgreSQL or live provider endpoint was used. Production files/workflows were untouched. GitHub access fetched repository refs/objects and publishes only this review branch.

## Environment

- Docker Engine 29.4.1, Linux amd64.
- Image `pgvector/pgvector:pg17`, pinned for reproduction as `pgvector/pgvector@sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d`.
- PostgreSQL **17.11** (Debian 17.11-1.pgdg12+2); pgvector **0.8.7**.
- Python **3.13.12**; psycopg/psycopg-binary **3.2.13**; libpq **17.6** (`170006`).
- Architecture base `0cf1c42d2397c9ae9bbdf81aa6e6e1b150ce1b58`; production data pinned to `90434ea6d9a3457f9c8ba40f01ad25fa2ceffca3`.

The pinned digest and actual versions are in [results/versions.txt](results/versions.txt). No claim is made about a managed provider's currently available versions.

## Command and results

From the dedicated review worktree:

```bash
RESULTS_DIR="$PWD/reviews/postgresql-final/results" \
  bash reviews/postgresql-final/tests/run_local.sh
```

The exact Docker/psql/Python commands are in that script. In particular, it applies archived DDL in one container, applies the proposed initial DDL in a different fresh database, then applies the OPTIONAL embedding DDL only for the retrieval probe. Test connections use that second container's dynamically allocated `127.0.0.1` port and database `final_review`. Trailing spaces in the original constraint script's captured stdout are trimmed; the archived script itself is byte-identical.

| Executed check | Outcome / evidence |
|---|---|
| Recovered `t1_constraints.sh` against recovered `001_schema.sql` | **28 intended outcomes, zero BAD lines**; [original-constraints.txt](results/original-constraints.txt). The old shell script itself does not exit nonzero on BAD, so the runner checks its output. |
| Recovered `t2_concurrency.sh` against recovered schema | All six behavioural probes reproduced: READ COMMITTED waits/sees committed pointer; REPEATABLE READ serialization failure; owner FK insert during non-key posting update; pause observed by waiter; opposite-order deadlock; empty DO NOTHING RETURNING. [original-concurrency.txt](results/original-concurrency.txt). These are demonstrations, not a complete collector test. |
| Recovered `urlcheck.py` | Seven equal cases and the known `:443` mismatch; [original-url.txt](results/original-url.txt). |
| Proposed initial SQL on fresh DB | Applied successfully, including roles, grants, deferred/composite FKs, generated hashes/FTS, audit and views; [final-schema.txt](results/final-schema.txt). |
| New constraint/evidence/audit/publication probes | **16 assertion-based tests passed**; [final-tests.txt](results/final-tests.txt). Uses actual separate logins and multiple connections, including a `pg_stat_activity` assertion that the second publisher is waiting on the source lock. |
| Optional embedding DDL and SQL capability check | **One test passed**, using synthetic 384-d vectors; current join drops the old vector after a new current version. Also reproduces C++/C FTS token collision; [retrieval-tests.txt](results/retrieval-tests.txt). No ANN or model-quality benchmark. |
| Current-posting import/schema round-trip | **629/629** preserved current rows, source fields, descriptions/hashes, first-seen/basis and screening values; current DB-held inputs reproduce verdict/codes. **536** text files hash-verified. [reconciliation.json](results/reconciliation.json). Full owner/health/history importer is NOT implemented by this probe. |
| Existing offline application suite | **170 tests passed** against unchanged runtime source with `PYTHONPATH=src`; [offline-tests.txt](results/offline-tests.txt). Some existing test stdout says "scan"/"Wrote": those use mocked feeds and temporary paths, not real scans or production files. |

The 16 new tests cover identity/key consistency, same-source run pointers, same-posting evidence references, evidence retention, runtime privileges, required owner actor, audit login/claim distinction, multi-row transaction IDs, an audit write observed inside a transaction then rolled back, URL identity parity (including default ports, IPv6 and Unicode), manual key linking, programme years, verification binding, NUL native retention, actual incident.io/Cambridge pairs, newly collected-field baselines, formatting-only evidence, three consecutive material revisions, immutable normalizer output, tied-start-time ordering, out-of-order/replayed runs, concurrent publication, halfway publication rollback/retry with original receipt time, failure/malformed/valid-empty/positive-only feeds, source-locked rescreen with old processing-revision rejection, concurrent owner writes and legacy health with genuinely unavailable counters.

`tests/reference.py` is an executable transaction probe using the existing adapter/screener. Its material comparator implements the demonstrated Greenhouse/Ashby regression subset. It does not implement the complete production provider comparison policy, deployment rollout tooling, importer, or retry scheduler. Its normalizer-v2 immutability test uses an explicitly synthetic changed rendering. Do not install it as application code.

## Corrections during validation

Initial execution exposed an ambiguous USING join in the proposed workflow view and nullable URL CHECKs that accidentally rejected NULL. Both were corrected before the successful fresh-schema run. The first whole-harness attempt also lacked `PYTHONPATH=src` for the existing offline suite; that runner configuration was corrected. These are resolved review-development failures, not successful tests of the discarded SQL. The audit rollback test explicitly executes and observes the audit rows before forcing rollback; it does not accept an unrelated multi-statement protocol error as evidence of rollback.

## Still proposed, not executed

- Full migration of employers, source health history, leads, split verification observations, original event histories and nonempty owner fixtures, with field-by-field reconciliation.
- Recovery/reconciliation of all historical description/native references across Git; production import determinism and a production-format dump/restore drill.
- Complete Lever/material comparison implementation, request-coverage removals and a real single-response/snapshot absence contract. Existing offset paging remains positive-only under the selected design.
- Production two-stage mixed normalizer/source transitions, rescreen rollout across every source, terminal failure cleanup after worker death, SQLSTATE retry/backoff and lost-ack fault injection in application code.
- Any hosted-provider setup/backup, live source collection, GitHub Actions cutover, actual model inference or retrieval-quality evaluation.

The acceptance checklist in [FINAL_REVIEW.md](FINAL_REVIEW.md) defines these implementation/cutover gates. A successful design probe is not a claim that production migration is ready to run.

## Recovered artifact integrity

All requested files existed at the supplied scratchpad path. Copies were compared by SHA-256:

| Recovered file under `original/` | SHA-256 |
|---|---|
| `REVIEW_postgres_architecture.md` | `ba504926c7d6b654b0dde6ad3907dc96de19c7777950276d1bc3da594c7cfb35` |
| `pgtest/001_schema.sql` | `7bba9f89ecba79b81b76e4f4410c65dc417d0a6520f58c3a9434e9c563c94ee0` |
| `pgtest/t1_constraints.sh` | `86546bd970afd0ec15bc38bf7d16b9780ba8ba055c04618f1a950e4ca4f1f1ec` |
| `pgtest/t2_concurrency.sh` | `84baf882a2bf8c3b2d18fd997edb6e3d175294430707385b380fc8905fb8e55e` |
| `pgtest/urlcheck.py` | `fe4ee895e35d6d5776ba6c5568e186be02526ef48c79ac746ae3095cb8d9d811` |

The previous review's HNSW metrics and old corpus analysis remain archived claims. The present review did not rerun that HNSW experiment or claim new performance numbers.
