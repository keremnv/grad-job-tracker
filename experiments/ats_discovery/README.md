# ATS-Wide Discovery Feasibility Experiments (Isolated)

Isolated, read-only research scripts and offline tests supporting
[`docs/ATS_WIDE_DISCOVERY_FEASIBILITY.md`](../../docs/ATS_WIDE_DISCOVERY_FEASIBILITY.md).

**Safety guarantees:**
- Zero writes to the production AWS PostgreSQL database, S3 backups, systemd timers, `config/employers.json`, or `src/jobwatch/`.
- Bounded request budgets, explicit inter-request delays, 4–8 MiB response caps, and clean halt on HTTP 429.
- `workable_persistence_demo.py` creates and destroys its own loopback-only disposable Docker `postgres:17` container.

## Files

- `workable_discovery.py` — Unseeded bounded UK graduate/early-career Workable search (`jobs.workable.com/api/v1/jobs`), unchanged `jobwatch.filtering.screen()` screening, corroborated `apply.workable.com` widget shortcode resolution (with multi-location shortcode grouping), and post-hoc held-out Stalis (`1C9CCEA6B4`) acceptance evaluation.
- `workable_persistence_demo.py` — Isolated disposable PostgreSQL 17 proof demonstrating both Path A (zero-migration `jobwatch.leads`) and Path B (native `sources`/`postings`/`posting_versions`/`posting_events` + unchanged `research.search` BM25/FTS and `research.evidence`).
- `directory_discovery.py` — Bounded multi-provider live probe across Greenhouse, Ashby, and Lever using `kalil0321/ats-scrapers` (MIT) across manual-watch matches, UK-hinted unknown boards, and pure random unknown boards.
- `test_discovery_experiments.py` — Offline unit tests (7 tests, zero outbound network).

## Quick reproduction

```bash
PYTHONPATH=src:. .venv/bin/python -m unittest discover -s experiments/ats_discovery -p 'test_*.py' -v
PYTHONPATH=src:. .venv/bin/python experiments/ats_discovery/workable_discovery.py --output /tmp/workable_result.json
PYTHONPATH=src:. .venv/bin/python experiments/ats_discovery/workable_persistence_demo.py --discovery-json /tmp/workable_result.json
PYTHONPATH=src:. .venv/bin/python experiments/ats_discovery/directory_discovery.py --output /tmp/directory_result.json
```
