"""Final 10-step end-to-end qualification & rollout regression suite on disposable PostgreSQL 17."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest

from psycopg.conninfo import make_conninfo

from jobwatch.discovery.directory_discovery import BoardProbeResult, DirectoryBoard
from jobwatch.postgres import calendar_feed, reports, research
from jobwatch.postgres.agent_evidence import (
    SafeFetchResult,
    admit_corroborated_vacancies,
    corroborate_pending,
)
from jobwatch.postgres.auto_activation import (
    activate_validated_candidates,
    stage_auto_candidates,
    validate_staged_candidates,
)
from jobwatch.postgres.connection import connect
from jobwatch.postgres.directory_discovery import record_board_probe, run_discovery_cycle, sync_directory_boards
from jobwatch.postgres.evidence_mcp import server as evidence_mcp_server
from jobwatch.postgres.evidence_tools import EvidenceTools
from jobwatch.postgres.migrate import migrate
from jobwatch.postgres.publisher import Collector
from jobwatch.postgres.read_tools import ReadTools
from support import DatabaseTest, actor

ROOT = Path(__file__).resolve().parents[2]
SQL_DIR = ROOT / "sql/postgresql"


class FinalEndToEndQualificationTests(DatabaseTest):
    def _create_isolated_db(self, db_name: str):
        container = os.environ["JOBWATCH_PG_TEST_CONTAINER"]
        subprocess.run(["docker", "exec", container, "createdb", "-U", "postgres", "-O", "postgres", db_name], check=True)

        def admin(sql: str):
            p = subprocess.run(
                ["docker", "exec", "-i", container, "psql", "-X", "-U", "postgres", "-d", db_name,
                 "-v", "ON_ERROR_STOP=1", "-v", f"DBNAME={db_name}", "-f", "-"],
                input=sql, text=True, capture_output=True,
            )
            self.assertEqual(p.returncode, 0, p.stderr)

        admin(f"""
            GRANT CONNECT, CREATE ON DATABASE {db_name} TO jobwatch_migrator;
            GRANT CONNECT ON DATABASE {db_name} TO jobwatch_owner, jobwatch_collector;
            ALTER ROLE jobwatch_migrator IN DATABASE {db_name} SET search_path=jobwatch,pg_catalog;
            ALTER ROLE jobwatch_owner IN DATABASE {db_name} SET search_path=jobwatch,pg_catalog;
            ALTER ROLE jobwatch_collector IN DATABASE {db_name} SET search_path=jobwatch,pg_catalog;
        """)
        cfg = replace(self.target.config(), dsn=make_conninfo(self.target.config().dsn, dbname=db_name))
        with connect(cfg) as mig:
            migrate(mig)

        # Provision all production narrow roles cleanly (idempotent role creation + grants).
        admin("""
            DO $$ BEGIN
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jobwatch_agent_readonly') THEN
                CREATE ROLE jobwatch_agent_readonly LOGIN PASSWORD NULL NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 3;
              END IF;
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jobwatch_agent_owner') THEN
                CREATE ROLE jobwatch_agent_owner LOGIN PASSWORD NULL NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2;
              END IF;
              IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'jobwatch_calendar_reader') THEN
                CREATE ROLE jobwatch_calendar_reader LOGIN PASSWORD NULL NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2;
              END IF;
            END $$;
        """)
        for sql_name in (
            "agent_access.sql",
            "lead_deadline_access.sql",
            "posting_deadline_access.sql",
            "calendar_selection_access.sql",
            "discovery_access.sql",
            "directory_discovery_access.sql",
            "discovery_fidelity_access.sql",
            "agent_evidence_roles.sql",
        ):
            admin((SQL_DIR / sql_name).read_text(encoding="utf-8"))
        admin(f"""
            GRANT CONNECT ON DATABASE {db_name} TO jobwatch_agent_readonly, jobwatch_agent_owner,
              jobwatch_calendar_reader, jobwatch_agent_evidence, jobwatch_corroborator;
            ALTER ROLE jobwatch_agent_readonly IN DATABASE {db_name} SET search_path=jobwatch,pg_catalog;
            ALTER ROLE jobwatch_agent_evidence IN DATABASE {db_name} SET search_path=jobwatch,pg_catalog;
            ALTER ROLE jobwatch_corroborator IN DATABASE {db_name} SET search_path=jobwatch,pg_catalog;
        """)
        return container, admin, cfg

    def test_10_step_end_to_end_qualification_and_rollout_safeguards(self):
        db_name = "jobwatch_final_e2e_qual_test"
        container, admin, cfg = self._create_isolated_db(db_name)
        try:
            owner_cfg = replace(cfg, dsn=make_conninfo(cfg.dsn, user="jobwatch_owner"), purpose="runtime")
            col_cfg = replace(cfg, dsn=make_conninfo(cfg.dsn, user="jobwatch_collector"), purpose="runtime")
            ro_cfg = replace(cfg, dsn=make_conninfo(cfg.dsn, user="jobwatch_agent_readonly"), purpose="runtime")
            ev_cfg = replace(cfg, dsn=make_conninfo(cfg.dsn, user="jobwatch_agent_evidence"), purpose="runtime")
            cor_cfg = replace(cfg, dsn=make_conninfo(cfg.dsn, user="jobwatch_corroborator"), purpose="runtime")

            t0 = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)
            ashby_payload = json.dumps({
                "apiVersion": "1",
                "jobs": [{
                    "id": "neb-1",
                    "title": "Graduate C++ Systems Engineer 2027",
                    "location": "London, United Kingdom",
                    "jobUrl": "https://jobs.ashbyhq.com/nebula/neb-1",
                    "department": "Core Engineering",
                    "team": "Runtime",
                    "employmentType": "FullTime",
                    "descriptionPlain": (
                        "Join Nebula AI in London as a Graduate C++ Systems Engineer building low-latency "
                        "inference engines. UK graduate scheme starting September 2027. "
                        "Applications close 10 December 2026."
                    ),
                }]
            }).encode()
            ashby_hosted_html = (
                "<html><head><title>Nebula AI Jobs</title></head>"
                "<body><a href='https://www.nebula-ai.co.uk'>Nebula AI</a></body></html>"
            ).encode()
            nebula_careers_html = (
                "<html><head><title>Careers at Nebula AI</title></head>"
                "<body><h1>Careers</h1><a href='https://jobs.ashbyhq.com/nebula'>Open Roles</a></body></html>"
            ).encode()

            # ------------------------------------------------------------------
            # STEP 1: Automatic discovery (ETag capture, lock, auto steady-state)
            # ------------------------------------------------------------------
            with connect(col_cfg) as col:
                sync_directory_boards(
                    col,
                    [DirectoryBoard("ashby", "nebula", "Nebula AI", True, "ats-scrapers:ashby", "https://example.invalid/ashby.json")],
                    directory_source="ats-scrapers:ashby",
                    provenance={"sha256": "a" * 64},
                    observed_at=t0,
                )

                # Advisory lock prevents concurrent overlapping discovery runs.
                with connect(col_cfg) as lock_holder:
                    self.assertTrue(
                        lock_holder.execute(
                            "SELECT pg_try_advisory_lock(hashtextextended('jobwatch.discover-probe', 0)) AS ok"
                        ).fetchone()["ok"]
                    )
                    locked_res = run_discovery_cycle(
                        col, refresh_directories=False, probe_boards=True,
                        providers=("ashby",), delay_s=0.0, pause=lambda _: None, now=t0,
                    )
                    self.assertTrue(locked_res["locked_by_concurrent_run"])
                    self.assertEqual(locked_res["halted_reason"], "locked_by_concurrent_run")
                    self.assertEqual(locked_res["boards_probed"], 0)
                    lock_holder.execute("SELECT pg_advisory_unlock(hashtextextended('jobwatch.discover-probe', 0))")

                def fake_get(url: str, max_bytes: int = 8 * 1024 * 1024, **kwargs):
                    if "posting-api/job-board/nebula" in url:
                        return 200, ashby_payload, url
                    if url.rstrip("/") == "https://jobs.ashbyhq.com/nebula":
                        return 200, ashby_hosted_html, url
                    if "nebula-ai.co.uk" in url:
                        return 200, nebula_careers_html, url
                    return 404, b"", url

                # Run 1: unprobed board discovered + identity verified + complete evidence persisted.
                disc_res = run_discovery_cycle(
                    col, refresh_directories=False, probe_boards=True,
                    providers=("ashby",), max_boards=100, exploration_ratio=0.90,
                    delay_s=0.0, pause=lambda _: None, get_bytes=fake_get, now=t0,
                    auto_steady_state=True,
                )
                self.assertEqual(disc_res["discovery_phase"], "backfill")
                self.assertEqual(disc_res["boards_probed"], 1)
                self.assertEqual(disc_res["unique_boards_newly_probed"], 1)
                self.assertEqual(disc_res["unique_employers_with_uk_relevant"], 1)
                self.assertEqual(disc_res["candidates_inserted"], 1)
                self.assertEqual(disc_res["identity_checks"].get("verified_official_link"), 1)

                # Verify HTTP 304 conditional revisit preserves status/counts/candidates with 0 bytes.
                rec_304 = record_board_probe(
                    col,
                    BoardProbeResult(
                        provider="ashby", board="nebula", region="global",
                        probe_status="unchanged_304", http_status=304,
                        total_vacancies=0, uk_vacancies=0, priority_vacancies=0, review_vacancies=0,
                        custom_host_urls=False, elapsed_s=0.05,
                        requests_made=1, bytes_read=0, etag='"etag-v1"', not_modified=True,
                    ),
                    observed_at=t0 + timedelta(days=8),
                )
                self.assertEqual(rec_304["probe_status"], "unchanged_304")
                b_row = col.execute(
                    "SELECT probe_status, probe_count, last_304_at, priority_vacancies FROM jobwatch.discovered_boards WHERE board_key='ashby:nebula'"
                ).fetchone()
                self.assertEqual(b_row["probe_status"], "active_uk_relevant")
                self.assertEqual(b_row["probe_count"], 2)
                self.assertIsNotNone(b_row["last_304_at"])
                self.assertGreaterEqual(b_row["priority_vacancies"], 1)

                # Automatic steady-state transition once backlog unprobed < 500 and < 5%.
                steady_res = run_discovery_cycle(
                    col, refresh_directories=False, probe_boards=True,
                    providers=("ashby",), max_boards=100, exploration_ratio=0.90,
                    delay_s=0.0, pause=lambda _: None, get_bytes=fake_get,
                    now=t0 + timedelta(days=9),
                    auto_steady_state=True, steady_state_max_boards=20, steady_state_exploration_ratio=0.40,
                )
                self.assertEqual(steady_res["discovery_phase"], "steady_state")
                self.assertEqual(steady_res["effective_max_boards"], 20)
                self.assertEqual(steady_res["exploration_ratio"], 0.40)

            # ------------------------------------------------------------------
            # STEP 2: Retained discovery evidence is searchable via Research MCP
            # ------------------------------------------------------------------
            read_tools = ReadTools(ro_cfg, shared=True)
            s_disc = read_tools.call("search", {"query": "C++ Systems", "method": "fts_symbol", "scope": "discovery"})
            self.assertTrue(s_disc["ok"], s_disc)
            self.assertEqual(len(s_disc["data"]["items"]), 1)
            item0 = s_disc["data"]["items"][0]
            self.assertEqual(item0["candidate_key"], "ashby:nebula:neb-1")
            self.assertEqual(item0["evidence_completeness"], "complete")
            self.assertEqual(item0["source_origin"], "ats_discovery")
            self.assertIsNone(item0["linked_posting_key"])

            s_all_pre = read_tools.call("search", {"query": "C++ Systems", "method": "fts_symbol", "scope": "all"})
            self.assertTrue(s_all_pre["ok"], s_all_pre)
            self.assertEqual(len(s_all_pre["data"]["items"]), 0)
            self.assertEqual(len(s_all_pre["data"]["discovery_items"]), 1)
            self.assertIn("NOT comparable across corpora", s_all_pre["data"]["cross_corpus_note"])

            # ------------------------------------------------------------------
            # STEP 3: Bounded automatic activation + native collection + dedupe
            # ------------------------------------------------------------------
            with connect(owner_cfg) as own:
                st = stage_auto_candidates(own, dry_run=False, now=t0 + timedelta(hours=1))
                self.assertEqual(len(st["staged"]), 1, st)

            def fake_json_get(url: str):
                return json.loads(ashby_payload.decode())

            collector = Collector(config=col_cfg, actor="collector:e2e")
            val = validate_staged_candidates(collector, get_json=fake_json_get, pause=lambda _: None, fixture=True)
            self.assertEqual(len(val["validated"]), 1, val)
            with connect(owner_cfg) as own:
                act = activate_validated_candidates(own, dry_run=False, fixture=True)
                self.assertEqual(len(act["activated"]), 1, act)
                src_id = act["activated"][0]["source_id"]

            run_id, status = collector.collect_source(src_id, get_json=fake_json_get, pause=lambda _: None)
            self.assertEqual(status, "published")

            # Linked candidate is now in native `items` and OMITTED from `discovery_items` (zero duplicates!).
            s_all_post = read_tools.call("search", {"query": "C++ Systems", "method": "fts_symbol", "scope": "all"})
            self.assertTrue(s_all_post["ok"], s_all_post)
            self.assertEqual(len(s_all_post["data"]["items"]), 1)
            self.assertEqual(s_all_post["data"]["items"][0]["posting_key"], "ashby:nebula:neb-1")
            self.assertEqual(len(s_all_post["data"]["discovery_items"]), 0)

            # ------------------------------------------------------------------
            # STEP 4 & 5: Agent finds missing vacancy & deadline -> Evidence MCP
            # ------------------------------------------------------------------
            import httpx
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
            import uvicorn

            with tempfile.TemporaryDirectory(prefix="jw-e2e-mcp-") as td:
                sock = Path(td) / "evidence.sock"
                mcp = evidence_mcp_server(EvidenceTools(ev_cfg), port=8767)
                srv = uvicorn.Server(uvicorn.Config(mcp.streamable_http_app(), uds=str(sock), log_level="error", lifespan="on"))
                thread = threading.Thread(target=srv.run, daemon=True)
                thread.start()
                for _ in range(100):
                    if sock.exists():
                        break
                    time.sleep(0.05)
                self.assertTrue(sock.exists())

                obs_ids = {}

                async def submit_via_mcp():
                    transport = httpx.AsyncHTTPTransport(uds=str(sock))
                    async with httpx.AsyncClient(transport=transport, timeout=15.0, follow_redirects=False) as client:
                        async with streamable_http_client("http://127.0.0.1:8767/mcp", http_client=client) as (rx, tx, _):
                            async with ClientSession(rx, tx) as session:
                                await session.initialize()
                                tools = await session.list_tools()
                                by_name = {t.name: t for t in tools.tools}
                                self.assertEqual(set(by_name), {"submit_public_observation", "submit_public_vacancy"})
                                for t in by_name.values():
                                    self.assertIs(t.annotations.readOnlyHint, False)

                                r_vac = await session.call_tool("submit_public_vacancy", {
                                    "logical_contributor": "chatgpt-research",
                                    "company": "Nebula AI",
                                    "title": "Graduate ML Research Engineer 2027",
                                    "posting_url": "https://careers.nebula-ai.co.uk/jobs/grad-ml-2027",
                                    "location_text": "London, United Kingdom",
                                    "country": "GB",
                                    "countries": ["GB"],
                                    "description_text": (
                                        "Graduate ML Research Engineer 2027 in London, United Kingdom. "
                                        "Work on PyTorch and CUDA distributed training systems."
                                    ),
                                    "idempotency_key": "e2e-missing-vac-001",
                                })
                                self.assertFalse(r_vac.isError, r_vac)
                                obs_ids["vac_obs"] = r_vac.structuredContent["data"]["observation_id"]
                                obs_ids["vac_key"] = r_vac.structuredContent["data"]["candidate_key"]

                                r_dl = await session.call_tool("submit_public_observation", {
                                    "kind": "deadline_wording",
                                    "logical_contributor": "chatgpt-research",
                                    "source_url": "https://jobs.ashbyhq.com/nebula/neb-1",
                                    "claim_summary": "Official posting states deadline 10 December 2026",
                                    "observed_wording": "Applications close 10 December 2026.",
                                    "candidate_deadline_date": "2026-12-10",
                                    "posting_key": "ashby:nebula:neb-1",
                                    "idempotency_key": "e2e-deadline-002",
                                })
                                self.assertFalse(r_dl.isError, r_dl)
                                obs_ids["dl_obs"] = r_dl.structuredContent["data"]["observation_id"]

                                # Terminal 403 test case (must never be retried automatically)
                                r_403 = await session.call_tool("submit_public_observation", {
                                    "kind": "official_source_link",
                                    "logical_contributor": "chatgpt-research",
                                    "source_url": "https://blocked.nebula-ai.co.uk/careers",
                                    "claim_summary": "Blocked page test",
                                    "idempotency_key": "e2e-blocked-403-003",
                                })
                                self.assertFalse(r_403.isError, r_403)
                                obs_ids["blocked_obs"] = r_403.structuredContent["data"]["observation_id"]

                try:
                    asyncio.run(submit_via_mcp())
                finally:
                    srv.should_exit = True
                    thread.join(timeout=5)

            # ------------------------------------------------------------------
            # STEP 6: Recurring corroborator (lock, transient retry backoff,
            #         never looping on 403) + automatic deterministic admission
            # ------------------------------------------------------------------
            t_cor1 = t0 + timedelta(hours=3)
            vac_html = (
                "<html><body><h1>Graduate ML Research Engineer 2027</h1>"
                "<p>Graduate ML Research Engineer 2027 in London, United Kingdom. "
                "Work on PyTorch and CUDA distributed training systems.</p></body></html>"
            ).encode()
            dl_html = (
                "<html><body><h1>Graduate C++ Systems Engineer 2027</h1>"
                "<p>Applications close 10 December 2026.</p></body></html>"
            ).encode()

            def fetcher_pass1(url: str, budget=None):
                if "grad-ml-2027" in url:
                    return SafeFetchResult(0, b"", url, "198.51.100.10", False, 0, error_kind="timeout")
                if "neb-1" in url:
                    return SafeFetchResult(200, dl_html, url, "198.51.100.11", False, 0)
                if "blocked.nebula-ai.co.uk" in url:
                    return SafeFetchResult(403, b"Forbidden", url, "198.51.100.12", False, 0)
                return SafeFetchResult(404, b"", url, "198.51.100.13", False, 0)

            with connect(cor_cfg) as cor:
                # Advisory lock prevents overlapping corroborator runs.
                with connect(cor_cfg) as lock_holder:
                    self.assertTrue(
                        lock_holder.execute(
                            "SELECT pg_try_advisory_lock(hashtextextended('jobwatch.corroborator', 0)) AS ok"
                        ).fetchone()["ok"]
                    )
                    self.assertEqual(corroborate_pending(cor, limit=10, fetcher=fetcher_pass1, now=t_cor1), [])
                    lock_holder.execute("SELECT pg_advisory_unlock(hashtextextended('jobwatch.corroborator', 0))")

                # Pass 1: vac times out, deadline corroborates, blocked gets 403.
                res1 = corroborate_pending(cor, limit=10, fetcher=fetcher_pass1, now=t_cor1)
                by_id1 = {r["observation_id"]: r for r in res1}
                self.assertEqual(by_id1[obs_ids["vac_obs"]]["retrieval_status"], "timeout")
                self.assertEqual(by_id1[obs_ids["dl_obs"]]["overall_state"], "claim_corroborated")
                self.assertEqual(by_id1[obs_ids["blocked_obs"]]["retrieval_status"], "blocked_or_403")

                # Pass 2 (1 hour later — inside 6h backoff): NOTHING retried!
                res2 = corroborate_pending(cor, limit=10, fetcher=fetcher_pass1, now=t_cor1 + timedelta(hours=1))
                self.assertEqual(res2, [])

                # Pass 3 (7 hours later — past 6h backoff): ONLY transient timeout retried, NEVER 403!
                def fetcher_pass3(url: str, budget=None):
                    if "grad-ml-2027" in url:
                        return SafeFetchResult(200, vac_html, url, "198.51.100.10", False, 0)
                    raise AssertionError(f"Terminal observation must not be retried: {url}")

                res3 = corroborate_pending(cor, limit=10, fetcher=fetcher_pass3, now=t_cor1 + timedelta(hours=7))
                self.assertEqual(len(res3), 1)
                self.assertEqual(res3[0]["observation_id"], obs_ids["vac_obs"])
                self.assertEqual(res3[0]["overall_state"], "claim_corroborated")

                # Pass 4 (another 7 hours later): queue is completely drained!
                self.assertEqual(corroborate_pending(cor, limit=10, fetcher=fetcher_pass3, now=t_cor1 + timedelta(hours=14)), [])

            # Automatic deterministic admission of corroborated agent vacancy (no owner approval needed).
            with connect(col_cfg) as col:
                adm = admit_corroborated_vacancies(col)
                self.assertEqual(adm["admitted_count"], 1)
                self.assertEqual(adm["admitted"][0]["candidate_key"], obs_ids["vac_key"])
                self.assertIn(adm["admitted"][0]["verdict"], ("priority", "review"))
                self.assertNotEqual(adm["admitted"][0]["rules_version"], "unscreened-agent-submission")

            # ------------------------------------------------------------------
            # STEP 7 & 8: Research inspection finds corroborated vacancy & deadline
            # ------------------------------------------------------------------
            s_agent_vac = read_tools.call("search", {"query": "CUDA distributed", "method": "fts_symbol", "scope": "all"})
            self.assertTrue(s_agent_vac["ok"], s_agent_vac)
            self.assertEqual(len(s_agent_vac["data"]["discovery_items"]), 1)
            d_item = s_agent_vac["data"]["discovery_items"][0]
            self.assertEqual(d_item["candidate_key"], obs_ids["vac_key"])
            self.assertEqual(d_item["source_origin"], "agent_submission")
            self.assertEqual(d_item["corroboration_status"], "claim_corroborated")
            self.assertEqual(d_item["target_binding_status"], "bound_exact_url")
            self.assertGreaterEqual(len(s_agent_vac["data"]["agent_evidence_items"]), 1)

            # Corroborated deadline appears in posting_deadline_claims_distinct without owner action.
            q_dl = read_tools.call("sql_query", {
                "sql": "SELECT posting_key, value, provenance FROM jobwatch.posting_deadline_claims_distinct WHERE posting_key = %s",
                "parameters": ["ashby:nebula:neb-1"],
                "limit": 10,
            })
            self.assertTrue(q_dl["ok"], q_dl)
            dl_rows = q_dl["data"]["rows"]
            self.assertTrue(any(r[1] == "2026-12-10" and r[2] == "corroborated_agent_observation" for r in dl_rows), dl_rows)

            # ------------------------------------------------------------------
            # STEP 9 & 10: Explicit owner approval -> calendar publish ->
            #              automatic calendar update on verified deadline change
            # ------------------------------------------------------------------
            policy_sql = (SQL_DIR / "proposals/calendar_deadlines_explicit_only.sql").read_text(encoding="utf-8")
            with tempfile.TemporaryDirectory(prefix="jw-e2e-cal-") as cal_dir:
                cal_out = Path(cal_dir)
                # Before explicit owner selection: zero events in published deadlines calendar!
                with connect(owner_cfg) as own:
                    pre_cal = reports.calendar_data(own, deadline_selection_sql=policy_sql)
                pub0 = calendar_feed.publish(pre_cal, cal_out, initialize=True, feeds=["deadlines"])
                self.assertEqual(pub0["files"]["deadlines.ics"]["events"], 0)

                # Step 9: Owner explicitly approves selection of `ashby:nebula:neb-1`.
                with connect(owner_cfg) as own, own.transaction():
                    actor(own, "owner:explicit-approval")
                    own.execute(
                        "INSERT INTO jobwatch.calendar_selections(posting_key, mode, approved_deadline, recorded_by) "
                        "VALUES ('ashby:nebula:neb-1', 'include', '2026-12-10', 'owner:explicit-approval')"
                    )
                with connect(owner_cfg) as own:
                    cal1 = reports.calendar_data(own, deadline_selection_sql=policy_sql)
                pub1 = calendar_feed.publish(cal1, cal_out, feeds=["deadlines"])
                self.assertEqual(pub1["files"]["deadlines.ics"]["events"], 1)
                ics1 = (cal_out / "current" / "deadlines.ics").read_text(encoding="utf-8")
                self.assertIn("DTSTART;VALUE=DATE:20261210", ics1)
                self.assertIn("SEQUENCE:0", ics1)
                uid_line1 = [ln for ln in ics1.splitlines() if ln.startswith("UID:")][0]

                # Step 10: Deadline changes to 2026-12-20 with explicit supersession of prior claim.
                with connect(owner_cfg) as own, own.transaction():
                    actor(own, "owner:deadline-update")
                    old_ids = [
                        r["claim_id"]
                        for r in own.execute(
                            "SELECT claim_id FROM jobwatch.posting_deadline_claims WHERE posting_key='ashby:nebula:neb-1'"
                        ).fetchall()
                    ]
                    own.execute(
                        "INSERT INTO jobwatch.verifications("
                        "posting_key, kind, checked_at, checked_by, url, method, wording, value, deadline_supersedes, deadline_reason"
                        ") VALUES ('ashby:nebula:neb-1', 'deadline', %s, 'owner', "
                        "'https://jobs.ashbyhq.com/nebula/neb-1', 'official_posting', "
                        "'Applications extended until 20 December 2026.', '2026-12-20', %s::jsonb, 'Employer extended official closing date')",
                        (datetime.now(timezone.utc) - timedelta(minutes=1), json.dumps(old_ids)),
                    )

                with connect(owner_cfg) as own:
                    cal2 = reports.calendar_data(own, deadline_selection_sql=policy_sql)
                pub2 = calendar_feed.publish(cal2, cal_out, feeds=["deadlines"])
                self.assertEqual(pub2["files"]["deadlines.ics"]["events"], 1)
                ics2 = (cal_out / "current" / "deadlines.ics").read_text(encoding="utf-8")
                self.assertIn("DTSTART;VALUE=DATE:20261220", ics2)
                self.assertIn("SEQUENCE:1", ics2)
                uid_line2 = [ln for ln in ics2.splitlines() if ln.startswith("UID:")][0]
                self.assertEqual(uid_line1, uid_line2)

        finally:
            subprocess.run(["docker", "exec", container, "dropdb", "-U", "postgres", "--if-exists", "--force", db_name], check=False)


if __name__ == "__main__":
    unittest.main()
