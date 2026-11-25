"""Disposable PostgreSQL 17 qualification tests for discovery fidelity (0011) and hardened agent evidence (0012)."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import os
from pathlib import Path
import subprocess
import unittest

import psycopg
from psycopg.conninfo import make_conninfo

from jobwatch.postgres.agent_evidence import (
    AgentEvidenceIngestor,
    EvidenceValidationError,
    IdempotencyConflictError,
    ObservationSubmission,
    SafeFetchResult,
    corroborate_observation,
)
from jobwatch.postgres.connection import connect
from jobwatch.postgres.migrate import migrate
from support import DatabaseTest, actor

ROOT = Path(__file__).resolve().parents[2]
FIDELITY_SQL = ROOT / "sql/postgresql/proposals/0011_discovery_evidence_fidelity.sql"
EVIDENCE_SQL = ROOT / "sql/postgresql/proposals/0012_agent_evidence_hardened.sql"
ROLES_SQL = ROOT / "sql/postgresql/proposals/agent_evidence_roles.sql"


class HardenedAgentEvidenceAndFidelityPgTests(DatabaseTest):
    def test_fidelity_security_definer_concurrency_atomicity_and_6state_corroboration(self):
        container = os.environ["JOBWATCH_PG_TEST_CONTAINER"]
        db = "jobwatch_agent_evidence_hardened_test"
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
            with connect(cfg) as mig:
                migrate(mig)
                mig.execute(FIDELITY_SQL.read_text(encoding="utf-8"))
                mig.execute(EVIDENCE_SQL.read_text(encoding="utf-8"))
                mig.commit()
            admin(ROLES_SQL.read_text(encoding="utf-8"))
            admin(f"GRANT CONNECT ON DATABASE {db} TO jobwatch_agent_evidence; "
                  f"ALTER ROLE jobwatch_agent_evidence IN DATABASE {db} SET search_path=jobwatch,pg_catalog;")

            owner_cfg = replace(cfg, dsn=make_conninfo(cfg.dsn, user="jobwatch_owner"), purpose="runtime")
            col_cfg = replace(cfg, dsn=make_conninfo(cfg.dsn, user="jobwatch_collector"), purpose="runtime")
            ev_cfg = replace(cfg, dsn=make_conninfo(cfg.dsn, user="jobwatch_agent_evidence"), purpose="runtime")

            with connect(owner_cfg) as own, own.transaction():
                actor(own)
                emp = own.execute("INSERT INTO jobwatch.employers(slug,name) VALUES ('acme-uk','Acme UK') RETURNING id").fetchone()["id"]
                sid = own.execute(
                    "INSERT INTO jobwatch.sources(provider,board,region,employer_id,status,validation_url,endpoint_checked_at,mapping_verified_at,mapping_verified_by,mapping_evidence) "
                    "VALUES ('greenhouse','acmeuk','global',%s,'monitored','https://acme.co.uk/careers',now(),now(),'owner','ev') RETURNING id",
                    (emp,),
                ).fetchone()["id"]
            with connect(col_cfg) as col, col.transaction():
                col.execute("SELECT set_config('jobwatch.actor', 'collector:test', true)")
                pkey = "greenhouse:acmeuk:42"
                vid = "11111111-1111-4111-8111-111111111111"
                col.execute(
                    "INSERT INTO jobwatch.postings(posting_key,source_id,provider,board,native_id,status,first_seen_at,first_seen_basis,"
                    "current_version_id,verdict,family,codes,rules_version,screening_input_sha256,screening_basis,screened_at) "
                    "VALUES (%s,%s,'greenhouse','acmeuk','42','listed',now(),'observed',%s,'priority','software',ARRAY['location.uk'],'screen-2026-11-25.1',repeat('a',64),'full',now())",
                    (pkey, sid, vid),
                )
                col.execute(
                    "INSERT INTO jobwatch.posting_versions(id,posting_key,source_id,origin,evidence,observed_at,normalizer_version,normalized_sha256,"
                    "company,title,posting_url,location_text,description_text) "
                    "VALUES (%s,%s,%s,'git_history',E'\\\\x7b7d',now(),'normalizer-v2',repeat('b',64),'Acme UK','Graduate SWE','https://job-boards.greenhouse.io/acmeuk/jobs/42','London, UK','Full native description')",
                    (vid, pkey, sid),
                )

            ingestor = AgentEvidenceIngestor(resolve_dns=False)
            now = datetime.now(timezone.utc)

            # 1. Complete 12,000-char description retained WITHOUT truncation + distinct complete vs excerpt hashes + snapshot history!
            long_desc = ("Build distributed ML systems in Python and Rust in London. " * 210) + "Application deadline: 29 November 2026. Right to work in the UK required."
            self.assertGreater(len(long_desc), 10000)
            with connect(ev_cfg) as ev_conn:
                vac = ingestor.record_new_vacancy(
                    ev_conn,
                    logical_contributor="local-cli:session-a",
                    claimed_actor="assistant-claimed-name",
                    observed_at=now - timedelta(minutes=5),
                    company="Bright AI UK",
                    title="Graduate Machine Learning Engineer 2027",
                    posting_url="https://brightai.co.uk/careers/grad-ml-2027",
                    location_text="London, United Kingdom",
                    country="GB",
                    description_text=long_desc,
                    evidence_completeness="complete",
                )
                self.assertEqual(vac["status"], "recorded")
                self.assertEqual(vac["overall_state"], "submission_recorded")
                self.assertEqual(vac["complete_description_sha256"], sha256(long_desc.encode()).hexdigest())
                self.assertNotEqual(vac["complete_description_sha256"], vac["excerpt_sha256"])

                # Excluded verdict STILL retains complete description & historical snapshot!
                excl_vac = ingestor.record_new_vacancy(
                    ev_conn,
                    logical_contributor="local-cli:session-a",
                    observed_at=now - timedelta(minutes=4),
                    company="Bright AI UK",
                    title="Senior Staff Sales Director",
                    posting_url="https://brightai.co.uk/careers/senior-sales",
                    location_text="London, United Kingdom",
                    country="GB",
                    description_text="Complete senior sales description preserved independently of verdict.",
                    evidence_completeness="complete",
                )
                self.assertEqual(excl_vac["verdict"], "excluded")

                # Verify ordinary SQL inspectability via research_discovery_evidence
                row = ev_conn.execute(
                    "SELECT effective_completeness, char_length(effective_description) AS dlen, "
                    "complete_description_sha256, excerpt_sha256, snapshot_count "
                    "FROM jobwatch.research_discovery_evidence WHERE candidate_key=%s",
                    (vac["candidate_key"],),
                ).fetchone()
                self.assertEqual(row["effective_completeness"], "complete")
                self.assertEqual(row["dlen"], len(long_desc))
                self.assertEqual(row["snapshot_count"], 1)

                # 2. Zero raw table INSERT/UPDATE/DELETE bypass for jobwatch_agent_evidence!
                for stmt in (
                    "INSERT INTO jobwatch.agent_evidence_observations(idempotency_key,submission_sha256,kind,db_login,logical_contributor,observed_at,source_url,claim_summary,payload_sha256) VALUES ('bypass1234',repeat('a',64),'official_source_link','jobwatch_owner','x',now(),'https://acme.co.uk','x',repeat('b',64))",
                    "INSERT INTO jobwatch.discovery_candidates(candidate_key,provider,company,title,posting_url,evidence_sha256,discovery_mechanisms,verdict,family,codes,rules_version,screening_basis) VALUES ('agent:bypass','other','X','T','https://acme.co.uk',repeat('a',64),ARRAY['x'],'review','other',ARRAY[]::text[],'v','full')",
                    "INSERT INTO jobwatch.agent_evidence_corroborations(observation_id,checked_at,checked_by,retrieval_status,wording_status,claim_status,overall_state) VALUES (gen_random_uuid(),now(),'spoof','retrieved_200','matched','corroborated','claim_corroborated')",
                    "UPDATE jobwatch.agent_evidence_observations SET claim_summary='tampered'",
                    "DELETE FROM jobwatch.agent_evidence_observations",
                    "INSERT INTO jobwatch.verifications(posting_key,kind,checked_at,checked_by,url,value) VALUES ('greenhouse:acmeuk:42','deadline',now(),'agent','https://acme.co.uk','2026-11-29')",
                    "INSERT INTO jobwatch.calendar_selections(posting_key,mode,recorded_by) VALUES ('greenhouse:acmeuk:42','include','agent')",
                    "UPDATE jobwatch.postings SET verdict='excluded'",
                ):
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege, msg=f"Must deny: {stmt}"):
                        with ev_conn.transaction():
                            ev_conn.execute(stmt)

                # 3. Attribution separation + idempotency collision refusal + genuine repeat observation preservation!
                d1 = ingestor.record_observation(ev_conn, ObservationSubmission(
                    kind="deadline_wording",
                    logical_contributor="local-cli:session-a",
                    claimed_actor="untrusted-agent-label",
                    observed_at=now - timedelta(minutes=3),
                    source_url="https://acme.co.uk/careers/grad-swe",
                    posting_key=pkey,
                    claim_summary="Observed closing date on careers page",
                    observed_wording="Applications close 29 November 2026.",
                    idempotency_key="fixed-idem-key-001",
                ))
                self.assertEqual(d1["status"], "recorded")
                # Exact replay succeeds
                d1_rep = ingestor.record_observation(ev_conn, ObservationSubmission(
                    kind="deadline_wording",
                    logical_contributor="local-cli:session-a",
                    claimed_actor="untrusted-agent-label",
                    observed_at=now - timedelta(minutes=3),
                    source_url="https://acme.co.uk/careers/grad-swe",
                    posting_key=pkey,
                    claim_summary="Observed closing date on careers page",
                    observed_wording="Applications close 29 November 2026.",
                    idempotency_key="fixed-idem-key-001",
                ))
                self.assertEqual(d1_rep["status"], "duplicate_replayed")
                # Same idempotency_key with DIFFERENT wording MUST FAIL loudly!
                with self.assertRaises(IdempotencyConflictError):
                    ingestor.record_observation(ev_conn, ObservationSubmission(
                        kind="deadline_wording",
                        logical_contributor="local-cli:session-a",
                        observed_at=now - timedelta(minutes=2),
                        source_url="https://acme.co.uk/careers/grad-swe",
                        posting_key=pkey,
                        claim_summary="Observed closing date on careers page",
                        observed_wording="Applications close 15 December 2026.",
                        idempotency_key="fixed-idem-key-001",
                    ))
                # Genuine repeat observation at a later time (auto idempotency key includes observed_at) creates a NEW row!
                d1_later = ingestor.record_observation(ev_conn, ObservationSubmission(
                    kind="deadline_wording",
                    logical_contributor="local-cli:session-a",
                    observed_at=now - timedelta(minutes=1),
                    source_url="https://acme.co.uk/careers/grad-swe",
                    posting_key=pkey,
                    claim_summary="Re-observed closing date later",
                    observed_wording="Applications close 29 November 2026.",
                ))
                self.assertEqual(d1_later["status"], "recorded")
                self.assertNotEqual(d1_later["observation_id"], d1["observation_id"])

                # Semantic date validation enforced inside PostgreSQL SECURITY DEFINER function too!
                with self.assertRaises(psycopg.errors.DatetimeFieldOverflow):
                    with ev_conn.transaction():
                        ev_conn.execute(
                            "SELECT * FROM jobwatch.submit_agent_observation("
                            "'bad-date-key-1', repeat('a',64), 'deadline_wording', 'cli:1', '', now(), "
                            "'https://acme.co.uk/jobs', 'Invalid date test', 'Apply by 30 Feb 2026', '2026-02-30', "
                            "%s, NULL, NULL, NULL, NULL, '{}'::jsonb, repeat('b',64))",
                            (pkey,),
                        )

                # Verify attribution columns in research_agent_evidence
                attr = ev_conn.execute(
                    "SELECT db_login, logical_contributor, claimed_actor, overall_state "
                    "FROM jobwatch.research_agent_evidence WHERE observation_id=%s",
                    (d1["observation_id"],),
                ).fetchone()
                self.assertEqual(attr["db_login"], "jobwatch_agent_evidence")
                self.assertEqual(attr["logical_contributor"], "local-cli:session-a")
                self.assertEqual(attr["claimed_actor"], "untrusted-agent-label")
                self.assertEqual(attr["overall_state"], "submission_recorded")

                # Atomicity: invalid vacancy leaves ZERO orphan rows!
                before_cands = ev_conn.execute("SELECT count(*) AS n FROM jobwatch.research_discovery_evidence").fetchone()["n"]
                with self.assertRaises(EvidenceValidationError):
                    ingestor.record_new_vacancy(
                        ev_conn, logical_contributor="local-cli:session-a",
                        observed_at=now + timedelta(hours=1),  # future rejected
                        company="Ghost Co", title="Grad SWE", posting_url="https://ghost.co.uk/jobs/1",
                        description_text="Should not persist.",
                    )
                after_cands = ev_conn.execute("SELECT count(*) AS n FROM jobwatch.research_discovery_evidence").fetchone()["n"]
                self.assertEqual(before_cands, after_cands)

            # 4. Concurrent transactional rate-budget enforcement keyed on db_login (NOT spoofing contributor names!)
            with connect(owner_cfg) as own, own.transaction():
                cur_cnt = own.execute(
                    "SELECT count(*) AS n FROM jobwatch.agent_evidence_observations WHERE db_login='jobwatch_agent_evidence'"
                ).fetchone()["n"]
                # Allow strictly 2 more submissions across ALL concurrent threads regardless of contributor names!
                own.execute(
                    "INSERT INTO jobwatch.agent_evidence_budgets(db_login, hourly_limit, daily_limit) "
                    "VALUES ('jobwatch_agent_evidence', %s, 100) "
                    "ON CONFLICT (db_login) DO UPDATE SET hourly_limit = EXCLUDED.hourly_limit",
                    (cur_cnt + 2,),
                )

            def submit_worker(i: int) -> str:
                try:
                    with connect(ev_cfg) as c:
                        res = ingestor.record_observation(c, ObservationSubmission(
                            kind="official_source_link",
                            logical_contributor=f"spoofed-contributor-{i}",
                            claimed_actor=f"spoofed-actor-{i}",
                            observed_at=now,
                            source_url=f"https://acme.co.uk/careers/page-{i}",
                            source_id=sid,
                            claim_summary=f"Concurrent link {i}",
                        ))
                        return res["status"]
                except EvidenceValidationError:
                    return "budget_blocked"

            with ThreadPoolExecutor(max_workers=6) as pool:
                outcomes = list(pool.map(submit_worker, range(6)))
            self.assertEqual(outcomes.count("recorded"), 2, outcomes)
            self.assertEqual(outcomes.count("budget_blocked"), 4, outcomes)

            # 5. Append-only 6-state corroboration history (preserves every attempt, distinguishes all 6 states!)
            with connect(col_cfg) as col_conn:
                # Attempt 1: HTTP 403 -> evidence_unavailable (NOT contradiction!)
                c1 = corroborate_observation(
                    col_conn, d1["observation_id"],
                    fetcher=lambda u, budget=None: SafeFetchResult(403, b"Forbidden", u, "93.184.216.34", False, 0),
                )
                self.assertEqual(c1["overall_state"], "evidence_unavailable")
                self.assertEqual(c1["retrieval_status"], "blocked_or_403")

                # Attempt 2: Truncated 200 response missing quote -> evidence_unavailable (NOT contradiction!)
                c2 = corroborate_observation(
                    col_conn, d1["observation_id"],
                    fetcher=lambda u, budget=None: SafeFetchResult(200, b"<html>Header only...</html>", u, "93.184.216.34", True, 0),
                )
                self.assertEqual(c2["overall_state"], "evidence_unavailable")
                self.assertEqual(c2["retrieval_status"], "truncated_before_match")

                # Attempt 3: HTTP 200 without quote and without contradictory date -> source_retrieved (NOT corroboration AND NOT contradiction!)
                c3 = corroborate_observation(
                    col_conn, d1["observation_id"],
                    fetcher=lambda u, budget=None: SafeFetchResult(200, b"<html><body>Welcome to Acme UK careers portal.</body></html>", u, "93.184.216.34", False, 0),
                )
                self.assertEqual(c3["overall_state"], "source_retrieved")

                # Attempt 4: HTTP 200 with EXPLICIT DIFFERENT DATE -> contradictory_evidence!
                c4 = corroborate_observation(
                    col_conn, d1["observation_id"],
                    fetcher=lambda u, budget=None: SafeFetchResult(200, b"<html><body>Application deadline: 15 December 2026.</body></html>", u, "93.184.216.34", False, 0),
                )
                self.assertEqual(c4["overall_state"], "contradictory_evidence")

                # Attempt 5: HTTP 200 with exact quote AND matching explicit date -> claim_corroborated!
                c5 = corroborate_observation(
                    col_conn, d1["observation_id"],
                    fetcher=lambda u, budget=None: SafeFetchResult(200, b"<html><body><p>Applications close 29 November 2026.</p></body></html>", u, "93.184.216.34", False, 0),
                )
                self.assertEqual(c5["overall_state"], "claim_corroborated")

                # All 5 prior corroboration attempts are preserved append-only!
                hist = col_conn.execute(
                    "SELECT overall_state FROM jobwatch.research_agent_corroborations "
                    "WHERE observation_id=%s ORDER BY recorded_at ASC",
                    (d1["observation_id"],),
                ).fetchall()
                self.assertEqual(
                    [r["overall_state"] for r in hist],
                    ["evidence_unavailable", "evidence_unavailable", "source_retrieved", "contradictory_evidence", "claim_corroborated"],
                )
                # Wording matched without explicit date interpretation -> wording_matched ONLY!
                with connect(owner_cfg) as own_conn, own_conn.transaction():
                    obs_vague = own_conn.execute(
                        "SELECT * FROM jobwatch.submit_agent_observation("
                        "'vague-dl-001', repeat('c',64), 'deadline_wording', 'cli:1', '', now(), "
                        "'https://acme.co.uk/grad', 'Vague deadline wording', 'Applications close soon at noon.', NULL, "
                        "%s, NULL, NULL, NULL, NULL, '{}'::jsonb, repeat('d',64))",
                        (pkey,),
                    ).fetchone()
                c_vague = corroborate_observation(
                    col_conn, str(obs_vague["observation_id"]),
                    fetcher=lambda u, budget=None: SafeFetchResult(200, b"<html>Applications close soon at noon.</html>", u, "93.184.216.34", False, 0),
                )
                self.assertEqual(c_vague["overall_state"], "wording_matched")
                self.assertEqual(c_vague["claim_status"], "unverified")
        finally:
            subprocess.run(["docker", "exec", container, "dropdb", "-U", "postgres", "--force", db], check=False)


if __name__ == "__main__":
    unittest.main()
