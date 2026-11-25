"""Disposable PG 17 tests for migration 0013, corroborator HTML/link semantics, and isolated Evidence MCP."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest

from psycopg.conninfo import make_conninfo

from jobwatch.postgres.agent_evidence import (
    AgentEvidenceIngestor,
    ObservationSubmission,
    SafeFetchResult,
    corroborate_observation,
)
from jobwatch.postgres.connection import connect
from jobwatch.postgres.directory_discovery import request_board_investigation, select_probe_batch_prioritized
from jobwatch.postgres.evidence_mcp import server as evidence_mcp_server
from jobwatch.postgres.evidence_tools import EvidenceTools
from jobwatch.postgres.migrate import migrate
from support import DatabaseTest, actor

ROOT = Path(__file__).resolve().parents[2]
ROLES_SQL = ROOT / "sql/postgresql/agent_evidence_roles.sql"


class Migration0013AndEvidenceMcpTests(DatabaseTest):
    def _create_isolated_db(self, db_name: str):
        container = os.environ["JOBWATCH_PG_TEST_CONTAINER"]
        subprocess.run(["docker", "exec", container, "createdb", "-U", "postgres", "-O", "postgres", db_name], check=True)

        def admin(sql: str):
            p = subprocess.run(
                ["docker", "exec", "-i", container, "psql", "-X", "-U", "postgres", "-d", db_name, "-v", "ON_ERROR_STOP=1", "-f", "-"],
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
        admin(ROLES_SQL.read_text(encoding="utf-8"))
        admin(
            f"GRANT CONNECT ON DATABASE {db_name} TO jobwatch_agent_evidence, jobwatch_corroborator; "
            f"ALTER ROLE jobwatch_agent_evidence IN DATABASE {db_name} SET search_path=jobwatch,pg_catalog; "
            f"ALTER ROLE jobwatch_corroborator IN DATABASE {db_name} SET search_path=jobwatch,pg_catalog;"
        )
        return container, admin, cfg

    def _seed_base(self, owner_cfg, col_cfg):
        with connect(owner_cfg) as own, own.transaction():
            actor(own)
            emp_id = own.execute(
                "INSERT INTO jobwatch.employers(slug, name, careers_url) "
                "VALUES ('anima', 'Anima Health', 'https://www.animahealth.com/company/careers') RETURNING id"
            ).fetchone()["id"]
            src_id = own.execute(
                "INSERT INTO jobwatch.sources(provider, board, region, employer_id, status, validation_url, "
                "endpoint_checked_at, mapping_verified_at, mapping_verified_by, mapping_evidence) "
                "VALUES ('ashby', 'anima', 'global', %s, 'monitored', 'https://www.animahealth.com/company/careers', "
                "now(), now(), 'owner', 'official careers page') RETURNING id",
                (emp_id,),
            ).fetchone()["id"]
            cc_src = own.execute(
                "INSERT INTO jobwatch.sources(provider, board, region, employer_id, status, validation_url, "
                "endpoint_checked_at, mapping_verified_at, mapping_verified_by, mapping_evidence) "
                "VALUES ('greenhouse', 'cc', 'global', %s, 'monitored', 'https://job-boards.greenhouse.io/cc', "
                "now(), now(), 'owner', 'official careers page') RETURNING id",
                (emp_id,),
            ).fetchone()["id"]
        with connect(col_cfg) as col, col.transaction():
            col.execute(
                "INSERT INTO jobwatch.discovered_boards(board_key, provider, board, company_hint, uk_hint, directory_sources, first_discovered_at, last_directory_seen_at) "
                "VALUES ('ashby:wayve', 'ashby', 'wayve', 'Wayve', false, ARRAY['ats-scrapers:ashby'], now() - interval '30 days', now()), "
                "       ('ashby:synthesia', 'ashby', 'synthesia', 'Synthesia', false, ARRAY['ats-scrapers:ashby'], now() - interval '30 days', now())"
            )
            v1 = "11111111-1111-4111-8111-111111111101"
            v2 = "11111111-1111-4111-8111-111111111102"
            col.execute(
                "INSERT INTO jobwatch.postings(posting_key, source_id, provider, board, native_id, status, "
                "first_seen_at, first_seen_basis, current_version_id, verdict, family, codes, rules_version, "
                "screening_input_sha256, screening_basis, screened_at) "
                "VALUES ('greenhouse:cc:1', %s, 'greenhouse', 'cc', '1', 'listed', now() - interval '5 days', 'observed', "
                "%s, 'priority', 'software', ARRAY['location.uk'], 'screen-2026-11-25.1', repeat('a', 64), 'full', now())",
                (cc_src, v2),
            )
            col.execute(
                "INSERT INTO jobwatch.posting_versions(id, posting_key, source_id, origin, evidence, observed_at, "
                "normalizer_version, normalized_sha256, company, title, posting_url, location_text, description_text, deadlines) "
                "VALUES (%s, 'greenhouse:cc:1', %s, 'git_history', E'\\\\x7b7d', now() - interval '5 days', "
                "'normalizer-v1', repeat('2', 64), 'Cambridge Consultants', 'Grad SWE', 'https://job-boards.greenhouse.io/cc/jobs/1', 'Cambridge, UK', 'desc', "
                "'[{\"wording\": \"&lt;p&gt;&lt;strong&gt;There is no deadline for this role.&lt;/strong&gt;&lt;/p&gt;\", \"source_field\": \"description\", \"url\": \"https://job-boards.greenhouse.io/cc/jobs/1\", \"observed_at\": \"2026-11-20T00:00:00Z\"}]'::jsonb)",
                (v1, cc_src),
            )
            col.execute(
                "INSERT INTO jobwatch.posting_versions(id, posting_key, source_id, origin, evidence, observed_at, "
                "normalizer_version, normalized_sha256, company, title, posting_url, location_text, description_text, deadlines) "
                "VALUES (%s, 'greenhouse:cc:1', %s, 'git_history', E'\\\\x7b2276223a327d', now() - interval '1 day', "
                "'normalizer-v2', repeat('4', 64), 'Cambridge Consultants', 'Grad SWE', 'https://job-boards.greenhouse.io/cc/jobs/1', 'Cambridge, UK', 'desc', "
                "'[{\"wording\": \"There is no deadline for this role.\", \"source_field\": \"description\", \"url\": \"https://job-boards.greenhouse.io/cc/jobs/1\", \"observed_at\": \"2026-11-24T00:00:00Z\"}]'::jsonb)",
                (v2, cc_src),
            )
        return src_id

    def test_0013_distinct_deadlines_targeted_probe_corroborator_and_isolated_mcp(self):
        db_name = "jobwatch_0013_evidence_mcp_test"
        container, admin, cfg = self._create_isolated_db(db_name)
        try:
            owner_cfg = replace(cfg, dsn=make_conninfo(cfg.dsn, user="jobwatch_owner"), purpose="runtime")
            col_cfg = replace(cfg, dsn=make_conninfo(cfg.dsn, user="jobwatch_collector"), purpose="runtime")
            ev_cfg = replace(cfg, dsn=make_conninfo(cfg.dsn, user="jobwatch_agent_evidence"), purpose="runtime")
            cor_cfg = replace(cfg, dsn=make_conninfo(cfg.dsn, user="jobwatch_corroborator"), purpose="runtime")

            src_id = self._seed_base(owner_cfg, col_cfg)

            # 1. Distinct deadline view deduplicates legacy HTML & multi-version wording while preserving raw claims.
            with connect(owner_cfg) as oconn:
                raw_claims = oconn.execute("SELECT * FROM jobwatch.posting_deadline_claims WHERE posting_key='greenhouse:cc:1'").fetchall()
                self.assertEqual(len(raw_claims), 2)
                distinct = oconn.execute("SELECT * FROM jobwatch.posting_deadline_claims_distinct WHERE posting_key='greenhouse:cc:1'").fetchall()
                self.assertEqual(len(distinct), 1)
                self.assertEqual(distinct[0]["normalized_wording"], "There is no deadline for this role.")
                self.assertEqual(distinct[0]["observation_count"], 2)
                self.assertEqual(len(distinct[0]["claim_ids"]), 2)
                self.assertTrue(distinct[0]["present_in_current_version"])

            with connect(col_cfg) as cconn:
                # 2. Targeted investigation prioritizes board without activating it
                req_res = request_board_investigation(cconn, ["ashby:wayve"], requested_by="owner:eval", reason="Benchmark UK AI employer")
                self.assertEqual(req_res["requested"], ["ashby:wayve"])
                batch = select_probe_batch_prioritized(cconn, limit=2, providers=("ashby",), exploration_ratio=0.0)
                self.assertEqual(batch[0]["board_key"], "ashby:wayve")
                self.assertEqual(batch[0]["selection_reason"], "targeted_investigation")

            # 3. Corroborator HTML/link semantics (Anima Astro + script guard + JS shell + agent board_key trigger)
            ing = AgentEvidenceIngestor(resolve_dns=False)
            now = datetime.now(timezone.utc)
            with connect(ev_cfg) as econn:
                anima_obs = ing.record_observation(econn, ObservationSubmission(
                    kind="source_identity", logical_contributor="eval-agent", observed_at=now,
                    source_url="https://www.animahealth.com/company/careers",
                    claim_summary="Anima careers page links to its official Ashby board",
                    observed_wording="jobs.ashbyhq.com/anima",
                    source_id=src_id, idempotency_key="anima-astro-corrob-001",
                ))
                script_obs = ing.record_observation(econn, ObservationSubmission(
                    kind="posting_evidence", logical_contributor="eval-agent", observed_at=now,
                    source_url="https://job-boards.greenhouse.io/cc/jobs/1",
                    claim_summary="Hidden script string must never match visible wording",
                    observed_wording="SECRET_SCRIPT_ONLY_PHRASE",
                    posting_key="greenhouse:cc:1", idempotency_key="script-guard-002",
                ))
                shell_obs = ing.record_observation(econn, ObservationSubmission(
                    kind="posting_evidence", logical_contributor="eval-agent", observed_at=now,
                    source_url="https://job-boards.greenhouse.io/cc/jobs/1",
                    claim_summary="JS-rendered shell should report unavailable",
                    observed_wording="Graduate Software Engineer",
                    posting_key="greenhouse:cc:1", idempotency_key="js-shell-guard-003",
                ))
                ing.record_observation(econn, ObservationSubmission(
                    kind="official_source_link", logical_contributor="eval-agent", observed_at=now,
                    source_url="https://www.synthesia.io/careers",
                    claim_summary="Synthesia careers page uses ashby:synthesia",
                    observed_wording="https://jobs.ashbyhq.com/synthesia",
                    board_key="ashby:synthesia", idempotency_key="synthesia-board-004",
                ))

            with connect(col_cfg) as cconn:
                batch2 = select_probe_batch_prioritized(cconn, limit=5, providers=("ashby",), exploration_ratio=0.0)
                reasons = {r["board_key"]: r["selection_reason"] for r in batch2}
                self.assertEqual(reasons["ashby:wayve"], "targeted_investigation")
                self.assertEqual(reasons["ashby:synthesia"], "targeted_investigation")

            anima_html = (
                "<!doctype html><html><head><title>Careers | Anima</title>"
                "<style>.x{display:none}</style><script>const boot = 'inline_js_noise';</script></head>"
                "<body><h1>Your work is for better care</h1><p>Join engineers at Anima.</p>"
                "<a href='https://jobs.ashbyhq.com/Anima'>Apply for a role</a></body></html>"
            ).encode()
            script_html = (
                "<html><head><script>var s = 'SECRET_SCRIPT_ONLY_PHRASE';</script></head>"
                "<body><h1>Grad SWE</h1><p>Visible job description text for Cambridge Consultants graduate role.</p></body></html>"
            ).encode()
            js_shell_html = (
                "<html><head><script>" + ("a" * 600) + "</script><script>window.__APP__={};</script></head>"
                "<body><div id='root'></div></body></html>"
            ).encode()

            with connect(cor_cfg) as co_conn:
                r_anima = corroborate_observation(
                    co_conn, anima_obs["observation_id"],
                    fetcher=lambda u, budget=None: SafeFetchResult(200, anima_html, u, "216.150.16.193", False, 0),
                )
                self.assertEqual(r_anima["overall_state"], "claim_corroborated")
                self.assertEqual(r_anima["wording_status"], "not_found")
                self.assertEqual(r_anima["claim_status"], "corroborated")
                self.assertEqual(r_anima["evidence_details"]["link_evidence"]["matched_via"], "a_href")
                self.assertNotIn("inline_js_noise", r_anima["evidence_excerpt"])
                self.assertIn("Your work is for better care", r_anima["evidence_excerpt"])

                r_script = corroborate_observation(
                    co_conn, script_obs["observation_id"],
                    fetcher=lambda u, budget=None: SafeFetchResult(200, script_html, u, "198.51.100.1", False, 0),
                )
                self.assertEqual(r_script["wording_status"], "not_found")
                self.assertEqual(r_script["overall_state"], "source_retrieved")
                self.assertNotIn("SECRET_SCRIPT_ONLY_PHRASE", r_script["evidence_excerpt"])

                r_shell = corroborate_observation(
                    co_conn, shell_obs["observation_id"],
                    fetcher=lambda u, budget=None: SafeFetchResult(200, js_shell_html, u, "198.51.100.2", False, 0),
                )
                self.assertEqual(r_shell["retrieval_status"], "js_shell_or_empty")
                self.assertEqual(r_shell["overall_state"], "evidence_unavailable")

            # 4. Isolated Evidence MCP over Unix socket + identity/budget/confirmation boundaries
            import httpx
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client
            import uvicorn

            # Set hourly_limit = 6 (4 already used above -> 2 remaining for MCP calls!)
            admin(
                "INSERT INTO jobwatch.agent_evidence_budgets(db_login, hourly_limit, daily_limit) "
                "VALUES ('jobwatch_agent_evidence', 6, 20) "
                "ON CONFLICT (db_login) DO UPDATE SET hourly_limit = 6, daily_limit = 20;"
            )
            bad_res = EvidenceTools(owner_cfg).call("submit_public_observation", {
                "kind": "official_source_link",
                "logical_contributor": "chatgpt-research",
                "source_url": "https://www.example.com/careers",
                "claim_summary": "Must fail identity guard",
            })
            self.assertFalse(bad_res["ok"])
            self.assertEqual(bad_res["error"]["code"], "wrong_database_identity")

            with tempfile.TemporaryDirectory(prefix="jw-ev-mcp-") as td:
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

                async def exercise_mcp():
                    transport = httpx.AsyncHTTPTransport(uds=str(sock))
                    async with httpx.AsyncClient(transport=transport, timeout=15.0, follow_redirects=False) as client:
                        async with streamable_http_client("http://127.0.0.1:8767/mcp", http_client=client) as (rx, tx, _):
                            async with ClientSession(rx, tx) as session:
                                await session.initialize()
                                tools = await session.list_tools()
                                by_name = {t.name: t for t in tools.tools}
                                self.assertEqual(set(by_name), {"submit_public_observation", "submit_public_vacancy"})
                                for t in by_name.values():
                                    self.assertIsNotNone(t.annotations)
                                    self.assertIs(t.annotations.readOnlyHint, False)
                                    self.assertIs(t.annotations.idempotentHint, True)

                                r1 = await session.call_tool("submit_public_observation", {
                                    "kind": "deadline_wording",
                                    "logical_contributor": "chatgpt-research",
                                    "source_url": "https://job-boards.greenhouse.io/cc/jobs/1",
                                    "claim_summary": "Official posting states deadline 2026-12-15",
                                    "observed_wording": "Applications close 15 December 2026.",
                                    "candidate_deadline_date": "2026-12-15",
                                    "posting_key": "greenhouse:cc:1",
                                    "idempotency_key": "mcp-obs-001",
                                })
                                self.assertFalse(r1.isError)
                                self.assertEqual(r1.structuredContent["data"]["status"], "recorded")

                                r1_dup = await session.call_tool("submit_public_observation", {
                                    "kind": "deadline_wording",
                                    "logical_contributor": "chatgpt-research",
                                    "source_url": "https://job-boards.greenhouse.io/cc/jobs/1",
                                    "claim_summary": "Official posting states deadline 2026-12-15",
                                    "observed_wording": "Applications close 15 December 2026.",
                                    "candidate_deadline_date": "2026-12-15",
                                    "posting_key": "greenhouse:cc:1",
                                    "idempotency_key": "mcp-obs-001",
                                })
                                self.assertFalse(r1_dup.isError)
                                self.assertEqual(r1_dup.structuredContent["data"]["status"], "duplicate_replayed")

                                r_ssrf = await session.call_tool("submit_public_observation", {
                                    "kind": "official_source_link",
                                    "logical_contributor": "chatgpt-research",
                                    "source_url": "https://169.254.169.254/latest/meta-data/",
                                    "claim_summary": "IMDS SSRF attempt",
                                    "idempotency_key": "mcp-ssrf-002",
                                })
                                self.assertTrue(r_ssrf.isError)
                                self.assertEqual(r_ssrf.structuredContent["error"]["code"], "ssrf_refused")

                                r_vac = await session.call_tool("submit_public_vacancy", {
                                    "logical_contributor": "chatgpt-research",
                                    "company": "Google DeepMind",
                                    "title": "Software Engineer, Graduate 2027",
                                    "posting_url": "https://www.google.com/about/careers/applications/jobs/results/12345",
                                    "location_text": "London, UK",
                                    "country": "GB",
                                    "countries": ["GB"],
                                    "description_text": "Full graduate software engineering description in London.",
                                    "idempotency_key": "mcp-vac-003",
                                })
                                self.assertFalse(r_vac.isError)
                                self.assertEqual(r_vac.structuredContent["data"]["screening_status"], "unscreened_agent_submission")

                                r_budget = await session.call_tool("submit_public_observation", {
                                    "kind": "official_source_link",
                                    "logical_contributor": "chatgpt-research",
                                    "source_url": "https://www.revolut.com/careers/",
                                    "claim_summary": "Exceeds hourly budget",
                                    "idempotency_key": "mcp-budget-004",
                                })
                                self.assertTrue(r_budget.isError)
                                self.assertEqual(r_budget.structuredContent["error"]["code"], "budget_exceeded")

                try:
                    asyncio.run(exercise_mcp())
                finally:
                    srv.should_exit = True
                    thread.join(timeout=5)
        finally:
            subprocess.run(["docker", "exec", container, "dropdb", "-U", "postgres", "--if-exists", "--force", db_name], check=False)


if __name__ == "__main__":
    unittest.main()
