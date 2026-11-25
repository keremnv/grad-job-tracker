"""Disposable PostgreSQL 17 integration tests for isolated agent-contributed evidence ingestion."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import unittest

import psycopg
from psycopg.conninfo import make_conninfo

from jobwatch.postgres.agent_evidence import (
    AgentEvidenceIngestor,
    EvidenceValidationError,
    ObservationSubmission,
    corroborate_observation,
    validate_public_evidence_url,
)
from jobwatch.postgres.connection import connect
from jobwatch.postgres.migrate import migrate
from support import DatabaseTest, actor

ROOT = Path(__file__).resolve().parents[2]
PROPOSAL_SQL = ROOT / "sql/postgresql/proposals/0011_agent_evidence_observations.sql"
ROLES_SQL = ROOT / "sql/postgresql/proposals/agent_evidence_roles.sql"


class AgentEvidenceIngestionPgTests(DatabaseTest):
    def test_narrow_authority_contradictions_ssrf_and_corroboration(self):
        container = os.environ["JOBWATCH_PG_TEST_CONTAINER"]
        db = "jobwatch_agent_evidence_test"
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
                mig.execute(PROPOSAL_SQL.read_text(encoding="utf-8"))
                mig.commit()
            admin(ROLES_SQL.read_text(encoding="utf-8"))
            admin(f"GRANT CONNECT ON DATABASE {db} TO jobwatch_agent_evidence; "
                  f"ALTER ROLE jobwatch_agent_evidence IN DATABASE {db} SET search_path=jobwatch,pg_catalog;")

            # Seed one employer, source, posting, and version so posting_evidence / deadline_wording can attach.
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
                    "VALUES (%s,%s,%s,'git_history',E'\\\\x7b7d',now(),'normalizer-v2',repeat('b',64),'Acme UK','Graduate SWE','https://job-boards.greenhouse.io/acmeuk/jobs/42','London, UK','Desc')",
                    (vid, pkey, sid),
                )

            # 1. SSRF and untrusted URL rejections
            for bad in (
                "http://example.co.uk/jobs",
                "https://169.254.169.254/latest/meta-data/",
                "https://127.0.0.1/admin",
                "https://localhost/jobs",
                "https://10.0.0.5/internal",
                "https://user:pass@example.co.uk/jobs",
                "file:///etc/passwd",
            ):
                with self.assertRaises(EvidenceValidationError, msg=f"Should reject {bad}"):
                    validate_public_evidence_url(bad)

            ingestor = AgentEvidenceIngestor(hourly_budget=10, resolve_dns=False)
            now = datetime.now(timezone.utc)

            with connect(ev_cfg) as ev_conn:
                # 2. Record new public vacancy -> discovery_candidates + observation log
                vac = ingestor.record_new_vacancy(
                    ev_conn,
                    contributor="agent:research-session-1",
                    observed_at=now - timedelta(minutes=2),
                    company="Bright AI UK",
                    title="Graduate Machine Learning Engineer 2027",
                    posting_url="https://brightai.co.uk/careers/grad-ml-2027",
                    location_text="London, United Kingdom",
                    country="GB",
                    description_excerpt="Build PyTorch models in London.",
                )
                self.assertEqual(vac["status"], "recorded")
                self.assertEqual(vac["corroboration_status"], "recorded")
                self.assertEqual(vac["verdict"], "priority")

                # 3. Record contradicting deadline wording observations on existing posting -> BOTH preserved, conflict flagged, NOT in posting_deadline_claims
                d1 = ingestor.record_observation(ev_conn, ObservationSubmission(
                    kind="deadline_wording", contributor="agent:research-session-1",
                    observed_at=now - timedelta(minutes=1),
                    source_url="https://acme.co.uk/careers/grad-swe",
                    posting_key=pkey,
                    claim_summary="Observed closing date on careers page",
                    observed_wording="Applications close 29 November 2026.",
                ))
                # Idempotent replay returns same observation without duplicating
                d1_replay = ingestor.record_observation(ev_conn, ObservationSubmission(
                    kind="deadline_wording", contributor="agent:research-session-1",
                    observed_at=now - timedelta(minutes=1),
                    source_url="https://acme.co.uk/careers/grad-swe",
                    posting_key=pkey,
                    claim_summary="Observed closing date on careers page",
                    observed_wording="Applications close 29 November 2026.",
                ))
                self.assertEqual(d1_replay["status"], "duplicate_replayed")
                self.assertEqual(d1_replay["observation_id"], d1["observation_id"])

                d2 = ingestor.record_observation(ev_conn, ObservationSubmission(
                    kind="deadline_wording", contributor="agent:research-session-2",
                    observed_at=now,
                    source_url="https://acme.co.uk/careers/faq",
                    posting_key=pkey,
                    claim_summary="FAQ lists a later closing date",
                    observed_wording="Closing date: 5 December 2026.",
                ))
                self.assertEqual(d2["status"], "recorded")

                # Contradictions are explicitly visible in research_agent_evidence
                obs_rows = ev_conn.execute(
                    "SELECT observation_id, candidate_deadline_date, corroboration_status, has_conflict "
                    "FROM jobwatch.research_agent_evidence WHERE posting_key=%s ORDER BY candidate_deadline_date",
                    (pkey,),
                ).fetchall()
                self.assertEqual(len(obs_rows), 2)
                self.assertTrue(all(r["has_conflict"] for r in obs_rows))
                self.assertTrue(all(r["corroboration_status"] == "recorded" for r in obs_rows))

                # Agent observations MUST NOT appear as authoritative posting_deadline_claims!
                self.assertEqual(
                    ev_conn.execute("SELECT count(*) AS n FROM jobwatch.posting_deadline_claims WHERE posting_key=%s", (pkey,)).fetchone()["n"],
                    0,
                )

                # 4. Official source link, posting evidence, source identity
                for kind, extra in (
                    ("official_source_link", {"source_id": sid}),
                    ("posting_evidence", {"posting_key": pkey}),
                    ("source_identity", {"source_id": sid}),
                ):
                    r = ingestor.record_observation(ev_conn, ObservationSubmission(
                        kind=kind, contributor="agent:research-session-1", observed_at=now,
                        source_url="https://acme.co.uk/careers",
                        claim_summary=f"Observed {kind}", observed_wording="Official Acme UK careers page.",
                        **extra,
                    ))
                    self.assertEqual(r["status"], "recorded")

                # 5. Privilege boundaries: jobwatch_agent_evidence CANNOT self-corroborate, UPDATE, DELETE, or write owner/collector tables!
                forbidden_sqls = [
                    "UPDATE jobwatch.agent_evidence_observations SET corroboration_status='corroborated'",
                    "DELETE FROM jobwatch.agent_evidence_observations",
                    "INSERT INTO jobwatch.agent_evidence_observations(idempotency_key,kind,contributor,observed_at,source_url,claim_summary,payload_sha256,corroboration_status,corroborated_by,corroborated_at) VALUES ('selfcorr123','official_source_link','agent:x',now(),'https://acme.co.uk','x',repeat('0',64),'corroborated','agent:x',now())",
                    "INSERT INTO jobwatch.verifications(posting_key,kind,checked_at,checked_by,url,value) VALUES ('greenhouse:acmeuk:42','deadline',now(),'agent','https://acme.co.uk','2026-11-29')",
                    "INSERT INTO jobwatch.calendar_selections(posting_key,mode,recorded_by) VALUES ('greenhouse:acmeuk:42','include','agent')",
                    "INSERT INTO jobwatch.triage(posting_key,decision) VALUES ('greenhouse:acmeuk:42','interested')",
                    "UPDATE jobwatch.postings SET verdict='excluded'",
                    "UPDATE jobwatch.sources SET status='paused'",
                ]
                for stmt in forbidden_sqls:
                    with self.assertRaises(psycopg.errors.InsufficientPrivilege, msg=f"Must deny: {stmt}"):
                        with ev_conn.transaction():
                            ev_conn.execute(stmt)

                # Future timestamp rejected
                with self.assertRaises(EvidenceValidationError):
                    ingestor.record_observation(ev_conn, ObservationSubmission(
                        kind="official_source_link", contributor="agent:x",
                        observed_at=now + timedelta(hours=2),
                        source_url="https://acme.co.uk", claim_summary="Future claim",
                    ))

            # 6. Independent corroboration by collector transitions recorded -> corroborated / unresolved_conflict
            with connect(col_cfg) as col_conn:
                c1 = corroborate_observation(
                    col_conn, d1["observation_id"],
                    fetch_url=lambda _u: (200, b"<html><body><p>Applications close 29 November 2026.</p></body></html>"),
                )
                self.assertEqual(c1["corroboration_status"], "corroborated")
                c2 = corroborate_observation(
                    col_conn, d2["observation_id"],
                    fetch_url=lambda _u: (200, b"<html><body><p>Different text entirely.</p></body></html>"),
                )
                self.assertEqual(c2["corroboration_status"], "unresolved_conflict")
        finally:
            subprocess.run(["docker", "exec", container, "dropdb", "-U", "postgres", "--force", db], check=False)


if __name__ == "__main__":
    unittest.main()
