"""Disposable PostgreSQL 17 tests for the isolated automatic source activation prototype."""
from datetime import datetime, timedelta, timezone
import unittest
from uuid import uuid4

from jobwatch.discovery.directory_discovery import DirectoryBoard, IdentityCheckResult, probe_ats_board
from jobwatch.postgres.auto_activation import (
    AUTO_DISCOVERY_PREFIX,
    AutoActivationPolicy,
    evaluate_board_eligibility,
    evaluate_deactivation_candidates,
    run_auto_activation_pass,
)
from jobwatch.postgres.directory_discovery import record_board_probe, sync_directory_boards
from jobwatch.postgres.publisher import Collector
from support import DatabaseTest, actor


class AutoActivationPrototypeTests(DatabaseTest):
    def _seed_board(self, conn, provider, board, company, *, uk_prio=1, total=5, custom_hosts=0, id_status="verified_official_link", careers_url="https://example.co.uk/careers", evidence="Official site links board"):
        sync_directory_boards(
            conn,
            [DirectoryBoard(provider, board, company, True, "ats-scrapers:test", "https://example.org/dir.csv")],
            directory_source="ats-scrapers:test",
            provenance={"sha256": "a" * 64},
        )
        conn.execute(
            "UPDATE jobwatch.discovered_boards SET probe_status='active_uk_relevant', http_status=200, "
            "total_vacancies=%s, uk_vacancies=%s, priority_vacancies=%s, review_vacancies=0, "
            "custom_host_urls=%s, identity_status=%s, identity_checked_at=now(), "
            "careers_url=%s, official_website='https://example.co.uk', identity_evidence=%s, last_probed_at=now() "
            "WHERE board_key=%s",
            (total, uk_prio, uk_prio, custom_hosts, id_status, careers_url, evidence, f"{provider}:{board.lower()}"),
        )

    def test_gates_reject_ambiguous_identity_unreviewed_custom_host_and_resource_caps(self):
        with self.target.connect("jobwatch_collector") as conn:
            self._seed_board(conn, "greenhouse", "gooduk", "Good UK Ltd", uk_prio=1, total=20)
            self._seed_board(conn, "greenhouse", "unverifieduk", "Unverified UK", id_status="hosted_link_only")
            self._seed_board(conn, "greenhouse", "customuk", "Custom Host UK", custom_hosts=5)
            self._seed_board(conn, "greenhouse", "giantco", "Giant Global", total=1200)

        collector = Collector(self.target.config("jobwatch_collector"), actor="collector:auto-test")
        policy = AutoActivationPolicy(max_monitored_boards=10, max_total_listed_postings=1000, max_single_board_vacancies=500)

        def fake_good_gh(_url):
            return {
                "jobs": [{
                    "id": 101, "internal_job_id": 1, "title": "Graduate Software Engineer 2027",
                    "absolute_url": "https://job-boards.greenhouse.io/gooduk/jobs/101",
                    "location": {"name": "London, UK"}, "content": "2027 graduate Python role.",
                }],
                "meta": {"total": 1},
            }

        with self.target.connect("jobwatch_owner") as owner:
            dry = run_auto_activation_pass(owner, collector, policy=policy, dry_run=True)
            self.assertEqual(dry["eligible"], ["greenhouse:gooduk"])
            deferred = {d["board_key"]: d["reasons"] for d in dry["deferred"]}
            self.assertIn("identity_not_verified_official", deferred["greenhouse:unverifieduk"])
            self.assertIn("unreviewed_custom_url_host", deferred["greenhouse:customuk"])
            self.assertIn("exceeds_single_board_vacancy_cap", deferred["greenhouse:giantco"])
            # Dry run makes zero source or audit writes.
            self.assertEqual(owner.execute("SELECT count(*) AS n FROM jobwatch.sources WHERE board='gooduk'").fetchone()["n"], 0)

            live = run_auto_activation_pass(
                owner, collector, policy=policy, dry_run=False, get_json=fake_good_gh, fixture=True
            )
            self.assertEqual(len(live["activated"]), 1)
            self.assertEqual(live["activated"][0]["board_key"], "greenhouse:gooduk")
            src = owner.execute("SELECT status, discovered_by, mapping_verified_by FROM jobwatch.sources WHERE board='gooduk'").fetchone()
            self.assertEqual(src["status"], "monitored")
            self.assertTrue(src["discovered_by"].startswith(AUTO_DISCOVERY_PREFIX))
            self.assertEqual(src["mapping_verified_by"], policy.actor)

    def test_deactivation_flags_only_auto_activated_failing_or_stale_sources(self):
        old_time = datetime.now(timezone.utc) - timedelta(days=100)
        with self.target.connect("jobwatch_owner") as owner, owner.transaction():
            actor(owner)
            manual_sid = owner.execute(
                "INSERT INTO jobwatch.sources(provider,board,region,employer_id,status,validation_url,"
                "endpoint_checked_at,mapping_verified_at,mapping_verified_by,mapping_evidence,discovered_by,discovered_at) "
                "VALUES ('greenhouse','manualco','global',%s,'monitored','https://example.org',now(),now(),'owner','ev','manual',%s) RETURNING id",
                (self.employer, old_time),
            ).fetchone()["id"]
            auto_sid = owner.execute(
                "INSERT INTO jobwatch.sources(provider,board,region,employer_id,status,validation_url,"
                "endpoint_checked_at,mapping_verified_at,mapping_verified_by,mapping_evidence,discovered_by,discovered_at) "
                "VALUES ('greenhouse','autofail','global',%s,'monitored','https://example.org',now(),now(),'policy','ev',%s,%s) RETURNING id",
                (self.employer, AUTO_DISCOVERY_PREFIX + "test", old_time),
            ).fetchone()["id"]
        with self.target.connect("jobwatch_collector") as col, col.transaction():
            col.execute("SELECT set_config('jobwatch.actor', 'collector:test', true)")
            for sid in (manual_sid, auto_sid):
                for _ in range(5):
                    rid = uuid4()
                    col.execute(
                        "INSERT INTO jobwatch.collection_runs(id,source_id,mode,request_version,processing_revision) "
                        "VALUES (%s,%s,'publish','greenhouse:list-content-v1','pipeline-v1')",
                        (rid, sid),
                    )
                    col.execute(
                        "UPDATE jobwatch.collection_runs SET status='failed',finished_at=now(),error='collection_failed' WHERE id=%s",
                        (rid,),
                    )
        with self.target.connect("jobwatch_owner") as owner:
            flagged = evaluate_deactivation_candidates(owner)
            self.assertEqual(len(flagged), 1)
            self.assertEqual(flagged[0]["board_key"], "greenhouse:autofail")
            self.assertIn("consecutive_collection_failures", flagged[0]["reasons"])
            self.assertIn("zero_uk_relevant_yield_over_window", flagged[0]["reasons"])


if __name__ == "__main__":
    unittest.main()
