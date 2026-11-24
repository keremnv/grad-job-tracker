"""Isolated disposable PostgreSQL 17 demonstration of Workable vacancy persistence.

Creates its own temporary loopback-only Docker PostgreSQL container, applies
the exact existing Graduate Job Watch migrations, demonstrates BOTH:
  - Path A (zero schema change): persisting the independently discovered
    Stalis `1C9CCEA6B4` vacancy as an evidenced `leads` row with `owner_audit`.
  - Path B (proposed Workable provider extension): persisting the exact raw
    Workable widget JSON record into `employers` -> `sources` -> `collection_runs`
    -> `postings` -> immutable `posting_versions` (ASCII JSON evidence,
    SHA-256, `search_tsv`) -> `posting_events` -> unchanged `research.search`
    (FTS + BM25) and `research.evidence()`.

NEVER connects to production. Always removes its disposable container on exit.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from experiments.ats_discovery.workable_discovery import widget_job_to_posting
from jobwatch import filtering
from jobwatch.postgres.collection import Processing, content, iso, normalize_v1, safe
from jobwatch.postgres.connection import DatabaseConfig, connect
from jobwatch.postgres.evidence import encode_evidence
from jobwatch.postgres.migrate import migrate
from jobwatch.postgres import research


def _start_disposable_postgres() -> tuple[str, int]:
    token = secrets.token_hex(8)
    name = f"jobwatch-discovery-demo-{token}"
    subprocess.run(
        [
            "docker", "run", "-d", "--rm",
            "--name", name,
            "--label", "jobwatch.experiment=workable-persistence-demo",
            "-p", "127.0.0.1::5432",
            "-e", "POSTGRES_HOST_AUTH_METHOD=trust",
            "-e", "POSTGRES_DB=jobwatch_discovery_demo",
            "postgres:17",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    port_out = subprocess.check_output(["docker", "port", name, "5432/tcp"], text=True).strip()
    port = int(port_out.rsplit(":", 1)[1])

    for _ in range(60):
        probe = subprocess.run(
            ["docker", "exec", name, "psql", "-h", "127.0.0.1", "-U", "postgres", "-d", "jobwatch_discovery_demo", "-c", "SELECT 1"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if probe.returncode == 0:
            break
        time.sleep(0.5)
    else:
        subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        raise RuntimeError("Disposable PostgreSQL did not become ready in time")

    bootstrap_sql = (REPO_ROOT / "sql" / "postgresql" / "bootstrap.sql").read_text()
    try:
        subprocess.run(
            ["docker", "exec", "-i", name, "psql", "-h", "127.0.0.1", "-v", "ON_ERROR_STOP=1", "-U", "postgres", "-d", "jobwatch_discovery_demo"],
            input=bootstrap_sql,
            text=True,
            check=True,
            stdout=subprocess.DEVNULL,
        )
    except Exception:
        subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        raise
    return name, port


def _dsn(port: int, user: str) -> DatabaseConfig:
    from psycopg.conninfo import make_conninfo
    dsn = make_conninfo(
        host="127.0.0.1",
        hostaddr="127.0.0.1",
        port=port,
        dbname="jobwatch_discovery_demo",
        user=user,
        sslmode="disable",
    )
    return DatabaseConfig(dsn, purpose="migration" if user == "jobwatch_migrator" else "runtime")


def run_demo(discovery_artifact: Path) -> dict:
    data = json.loads(discovery_artifact.read_text())
    held_out = data["held_out_acceptance_test"]
    if not held_out.get("passed"):
        raise RuntimeError("Discovery artifact did not pass the held-out acceptance test")
    res = held_out["widget_resolution"]
    raw_item = data["held_out_widget_raw"]
    if not raw_item:
        raise RuntimeError("Missing raw widget item for held-out record")

    account = res["account_slug"]
    company = res["company"]
    posting = widget_job_to_posting(account, company, raw_item)
    observed_at = datetime.now(timezone.utc)
    processing = Processing()
    snapshot = safe(normalize_v1(posting, iso(observed_at)))
    judgment = processing.judge(snapshot)
    evidence_bytes = encode_evidence(raw_item)
    norm_digest = sha256(encode_evidence(content(snapshot))).hexdigest()

    container, port = _start_disposable_postgres()
    try:
        # Apply exact unmodified production migrations (0001..0007, skip vector if extension absent)
        with connect(_dsn(port, "jobwatch_migrator")) as conn:
            # Skip 0004_embeddings if pgvector isn't installed in stock postgres:17 image;
            # or apply core migrations 0001, 0002, 0003, 0005, 0006, 0007.
            mig_dir = REPO_ROOT / "src" / "jobwatch" / "postgres" / "migrations"
            conn.execute("CREATE SCHEMA IF NOT EXISTS jobwatch")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS jobwatch.schema_migrations "
                "(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            for sql_file in sorted(mig_dir.glob("*.sql")):
                if sql_file.name.startswith("0004_"):
                    continue
                with conn.transaction():
                    conn.execute(sql_file.read_text())
                    conn.execute("INSERT INTO jobwatch.schema_migrations(version) VALUES (%s)", (sql_file.name,))

        # ------------------------------------------------------------------
        # Path A: Zero schema migration — immediate entry into existing `leads`
        # ------------------------------------------------------------------
        lead_key = f"vacancy:workable:{account}:{posting.external_id.lower()}"
        with connect(_dsn(port, "jobwatch_owner")) as conn, conn.transaction():
            conn.execute("SELECT set_config('jobwatch.actor', %s, true)", ("owner:discovery-feasibility-demo",))
            emp_id = conn.execute(
                "INSERT INTO jobwatch.employers(slug, name, careers_url, watch_status, notes) "
                "VALUES (%s, %s, %s, 'listed_in_feed', %s) RETURNING id",
                (
                    account.lower(),
                    company,
                    f"https://apply.workable.com/{account}/",
                    "Isolated disposable demo — discovered via jobs.workable.com search and verified against apply.workable.com widget.",
                ),
            ).fetchone()["id"]
            conn.execute(
                "INSERT INTO jobwatch.leads(lead_key, kind, employer_id, company, title, state, url, official, "
                "source_note, wording, checked_at, checked_by) "
                "VALUES (%s, 'vacancy', %s, %s, %s, 'listed', %s, true, %s, %s, %s, 'agent_on_owner_instruction')",
                (
                    lead_key,
                    emp_id,
                    company,
                    posting.title,
                    posting.url,
                    f"Independently discovered via jobs.workable.com search and verified on apply.workable.com/api/v1/widget/accounts/{account}",
                    f"Published {posting.published_at}; location: {posting.location}",
                    observed_at,
                ),
            )

        with connect(_dsn(port, "jobwatch_owner")) as conn:
            lead_row = conn.execute(
                "SELECT lead_key, company, title, state, url, url_exact, official FROM jobwatch.leads WHERE lead_key=%s",
                (lead_key,),
            ).fetchone()
            audit_count_a = conn.execute(
                "SELECT count(*) AS n FROM jobwatch.owner_audit WHERE table_name IN ('employers','leads')"
            ).fetchone()["n"]

        # ------------------------------------------------------------------
        # Path B: Native ATS evidence & persistence architecture (prototype)
        # ------------------------------------------------------------------
        with connect(_dsn(port, "jobwatch_migrator")) as conn, conn.transaction():
            conn.execute("ALTER TABLE jobwatch.sources DROP CONSTRAINT sources_provider_check")
            conn.execute(
                "ALTER TABLE jobwatch.sources ADD CONSTRAINT sources_provider_check "
                "CHECK (provider IN ('greenhouse','lever','ashby','workable'))"
            )

        with connect(_dsn(port, "jobwatch_owner")) as conn, conn.transaction():
            conn.execute("SELECT set_config('jobwatch.actor', %s, true)", ("owner:discovery-feasibility-demo",))
            source_id = conn.execute(
                "INSERT INTO jobwatch.sources(provider, board, region, employer_id, status, validation_url, "
                "endpoint_checked_at, mapping_verified_at, mapping_verified_by, mapping_evidence, discovered_by) "
                "VALUES ('workable', %s, 'global', %s, 'monitored', %s, %s, %s, %s, %s, %s) RETURNING id",
                (
                    account,
                    emp_id,
                    f"https://apply.workable.com/{account}/",
                    observed_at,
                    observed_at,
                    "experiment:disposable-demo",
                    "Disposable demo: jobs.workable.com discovery corroborated against apply.workable.com widget.",
                    "experiment:workable-search-v0",
                ),
            ).fetchone()["id"]

        run_id = uuid4()
        version_id = uuid4()
        from psycopg.types.json import Jsonb
        with connect(_dsn(port, "jobwatch_collector")) as conn, conn.transaction():
            conn.execute("SELECT set_config('jobwatch.actor', %s, true)", ("collector:discovery-feasibility-demo",))
            # Respect column-level grants on collection_runs: register first, then finalize.
            conn.execute(
                "INSERT INTO jobwatch.collection_runs(id, source_id, mode, request_version, processing_revision) "
                "VALUES (%s, %s, 'publish', 'workable:widget-details-v1', %s)",
                (run_id, source_id, processing.revision),
            )
            conn.execute(
                "INSERT INTO jobwatch.postings(posting_key, source_id, provider, board, native_id, status, "
                "first_seen_at, first_seen_basis, last_seen_at, last_seen_run_id, current_version_id, "
                "verdict, family, codes, rules_version, screening_input_sha256, screening_basis, screened_at) "
                "VALUES (%s, %s, 'workable', %s, %s, 'listed', %s, 'observed', %s, %s, %s, %s, %s, %s, %s, %s, %s, clock_timestamp())",
                (
                    posting.key,
                    source_id,
                    account,
                    posting.external_id,
                    observed_at,
                    observed_at,
                    run_id,
                    version_id,
                    judgment["verdict"],
                    judgment["family"],
                    judgment["codes"],
                    judgment["rules_version"],
                    judgment["input_sha256"],
                    judgment["basis"],
                ),
            )
            conn.execute(
                "INSERT INTO jobwatch.posting_versions(id, posting_key, source_id, origin, evidence, first_run_id, "
                "observed_at, normalizer_version, normalized_sha256, nul_replaced, company, title, posting_url, "
                "location_text, country, countries, department, team, employment_type, salary, published_at, "
                "source_dates, deadlines, description_text, extra_fields) "
                "VALUES (%s,%s,%s,'collector',%s,%s,%s,%s,%s,false,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    version_id,
                    posting.key,
                    source_id,
                    evidence_bytes,
                    run_id,
                    observed_at,
                    processing.normalizer_version,
                    norm_digest,
                    snapshot["company"],
                    snapshot["title"],
                    snapshot["posting_url"],
                    snapshot["location_text"],
                    snapshot["country"],
                    snapshot["countries"],
                    snapshot["department"],
                    snapshot["team"],
                    snapshot["employment_type"],
                    snapshot["salary"],
                    snapshot["published_at"],
                    Jsonb(snapshot["source_dates"]),
                    Jsonb(snapshot["deadlines"]),
                    snapshot["description_text"],
                    Jsonb(snapshot["extra_fields"]),
                ),
            )
            conn.execute(
                "INSERT INTO jobwatch.posting_events(posting_key, source_id, run_id, observed_at, category, kind, after_version_id) "
                "VALUES (%s, %s, %s, %s, 'observation', 'first_seen', %s)",
                (posting.key, source_id, run_id, observed_at, version_id),
            )
            conn.execute(
                "UPDATE jobwatch.collection_runs SET status='published', fetched_at=%s, finished_at=clock_timestamp(), "
                "source_records=1, vacancies=1, skipped_non_vacancies=0, absence_safe=true WHERE id=%s",
                (observed_at, run_id),
            )
            conn.execute(
                "UPDATE jobwatch.sources SET last_published_run_id=%s WHERE id=%s",
                (run_id, source_id),
            )

        # Verify read-back via UNCHANGED Graduate Job Watch views & retrieval functions!
        with connect(_dsn(port, "jobwatch_owner")) as conn:
            rp = conn.execute(
                "SELECT posting_key, provider, board, native_id, status, verdict, family, codes, "
                "company, title, posting_url, location_text, evidence_sha256 "
                "FROM jobwatch.research_postings WHERE posting_key=%s",
                (posting.key,),
            ).fetchone()
            bm25 = research.search(conn, "Data Science AI Python SQL", method="bm25", limit=5)
            fts = research.search(conn, "Data Science AI Python SQL", method="fts", limit=5)
            ev = research.evidence(conn, posting.key)

        return {
            "disposable_target": container,
            "path_a_zero_migration_lead": {
                "lead_key": lead_row["lead_key"],
                "company": lead_row["company"],
                "title": lead_row["title"],
                "state": lead_row["state"],
                "url_exact": lead_row["url_exact"],
                "official": lead_row["official"],
                "owner_audit_rows_recorded": audit_count_a,
            },
            "path_b_native_ats_evidence": {
                "posting_key": rp["posting_key"],
                "verdict": rp["verdict"],
                "family": rp["family"],
                "codes": rp["codes"],
                "location_text": rp["location_text"],
                "evidence_sha256": rp["evidence_sha256"],
                "evidence_bytes": len(evidence_bytes),
                "bm25_hits": len(bm25["items"]),
                "bm25_top_key": bm25["items"][0]["posting_key"] if bm25["items"] else None,
                "fts_hits": len(fts["items"]),
                "fts_top_key": fts["items"][0]["posting_key"] if fts["items"] else None,
                "inspect_evidence_shortcode": ev["untrusted_evidence"]["data"].get("shortcode"),
                "inspect_evidence_trust_label": ev["trust"],
            },
        }
    finally:
        subprocess.run(["docker", "rm", "-f", container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Disposable PostgreSQL persistence demo for Workable discovery")
    parser.add_argument("--discovery-json", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    result = run_demo(args.discovery_json)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
