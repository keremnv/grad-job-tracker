# Review-only PostgreSQL finalization artifacts

Start with [FINAL_REVIEW.md](FINAL_REVIEW.md). This directory is a design proposal and disposable test harness, not production migrations or a new runtime implementation.

- [001_proposed_schema.sql](001_proposed_schema.sql): complete replacement for the earlier scratch DDL; initial schema/roles/grants/audit/views.
- [002_deferred_embeddings.sql](002_deferred_embeddings.sql): optional later embedding-table example; model/dimension still require selection.
- [TEST_RESULTS.md](TEST_RESULTS.md): executed commands, versions, results and remaining implementation tests.
- `original/REVIEW_postgres_architecture.md` and `original/pgtest/{001_schema.sql,t1_constraints.sh,t2_concurrency.sh,urlcheck.py}`: byte-identical recovered artifacts. Original embedded passwords are disposable test credentials, not deployment settings.
- `tests/`: executable reference probes, actual provider regression pairs and an isolated runner.
- `results/`: captured text output and the pinned current-corpus reconciliation manifest; no database dump or private owner row values.

Reproduce from a checkout with Docker, Bash, Python 3.11+, `venv`, `rg`, `bc` and network access for the pinned image/psycopg wheel:

```bash
RESULTS_DIR=/tmp/jobwatch-review-results bash reviews/postgresql-final/tests/run_local.sh
```

The runner creates two fresh uniquely named local containers, exposes only the final container's ephemeral loopback port, and removes both containers/volumes on exit. It accepts no hosted DSN. It creates a temporary virtual environment, never installs or edits the repository's runtime package. Source data is read from pinned Git objects; it is not read or rewritten through production file writers. The corpus probe needs commit `90434ea6d9a3457f9c8ba40f01ad25fa2ceffca3` in the local Git object database (`git fetch origin main` if absent).

The archived scripts contain hard-coded old container/output paths. The runner substitutes those two paths in temporary execution copies, retaining the archived originals unchanged. Their old exception/output probes are not confused with the new schema's assertion-based tests.

Nothing here authorizes deploying a database, running a live scan, changing GitHub Actions, modifying production data or merging to main.
