# Development guide

Read this only when the owner has asked for development work. Day-to-day
operation is governed by `AGENTS.md`. The original build specification is kept
for reference in `history/CODEX_BUILD_SPEC.md`; its backlog is not a mandate.

## Commands

```bash
python -m venv .venv && .venv/bin/python -m pip install -e .
.venv/bin/python -m unittest discover -s tests -v     # offline; no data/ or config/ needed
.venv/bin/python -m jobwatch.cli validate-data         # checks the live operational files
.venv/bin/python -m jobwatch.cli eval run --baseline   # synthetic screening diagnostics
```

Optional PostgreSQL setup, migrations and disposable integration tests are
documented in [POSTGRESQL_DEVELOPMENT.md](POSTGRESQL_DEVELOPMENT.md). The file-backed
runtime and scheduled production data path remain authoritative and unchanged.
The separate development collection/history path is described in
[POSTGRESQL_COLLECTION.md](POSTGRESQL_COLLECTION.md).

Never run `scan` against `data/` locally; use scratch `--state`, `--report`,
`--config` and `--verifications` paths. Live provider checks are separate from
unit tests and should be rare.

## Module map

| Module | Responsibility |
|---|---|
| `feeds.py` | Bounded, fail-closed Greenhouse/Lever/Ashby clients. Any malformed record fails the whole board. |
| `identity.py` | Posting key, exact URL (fragment kept), loose URL (fragment dropped; refusal/warnings only). |
| `filtering.py` | Deterministic screening: `screen(inputs)` → verdict, family, codes, rules version, input fingerprint. |
| `state.py` | Discovery index (schema 2), content-addressed source text, health file, migration, source/screening events. |
| `opportunities.py` | The only join of index × ledger × triage × verifications × health. |
| `commands.py`, `contract.py` | Agent commands and the `jobwatch.cli/1` envelope. |
| `triage.py`, `verifications.py`, `storage.py` | Owner/evidence writes: lock, snapshot, atomic replace. |
| `evaluation.py`, `search.py` | Offline diagnostics and BM25. |
| `postgres/` | Optional PostgreSQL development collector, material comparison, immutable evidence/history, rescreening, connections and migrations. The file-backed CLI is unchanged. |
| `datacheck.py` | `validate-data`: operational checks kept out of unit tests. |
| `applications.py`, `overview.py`, `calendar.py`, `leads.py` | Ledger, weekly/requirements reports, ICS export, lead registry. |

## Invariants to preserve

- A failed, truncated, rate-limited or malformed response is a **failure**,
  never an empty board. Failed boards never produce `not_seen`.
- Every identity from a successful board is recorded, whatever its verdict.
- Source events contain source fields only; judgment changes are `rescreened`
  screening events with a cause. Bump `RULES_VERSION` whenever screening
  behaviour changes.
- Never invent deadlines, salary, eligibility, sponsorship, start dates or
  closure. Keep exact wording, URL, observation time and timezone.
- Owner files (ledger, triage, verifications, leads) are never written by scans.
- Scan write order is texts → index → health → orphan removal → report; the
  health file must never be ahead of the index (`tests/test_durability.py`).
  `scan` exits 2 for anything that must not be committed; `scan.yml` commits
  only on exit 0/1.
- Write commands use the real clock and refuse `--as-of`; tests patch
  `jobwatch.commands.utcnow`. Evidence times (`--verified-at`) are explicit.
- Unit tests must not read `data/` or `config/`; add operational checks to
  `datacheck.py`. Add a regression test (or a case in
  `tests/fixtures/screening_regressions.json`) for every fixed bug.
- The production file-backed runtime remains standard-library-only. PostgreSQL
  dependencies are optional and used only for the explicitly invoked development
  foundation. No LLM calls, paid services, browser automation or hosted database
  provisioning in this stage.
