"""Disposable PostgreSQL 17 trust-boundary & direct-SQL security qualification for Stage B (0012).

Tests BOTH Python wrapper paths AND hostile direct SQL calls to the SECURITY DEFINER functions
as `jobwatch_agent_evidence`, including:
- `pg_temp` temporary-schema function shadowing attack (`pg_temp.sha256`, `pg_temp.clock_timestamp`, `pg_temp.hashtextextended`)
- Trusted SQL hash derivation (caller cannot forge `submission_sha256`/`payload_sha256`/`evidence_sha256`)
- Cross-login idempotency isolation (`UNIQUE (db_login, idempotency_key)`)
- Candidate identity consistency & anti-contamination of collector-discovered candidates
- Neutralization of untrusted screening verdicts (`unscreened-agent-submission` until collector rescreens)
- Direct SQL SSRF/URL, semantic date, completeness, and retention-policy enforcement
- Full PostgreSQL catalog audit of function ownership, `search_path`, `PUBLIC`, `NOINHERIT`, and table grants
- Target-vacancy binding in corroboration (unrelated page with identical wording CANNOT corroborate!)
"""
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

from jobwatch.postgres import discovery
from jobwatch.postgres.agent_evidence import (
    AgentEvidenceIngestor,
    CandidateIdentityConflictError,
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
EVIDENCE_SQL = ROOT / "sql/postgresql/proposals/0012_agent_evidence_hardened.sql"
ROLES_SQL = ROOT / "sql/postgresql/proposals/agent_evidence_roles.sql"


class HardenedAgentEvidenceDirectSqlSecurityTests(DatabaseTest):
    def test_direct_sql_trust_boundary_pg_temp_shadowing_grants_and_bound_corroboration(self):
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
                # 0001..0011 come from packaged migrations; 0012 stays isolated in sql/postgresql/proposals/.
                migrate(mig)
                mig.execute(EVIDENCE_SQL.read_text(encoding="utf-8"))
                mig.commit()
            admin(ROLES_SQL.read_text(encoding="utf-8"))
            admin(
                f"GRANT CONNECT ON DATABASE {db} TO jobwatch_agent_evidence; "
                f"ALTER ROLE jobwatch_agent_evidence IN DATABASE {db} SET search_path=jobwatch,pg_catalog;"
            )

            owner_cfg = replace(cfg, dsn=make_conninfo(cfg.dsn, user="jobwatch_owner"), purpose="runtime")
            col_cfg = replace(cfg, dsn=make_conninfo(cfg.dsn, user="jobwatch_collector"), purpose="runtime")
            ev_cfg = replace(cfg, dsn=make_conninfo(cfg.dsn, user="jobwatch_agent_evidence"), purpose="runtime")

            # Seed one monitored posting and one collector-discovered candidate for anti-contamination tests.
            with connect(owner_cfg) as own, own.transaction():
                actor(own)
                emp = own.execute("INSERT INTO jobwatch.employers(slug,name,careers_url) VALUES ('acme-uk','Acme UK','https://acme.co.uk/careers') RETURNING id").fetchone()["id"]
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
                    "VALUES (%s,%s,%s,'git_history',E'\\\\x7b7d',now(),'normalizer-v2',repeat('b',64),'Acme UK','Graduate Machine Learning Engineer','https://job-boards.greenhouse.io/acmeuk/jobs/42','London, UK','Full native description')",
                    (vid, pkey, sid),
                )
                # Collector-discovered candidate that an agent MUST NOT contaminate!
                col_desc = "Collector-discovered complete description for Beta UK."
                col_sha = sha256(col_desc.encode()).hexdigest()
                col.execute(
                    "INSERT INTO jobwatch.discovery_candidates("
                    "candidate_key,provider,board,native_id,company,title,posting_url,"
                    "description_text,description_excerpt,evidence_completeness,complete_description_sha256,excerpt_sha256,"
                    "evidence_sha256,state,discovery_mechanisms,verdict,family,codes,rules_version,screening_basis) "
                    "VALUES ('greenhouse:betauk:77','greenhouse','betauk','77','Beta UK','Graduate SWE',"
                    "'https://job-boards.greenhouse.io/betauk/jobs/77',%s,%s,'complete',%s,%s,repeat('c',64),"
                    "'verified_identity',ARRAY['ats_directory:gh'],'priority','software',ARRAY['location.uk'],'screen-2026-11-25.1','full')",
                    (col_desc, col_desc, col_sha, col_sha),
                )

            # 1. FULL POSTGRESQL SECURITY & GRANT AUDIT (ownership, search_path, PUBLIC, NOINHERIT, zero table writes)
            with connect(cfg) as audit_conn:
                role = audit_conn.execute(
                    "SELECT rolsuper, rolinherit, rolcreaterole, rolcreatedb, rolcanlogin, rolreplication, rolbypassrls "
                    "FROM pg_roles WHERE rolname='jobwatch_agent_evidence'"
                ).fetchone()
                self.assertEqual(dict(role), {
                    "rolsuper": False, "rolinherit": False, "rolcreaterole": False,
                    "rolcreatedb": False, "rolcanlogin": True, "rolreplication": False, "rolbypassrls": False,
                })
                memberships = audit_conn.execute(
                    "SELECT count(*) AS n FROM pg_auth_members WHERE pg_get_userbyid(member)='jobwatch_agent_evidence'"
                ).fetchone()["n"]
                self.assertEqual(memberships, 0)

                # Every 0012 function owned by non-superuser jobwatch_migrator, pg_temp LAST in search_path, ZERO PUBLIC execute!
                funcs = audit_conn.execute("""
                    SELECT p.proname, pg_get_userbyid(p.proowner) AS owner, p.prosecdef, p.proconfig,
                           has_function_privilege('public', p.oid, 'EXECUTE') AS pub_exec,
                           has_function_privilege('jobwatch_agent_evidence', p.oid, 'EXECUTE') AS ev_exec
                    FROM pg_proc p
                    JOIN pg_namespace n ON n.oid = p.pronamespace
                    WHERE n.nspname = 'jobwatch'
                      AND p.proname IN (
                        'parse_strict_iso_date', 'validate_agent_https_url',
                        'enforce_agent_evidence_budget', 'submit_agent_observation', 'submit_agent_vacancy'
                      )
                    ORDER BY p.proname
                """).fetchall()
                self.assertEqual(len(funcs), 5)
                for fn in funcs:
                    self.assertEqual(fn["owner"], "jobwatch_migrator")
                    self.assertFalse(fn["pub_exec"], f"PUBLIC must not execute {fn['proname']}")
                    self.assertIn("search_path=pg_catalog, jobwatch, pg_temp", fn["proconfig"] or [], fn)
                    if fn["proname"] in ("submit_agent_observation", "submit_agent_vacancy"):
                        self.assertTrue(fn["prosecdef"])
                        self.assertTrue(fn["ev_exec"])
                    else:
                        self.assertFalse(fn["ev_exec"])

                # ZERO INSERT/UPDATE/DELETE/TRUNCATE/TRIGGER/REFERENCES privileges on ANY table!
                bad_grants = audit_conn.execute("""
                    SELECT table_schema, table_name, privilege_type
                    FROM information_schema.role_table_grants
                    WHERE grantee = 'jobwatch_agent_evidence'
                      AND privilege_type <> 'SELECT'
                """).fetchall()
                self.assertEqual(bad_grants, [], f"Unexpected write grants: {bad_grants}")

            # 2. LIVE `pg_temp` TEMPORARY-SCHEMA SHADOWING ATTACK TEST (even with TEMP granted!)
            admin(f"GRANT TEMP ON DATABASE {db} TO jobwatch_agent_evidence;")
            with connect(ev_cfg) as ev_conn:
                with ev_conn.transaction():
                    ev_conn.execute("""
                        CREATE FUNCTION pg_temp.sha256(bytea) RETURNS bytea LANGUAGE sql AS $$ SELECT decode(repeat('f', 64), 'hex') $$;
                        CREATE FUNCTION pg_temp.clock_timestamp() RETURNS timestamptz LANGUAGE sql AS $$ SELECT '2099-01-01T00:00:00Z'::timestamptz $$;
                        CREATE FUNCTION pg_temp.hashtextextended(text, bigint) RETURNS bigint LANGUAGE sql AS $$ SELECT 42::bigint $$;
                    """)
                    # Confirm our hostile pg_temp functions exist in the session, then invoke SECURITY DEFINER directly!
                    r_shadow = ev_conn.execute(
                        "SELECT * FROM jobwatch.submit_agent_observation("
                        "'shadow-test-001', 'official_source_link', 'direct-sql:attacker', 'spoof', "
                        "now(), 'https://acme.co.uk/careers', 'Shadow test claim', '', NULL, "
                        "NULL, NULL, NULL, %s, NULL, '{\"probe\":1}'::jsonb)",
                        (sid,),
                    ).fetchone()
                # submission_sha256 MUST NOT be all 'f' (pg_temp.sha256 was bypassed!) and recorded_at MUST NOT be 2099!
                self.assertNotEqual(r_shadow["submission_sha256"], "f" * 64)
                self.assertLess(r_shadow["recorded_at"].year, 2090)

                # 3. DIRECT SQL HOSTILE CALLS TO SECURITY DEFINER FUNCTIONS
                # 3a. Direct SQL SSRF / private IP / IMDS / bad port / non-HTTPS refusal!
                for bad_url in (
                    "http://acme.co.uk/jobs",
                    "https://169.254.169.254/latest/meta-data/",
                    "https://10.0.0.5/internal",
                    "https://127.0.0.1/admin",
                    "https://[::1]/admin",
                    "https://[::ffff:169.254.169.254]/latest",
                    "https://localhost/jobs",
                    "https://acme.co.uk:8443/jobs",
                    "https://user:pass@acme.co.uk/jobs",
                ):
                    with self.subTest(bad_url=bad_url):
                        with self.assertRaises(psycopg.errors.InvalidParameterValue):
                            with ev_conn.transaction():
                                ev_conn.execute(
                                    "SELECT * FROM jobwatch.submit_agent_observation("
                                    "'ssrf-direct-01', 'official_source_link', 'cli:1', '', now(), %s, 'Bad URL')",
                                    (bad_url,),
                                )

                # 3b. Direct SQL semantic date refusal (`2026-02-30`)
                with self.assertRaises(psycopg.errors.DatetimeFieldOverflow):
                    with ev_conn.transaction():
                        ev_conn.execute(
                            "SELECT * FROM jobwatch.submit_agent_observation("
                            "'bad-date-direct', 'deadline_wording', 'cli:1', '', now(), "
                            "'https://acme.co.uk/jobs/42', 'Invalid date', 'Apply by 30 Feb 2026', '2026-02-30', %s)",
                            (pkey,),
                        )

                # 3c. Direct SQL idempotency conflict detection via SQL-computed submission_sha256!
                with ev_conn.transaction():
                    ev_conn.execute(
                        "SELECT * FROM jobwatch.submit_agent_observation("
                        "'shared-idem-001', 'official_source_link', 'cli:1', '', now(), "
                        "'https://acme.co.uk/page-a', 'First summary', '', NULL, NULL, NULL, NULL, %s, NULL, '{}'::jsonb)",
                        (sid,),
                    )
                with self.assertRaises(psycopg.errors.UniqueViolation):
                    with ev_conn.transaction():
                        ev_conn.execute(
                            "SELECT * FROM jobwatch.submit_agent_observation("
                            "'shared-idem-001', 'official_source_link', 'cli:1', '', now(), "
                            "'https://acme.co.uk/page-a', 'TAMPERED summary', '', NULL, NULL, NULL, NULL, %s, NULL, '{}'::jsonb)",
                            (sid,),
                        )

                # 3d. Direct SQL provider/board/URL mismatch refusal!
                with self.assertRaises(psycopg.errors.InvalidParameterValue):
                    with ev_conn.transaction():
                        ev_conn.execute(
                            "SELECT * FROM jobwatch.submit_agent_vacancy("
                            "'mismatch-gh-001', 'cli:1', '', now(), 'greenhouse', 'wrongboard', '999', "
                            "'RealCo', 'Grad SWE', 'https://job-boards.greenhouse.io/realboard/jobs/999', "
                            "'London', 'GB', ARRAY['GB'], 'complete', 'complete_public_ats', 'Full description')"
                        )

                # 3e. Direct SQL retention policy & completeness refusal!
                for bad_comp, bad_pol, bad_desc, bad_ex in (
                    ("complete", "complete_public_ats", None, "only excerpt"),
                    ("complete", "excerpt_only_terms_restricted", "full text", "ex"),
                    ("truncated", "metadata_only", None, "ex"),
                    ("unavailable", "complete_public_ats", "should fail", ""),
                ):
                    with self.subTest(bad=(bad_comp, bad_pol)):
                        with self.assertRaises(psycopg.errors.InvalidParameterValue):
                            with ev_conn.transaction():
                                ev_conn.execute(
                                    "SELECT * FROM jobwatch.submit_agent_vacancy("
                                    "'pol-fail-001', 'cli:1', '', now(), 'other', NULL, NULL, "
                                    "'Co', 'Grad SWE', 'https://co.co.uk/jobs/1', 'London', 'GB', ARRAY['GB'], %s, %s, %s, %s)",
                                    (bad_comp, bad_pol, bad_desc, bad_ex),
                                )

                # 3f. Anti-contamination: agent CANNOT overwrite or conflict with collector candidate `greenhouse:betauk:77`!
                # Provider/host spoofing on collector URL -> rejected immediately as InvalidParameterValue!
                with self.assertRaises(psycopg.errors.InvalidParameterValue):
                    with ev_conn.transaction():
                        ev_conn.execute(
                            "SELECT * FROM jobwatch.submit_agent_vacancy("
                            "'contam-spoof-1', 'cli:1', '', now(), 'other', NULL, NULL, "
                            "'SpoofCo', 'Hijacked Title', 'https://job-boards.greenhouse.io/betauk/jobs/77', "
                            "'London', 'GB', ARRAY['GB'], 'complete', 'complete_public_ats', 'Malicious overwrite attempt')"
                        )
                # Conflicting native_id on exact collector URL -> rejected as candidate_identity_conflict (UniqueViolation)!
                with self.assertRaises(psycopg.errors.UniqueViolation):
                    with ev_conn.transaction():
                        ev_conn.execute(
                            "SELECT * FROM jobwatch.submit_agent_vacancy("
                            "'contam-conflict-1', 'cli:1', '', now(), 'greenhouse', 'betauk', '88', "
                            "'Beta UK', 'Hijacked Title', 'https://job-boards.greenhouse.io/betauk/jobs/77', "
                            "'London', 'GB', ARRAY['GB'], 'complete', 'complete_public_ats', 'Malicious overwrite attempt')"
                        )
                # Consistent re-observation of collector candidate records ONLY an observation — ZERO mutation to candidate or snapshots!
                with ev_conn.transaction():
                    r_exist = ev_conn.execute(
                        "SELECT * FROM jobwatch.submit_agent_vacancy("
                        "'contam-consistent-1', 'cli:1', '', now(), 'greenhouse', 'betauk', '77', "
                        "'Beta UK', 'Tampered Title', 'https://job-boards.greenhouse.io/betauk/jobs/77', "
                        "'London', 'GB', ARRAY['GB'], 'complete', 'complete_public_ats', 'Agent wording should not overwrite collector')"
                    ).fetchone()
                self.assertEqual(r_exist["candidate_key"], "greenhouse:betauk:77")
                cand_check = ev_conn.execute(
                    "SELECT title, effective_description, snapshot_count FROM jobwatch.research_discovery_evidence "
                    "WHERE candidate_key='greenhouse:betauk:77'"
                ).fetchone()
                self.assertEqual(cand_check["title"], "Graduate SWE")  # untouched!
                self.assertEqual(cand_check["effective_description"], col_desc)  # untouched!
                self.assertEqual(cand_check["snapshot_count"], 0)  # zero untrusted snapshots injected!

                # 3g. Direct SQL new vacancy ALWAYS enters unscreened (caller CANNOT forge priority verdict/rules_version)!
                long_desc = ("Graduate distributed Python & ML engineer in London. " * 150) + "Closing date: 29 November 2026."
                with ev_conn.transaction():
                    r_new = ev_conn.execute(
                        "SELECT * FROM jobwatch.submit_agent_vacancy("
                        "'direct-new-vac-001', 'local-cli:s1', 'untrusted-label', now(), 'other', NULL, NULL, "
                        "'Bright AI UK', 'Graduate Machine Learning Engineer 2027', "
                        "'https://brightai.co.uk/careers/grad-ml-2027', 'London, United Kingdom', 'GB', ARRAY['GB'], "
                        "'complete', 'complete_public_ats', %s)",
                        (long_desc,),
                    ).fetchone()
                self.assertEqual(r_new["complete_description_sha256"], sha256(long_desc.encode()).hexdigest())
                unscreened_row = ev_conn.execute(
                    "SELECT verdict, family, codes, rules_version, screening_basis "
                    "FROM jobwatch.research_discovery_candidates WHERE candidate_key=%s",
                    (r_new["candidate_key"],),
                ).fetchone()
                self.assertEqual(unscreened_row["verdict"], "review")
                self.assertEqual(unscreened_row["family"], "unscreened")
                self.assertEqual(unscreened_row["codes"], ["evidence.agent_submitted_unscreened"])
                self.assertEqual(unscreened_row["rules_version"], "unscreened-agent-submission")

                # 3h. Zero raw table writes for jobwatch_agent_evidence
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

            # 4. CROSS-LOGIN IDEMPOTENCY ISOLATION: `jobwatch_owner` can reuse 'shared-idem-001' with DIFFERENT content!
            with connect(owner_cfg) as own, own.transaction():
                r_own = own.execute(
                    "SELECT * FROM jobwatch.submit_agent_observation("
                    "'shared-idem-001', 'official_source_link', 'owner-cli:1', '', now(), "
                    "'https://acme.co.uk/owner-page', 'Owner completely different summary', '', NULL, NULL, NULL, NULL, %s)",
                    (sid,),
                ).fetchone()
            self.assertEqual(r_own["status"], "recorded")

            # Trusted deterministic collector rescreening upgrades the unscreened agent vacancy cleanly!
            with connect(col_cfg) as col:
                rescreened = discovery.rescreen_candidates(col)
                self.assertGreaterEqual(rescreened, 1)
                screened_row = col.execute(
                    "SELECT verdict, family, rules_version, screening_basis FROM jobwatch.discovery_candidates WHERE candidate_key=%s",
                    (r_new["candidate_key"],),
                ).fetchone()
                self.assertEqual(screened_row["verdict"], "priority")
                self.assertEqual(screened_row["screening_basis"], "full")
                self.assertNotEqual(screened_row["rules_version"], "unscreened-agent-submission")

            # 5. CONCURRENT TRANSACTIONAL RATE-BUDGET ENFORCEMENT (keyed on db_login, immune to contributor spoofing)
            ingestor = AgentEvidenceIngestor(resolve_dns=False)
            now = datetime.now(timezone.utc)
            with connect(owner_cfg) as own, own.transaction():
                cur_cnt = own.execute(
                    "SELECT count(*) AS n FROM jobwatch.agent_evidence_observations WHERE db_login='jobwatch_agent_evidence'"
                ).fetchone()["n"]
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

            with ThreadPoolExecutor(max_workers=5) as pool:
                outcomes = list(pool.map(submit_worker, range(6)))
            self.assertEqual(outcomes.count("recorded"), 2, outcomes)
            self.assertEqual(outcomes.count("budget_blocked"), 4, outcomes)

            # Raise budget for corroboration test observations.
            with connect(owner_cfg) as own, own.transaction():
                own.execute("UPDATE jobwatch.agent_evidence_budgets SET hourly_limit=50 WHERE db_login='jobwatch_agent_evidence'")

            # 6. TARGET-VACANCY BINDING IN CORROBORATION (unrelated page with matching wording MUST NOT corroborate!)
            with connect(ev_cfg) as ev_conn:
                # 6a. Observation pointing to UNRELATED blog URL for posting `greenhouse:acmeuk:42`
                obs_unrelated = ingestor.record_observation(ev_conn, ObservationSubmission(
                    kind="deadline_wording",
                    logical_contributor="local-cli:s2",
                    observed_at=now - timedelta(minutes=2),
                    source_url="https://unrelated-grad-blog.co.uk/tips",
                    posting_key=pkey,
                    claim_summary="Blog mentions closing date",
                    observed_wording="Applications close 29 November 2026.",
                ))
                # 6b. Observation pointing to official employer careers page for `greenhouse:acmeuk:42`
                obs_official = ingestor.record_observation(ev_conn, ObservationSubmission(
                    kind="deadline_wording",
                    logical_contributor="local-cli:s2",
                    observed_at=now - timedelta(minutes=1),
                    source_url="https://acme.co.uk/careers/grad-ml",
                    posting_key=pkey,
                    claim_summary="Official careers page closing date",
                    observed_wording="Applications close 29 November 2026.",
                ))

            with connect(col_cfg) as col_conn:
                # Unrelated page contains the EXACT quoted wording, yet MUST NOT reach `claim_corroborated`!
                c_unrel = corroborate_observation(
                    col_conn, obs_unrelated["observation_id"],
                    fetcher=lambda u, budget=None: SafeFetchResult(
                        200, b"<html><body>Random grad blog: Applications close 29 November 2026.</body></html>",
                        u, "93.184.216.34", False, 0,
                    ),
                )
                self.assertEqual(c_unrel["target_binding_status"], "unbound_target")
                self.assertEqual(c_unrel["wording_status"], "matched")
                self.assertEqual(c_unrel["claim_status"], "unverified")
                self.assertEqual(c_unrel["overall_state"], "wording_matched")
                self.assertEqual(c_unrel["evidence_completeness"], "complete")

                # SQL CHECK itself refuses inserting `claim_corroborated` with `unbound_target`!
                with self.assertRaises(psycopg.errors.CheckViolation):
                    with col_conn.transaction():
                        col_conn.execute(
                            "INSERT INTO jobwatch.agent_evidence_corroborations("
                            "observation_id,checked_at,checked_by,retrieval_status,target_binding_status,"
                            "wording_status,claim_status,overall_state,evidence_completeness) "
                            "VALUES (%s,now(),'bad','retrieved_200','unbound_target','matched','corroborated','claim_corroborated','complete')",
                            (obs_unrelated["observation_id"],),
                        )

                # Official employer domain + role title present -> `bound_host_and_identity` + `claim_corroborated`!
                c_bound = corroborate_observation(
                    col_conn, obs_official["observation_id"],
                    fetcher=lambda u, budget=None: SafeFetchResult(
                        200,
                        b"<html><body><h1>Acme UK - Graduate Machine Learning Engineer</h1><p>Applications close 29 November 2026.</p></body></html>",
                        u, "93.184.216.34", False, 0,
                    ),
                )
                self.assertEqual(c_bound["target_binding_status"], "bound_host_and_identity")
                self.assertEqual(c_bound["overall_state"], "claim_corroborated")
                self.assertEqual(c_bound["evidence_completeness"], "complete")
                self.assertIn("29 November 2026", c_bound["evidence_excerpt"])
                self.assertEqual(c_bound["provenance"]["target_binding_status"], "bound_host_and_identity")

                # Confirm corroboration NEVER silently created verifications or calendar_selections!
                with connect(owner_cfg) as own:
                    self.assertEqual(own.execute("SELECT count(*) AS n FROM jobwatch.verifications").fetchone()["n"], 0)
                    self.assertEqual(own.execute("SELECT count(*) AS n FROM jobwatch.calendar_selections").fetchone()["n"], 0)
        finally:
            subprocess.run(["docker", "exec", container, "dropdb", "-U", "postgres", "--force", db], check=False)


if __name__ == "__main__":
    unittest.main()
