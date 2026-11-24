"""Disposable PostgreSQL 17 integration tests for recurring directory discovery (0010)."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import unittest

from jobwatch.discovery.directory_discovery import DirectoryBoard
from jobwatch.postgres.connection import connect
from jobwatch.postgres.directory_discovery import (
    run_discovery_cycle,
    select_probe_batch,
    sync_directory_boards,
)
from jobwatch.postgres.migrate import migrate
from jobwatch.postgres.read_tools import ReadTools
from support import DatabaseTest, ENABLED, Target, actor

ROOT = Path(__file__).resolve().parents[2]


class DirectoryDiscoveryPgTests(DatabaseTest):
    def test_sync_deduplicates_sources_and_collector_runs_without_owner_writes(self):
        with self.target.connect("jobwatch_owner") as conn, conn.transaction():
            actor(conn)
            conn.execute(
                "INSERT INTO jobwatch.sources(provider, board, region, employer_id, status, "
                "validation_url, endpoint_checked_at, mapping_verified_at, mapping_verified_by, mapping_evidence) "
                "VALUES ('greenhouse', 'monzo', 'global', %s, 'monitored', 'https://monzo.com/careers', "
                "now(), now(), 'owner', 'verified')",
                (self.employer,),
            )
            audit_before = conn.execute("SELECT count(*) AS n FROM jobwatch.owner_audit").fetchone()["n"]
            sources_before = conn.execute("SELECT count(*) AS n FROM jobwatch.sources").fetchone()["n"]

        boards = [
            DirectoryBoard("greenhouse", "monzo", "Monzo", True, "ats-scrapers:greenhouse", "https://example.org/gh.csv"),
            DirectoryBoard("greenhouse", "ukstartup", "UK Startup Ltd", True, "ats-scrapers:greenhouse", "https://example.org/gh.csv"),
            DirectoryBoard("ashby", "newai.co.uk", "NewAI UK", True, "ats-scrapers:ashby", "https://example.org/ashby.csv"),
        ]

        # Collector role records public directory observations with NO jobwatch.actor / owner_audit.
        with self.target.connect("jobwatch_collector") as conn:
            synced = sync_directory_boards(
                conn,
                boards,
                directory_source="ats-scrapers:test",
                provenance={"sha256": "a" * 64},
            )
            self.assertEqual(synced["inserted"], 3)
            self.assertEqual(synced["already_registered"], 1)
            self.assertEqual(synced["unknown_boards"], 2)

            # Rotating probe batch must skip already-monitored monzo.
            batch = select_probe_batch(conn, limit=10)
            self.assertEqual({r["board_key"] for r in batch}, {"greenhouse:ukstartup", "ashby:newai.co.uk"})

            def fake_get(url, **_kwargs):
                if "boards-api.greenhouse.io/v1/boards/ukstartup" in url:
                    return 200, json.dumps({
                        "jobs": [{
                            "id": 9001,
                            "internal_job_id": 1,
                            "title": "Graduate Software Engineer 2027",
                            "absolute_url": "https://job-boards.greenhouse.io/ukstartup/jobs/9001",
                            "location": {"name": "London, UK"},
                            "content": "Graduate Python and C++ engineering role in London.",
                        }]
                    }).encode(), url
                if "job-boards.greenhouse.io/ukstartup" in url:
                    return 200, b'<a href="https://ukstartup.co.uk">Home</a>', url
                if "ukstartup.co.uk/careers" in url:
                    return 200, b'<a href="https://job-boards.greenhouse.io/ukstartup">Jobs</a>', url
                if "ukstartup.co.uk" in url:
                    return 200, b"<html>UK Startup</html>", url
                if "api.ashbyhq.com/posting-api/job-board/newai.co.uk" in url:
                    return 200, json.dumps({
                        "organizationName": "NewAI UK",
                        "jobs": [{
                            "id": "ai-uuid-1",
                            "title": "Senior US Sales Manager",
                            "jobUrl": "https://jobs.ashbyhq.com/newai.co.uk/ai-uuid-1",
                            "location": "New York, USA",
                            "address": {"postalAddress": {"addressCountry": "US"}},
                            "isListed": True,
                            "descriptionPlain": "US sales role.",
                        }],
                    }).encode(), url
                return 404, b"", url

            summary = run_discovery_cycle(
                conn,
                refresh_directories=False,
                max_boards=10,
                max_requests=20,
                delay_s=0.0,
                get_bytes=fake_get,
                pause=lambda _: None,
            )
            self.assertEqual(summary["boards_probed"], 2)
            self.assertEqual(summary["status_counts"], {"active_uk_relevant": 1, "active_non_uk": 1})
            self.assertEqual(summary["priority_vacancies"], 1)
            self.assertEqual(summary["candidates_inserted"], 1)
            self.assertEqual(summary["identity_checks"], {"verified_official_link": 1})

            # Immediate re-run respects cooldowns and selects 0 boards.
            self.assertEqual(select_probe_batch(conn, limit=10), [])
            # After 8 days, active_uk_relevant re-enters queue while active_non_uk stays in 30-day cooldown.
            future = datetime.now(timezone.utc) + timedelta(days=8)
            next_batch = select_probe_batch(conn, limit=10, now=future)
            self.assertEqual([r["board_key"] for r in next_batch], ["greenhouse:ukstartup"])

            # Verify zero sources were activated and owner_audit is untouched.
            row = conn.execute("SELECT * FROM jobwatch.research_discovered_boards WHERE board_key='greenhouse:ukstartup'").fetchone()
            self.assertTrue(row["unknown_employer_board"])
            self.assertEqual(row["identity_status"], "verified_official_link")
            self.assertEqual(row["active_candidate_vacancies"], 1)
            self.assertEqual(row["active_priority_candidates"], 1)

        with self.target.connect("jobwatch_owner") as conn:
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM jobwatch.sources").fetchone()["n"], sources_before)
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM jobwatch.owner_audit").fetchone()["n"], audit_before)

    def test_research_mcp_read_only_visibility_and_mutation_refusal(self):
        from psycopg.conninfo import make_conninfo
        container = os.environ["JOBWATCH_PG_TEST_CONTAINER"]
        db = "jobwatch_dirdisc_read_test"
        subprocess.run(["docker", "exec", container, "createdb", "-U", "postgres", "-O", "postgres", db], check=True)
        try:
            def admin(sql):
                p = subprocess.run(
                    ["docker", "exec", "-i", container, "psql", "-X", "-U", "postgres", "-d", db, "-v", "ON_ERROR_STOP=1", "-f", "-"],
                    input=sql, text=True, capture_output=True,
                )
                self.assertEqual(p.returncode, 0, p.stderr)

            admin(f"""
                GRANT CONNECT, CREATE ON DATABASE {db} TO jobwatch_migrator;
                GRANT CONNECT ON DATABASE {db} TO jobwatch_owner, jobwatch_collector;
                ALTER ROLE jobwatch_migrator IN DATABASE {db} SET search_path=jobwatch,pg_catalog;
                ALTER ROLE jobwatch_owner IN DATABASE {db} SET search_path=jobwatch,pg_catalog;
                ALTER ROLE jobwatch_collector IN DATABASE {db} SET search_path=jobwatch,pg_catalog;
            """)
            cfg = replace(self.target.config(), dsn=make_conninfo(self.target.config().dsn, dbname=db))
            with connect(cfg) as c:
                migrate(c)
                has_agent = c.execute("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='jobwatch_agent_readonly') AS ok").fetchone()["ok"]
            if not has_agent:
                admin((ROOT / "sql/postgresql/agent_roles.sql").read_text())
            admin((ROOT / "sql/postgresql/agent_access.sql").read_text())
            admin((ROOT / "sql/postgresql/discovery_access.sql").read_text())
            admin((ROOT / "sql/postgresql/directory_discovery_access.sql").read_text())
            col_cfg = replace(cfg, dsn=make_conninfo(cfg.dsn, user="jobwatch_collector"), purpose="runtime")
            with connect(col_cfg) as c:
                sync_directory_boards(
                    c,
                    [DirectoryBoard("ashby", "ukexample", "UK Example", True, "ats-scrapers:ashby", "https://example.org/a.csv")],
                    directory_source="ats-scrapers:ashby",
                    provenance={"sha256": "b" * 64},
                )
            read_cfg = replace(cfg, dsn=make_conninfo(cfg.dsn, user="jobwatch_agent_readonly"), purpose="runtime")
            tools = ReadTools(read_cfg, shared=True)
            ok = tools.call("sql_query", {
                "sql": "SELECT board_key, unknown_employer_board FROM jobwatch.research_discovered_boards ORDER BY board_key",
                "limit": 10,
            })
            self.assertTrue(ok["ok"], ok)
            self.assertEqual(ok["data"]["rows"], [["ashby:ukexample", True]])
            refused = tools.call("sql_query", {
                "sql": "DELETE FROM jobwatch.discovered_boards RETURNING *",
                "limit": 1,
            })
            self.assertFalse(refused["ok"])
        finally:
            subprocess.run(["docker", "exec", container, "dropdb", "-U", "postgres", "--force", db], check=False)


if __name__ == "__main__":
    unittest.main()
