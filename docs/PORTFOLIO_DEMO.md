# Synthetic portfolio walkthrough

This is a small **local, reproducible engineering demo** for Graduate Job Watch. It exercises the **real MCP server interfaces** and existing owner/calendar code against a synthetic posting in an isolated PostgreSQL container. It is **not** a ChatGPT UI recording, an ATS network scan, a production deployment or a test of real client-side human approval.

## Run

You need Python 3.11+, Docker (with a running engine), and `git`. From the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[postgres,chat-mcp]'
PYTHON=.venv/bin/python bash scripts/test_postgres.sh --demo
```

The script builds/uses the reviewed PostgreSQL 17 + pgvector Docker image, creates a **fresh labelled, loopback-bound disposable database**, verifies its identity, bootstraps restricted roles, and removes the container on exit even when the test fails. No caller-supplied PostgreSQL URL is used. The demo does not contact Greenhouse, Lever, Ashby, AWS, ChatGPT or Proton; dependency/image installation may require ordinary package downloads.

The test prints five `DEMO` milestones **only after the corresponding assertions pass**. These are locally generated results, not a pre-recorded transcript:

1. **Research:** an official MCP SDK client calls `sql_query` over the restricted Research server and finds one synthetic software-engineering posting.
2. **Evidence:** `inspect_evidence` retrieves the stored parsed source and its untrusted-data label.
3. **Owner action:** a second MCP client prepares and commits an exact `set_triage` review for a revisit five days later. **The test simulates client confirmation only for synthetic data**; it does not claim that the ChatGPT approval UI was exercised.
4. **Failure case:** a second, conflicting review prepared before the first commit is refused as `stale_state`.
5. **Calendar and audit:** the existing minimal ICS projection contains one revisit event, and database audit records the restricted owner login.

The fixture is the existing `DatabaseTest` synthetic posting; URLs use `example.org`, and no real employer/application data is involved. The demo includes no screenshots and does not manufacture a ChatGPT conversation.

## What it demonstrates and does not

The walkthrough demonstrates the composition of the database model, the two separate MCP access paths, a guarded write, stale-state refusal and a privacy-minimised calendar output. It is a **functional integration demonstration at personal scale**, not a retrieval-quality benchmark, an availability claim, proof of sustained production uptime, or proof of a human clicking a confirmation button.

The real personal deployment and dated operational checks are described separately in [V1 release evidence](V1_RELEASE_REPORT.md) and the [integration release](INTEGRATION_RELEASE.md). See [agent authority](AGENT_OWNER_TOOLS.md) and [recovery documentation](AWS_PRODUCTION_HANDOVER.md) for the implementation and operational boundaries.

Run the complete PostgreSQL integration suite with `PYTHON=.venv/bin/python bash scripts/test_postgres.sh`; `--demo` runs only the dedicated walkthrough. Do **not** run this against a live database or replace its synthetic fixtures with private records.
