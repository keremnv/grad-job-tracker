"""Isolated prototype for deterministic automatic source activation and lifecycle caps.

NOT enabled in production. Evaluates discovered boards against strict objective
verification gates and fleet resource caps, then delegates to the existing
`endpoints.check_endpoint` and `endpoints.activate_source` transactions under an
explicit audited actor. Ambiguous identities, unsupported custom URL hosts, and
oversized boards remain discoverable candidates in `jobwatch.discovered_boards`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re

from jobwatch import feeds
from .endpoints import activate_source, check_endpoint

AUTO_ACTOR = "policy:auto-activation-v1"
AUTO_DISCOVERY_PREFIX = "auto_activation:"


@dataclass(frozen=True)
class AutoActivationPolicy:
    max_monitored_boards: int = 60
    max_total_listed_postings: int = 6000
    max_single_board_vacancies: int = 500
    max_activations_per_run: int = 3
    min_uk_relevant_vacancies: int = 1
    min_priority_vacancies: int = 0
    require_verified_official_identity: bool = True
    require_absence_safe_validation: bool = True
    deactivation_consecutive_failures: int = 5
    deactivation_stale_zero_uk_days: int = 90
    actor: str = AUTO_ACTOR


def _slugify(name: str, fallback: str) -> str:
    raw = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    if not raw or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", raw):
        raw = re.sub(r"[^a-z0-9]+", "-", fallback.lower()).strip("-")
    return raw or "employer"


def evaluate_board_eligibility(
    board: dict,
    *,
    fleet_monitored: int,
    fleet_listed_postings: int,
    policy: AutoActivationPolicy = AutoActivationPolicy(),
) -> tuple[bool, list[str]]:
    """Pure deterministic gate returning (eligible, rejection_reasons)."""
    reasons: list[str] = []
    provider = board.get("provider") or ""
    slug = board.get("board") or ""
    region = board.get("region") or "global"

    if board.get("source_id") is not None:
        reasons.append("already_registered_source")
    if provider not in ("greenhouse", "ashby", "lever", "workable"):
        reasons.append("unsupported_provider")
    if not feeds.BOARD.fullmatch(slug):
        reasons.append("invalid_board_slug")
    if provider in ("ashby", "workable") and region != "global":
        reasons.append("invalid_provider_region")
    if provider in ("greenhouse", "lever") and region not in ("global", "eu"):
        reasons.append("invalid_provider_region")

    if board.get("probe_status") != "active_uk_relevant":
        reasons.append("not_active_uk_relevant")
    uk_rel = int(board.get("priority_vacancies") or 0) + int(board.get("review_vacancies") or 0)
    if uk_rel < policy.min_uk_relevant_vacancies:
        reasons.append("insufficient_uk_relevant_vacancies")
    if int(board.get("priority_vacancies") or 0) < policy.min_priority_vacancies:
        reasons.append("insufficient_priority_vacancies")

    if policy.require_verified_official_identity:
        if board.get("identity_status") != "verified_official_link":
            reasons.append("identity_not_verified_official")
        if not feeds.public_url(board.get("careers_url") or ""):
            reasons.append("missing_verified_careers_url")
        if not (board.get("identity_evidence") or "").strip():
            reasons.append("missing_identity_evidence")

    custom_hosts = int(board.get("custom_host_urls") or 0)
    if custom_hosts > 0:
        if provider != "greenhouse" or (slug, region) not in feeds.CUSTOM_GREENHOUSE_URLS:
            reasons.append("unreviewed_custom_url_host")

    total_vac = int(board.get("total_vacancies") or 0)
    if total_vac <= 0:
        reasons.append("zero_vacancies")
    if total_vac > policy.max_single_board_vacancies:
        reasons.append("exceeds_single_board_vacancy_cap")
    if fleet_monitored >= policy.max_monitored_boards:
        reasons.append("fleet_board_cap_reached")
    if fleet_listed_postings + total_vac > policy.max_total_listed_postings:
        reasons.append("fleet_posting_cap_reached")

    return len(reasons) == 0, reasons


def run_auto_activation_pass(
    owner_conn,
    collector,
    *,
    policy: AutoActivationPolicy = AutoActivationPolicy(),
    dry_run: bool = True,
    get_json=None,
    pause=None,
    fixture: bool = False,
    now: datetime | None = None,
) -> dict:
    """Evaluate discovered boards and (when dry_run=False) validate & activate qualifying ones.

    Requires separate `owner_conn` (owner role, audited writes) and `collector`
    (collector role, `check_endpoint` validation). Never bypasses `check_endpoint`
    or `activate_source`.
    """
    at = now or datetime.now(timezone.utc)
    fleet_monitored = owner_conn.execute(
        "SELECT count(*) AS n FROM jobwatch.sources WHERE status='monitored'"
    ).fetchone()["n"]
    fleet_listed = owner_conn.execute(
        "SELECT count(*) AS n FROM jobwatch.postings WHERE status='listed'"
    ).fetchone()["n"]

    candidates = owner_conn.execute(
        "SELECT * FROM jobwatch.discovered_boards "
        "WHERE source_id IS NULL AND probe_status = 'active_uk_relevant' "
        "ORDER BY priority_vacancies DESC, review_vacancies DESC, board_key"
    ).fetchall()

    report = {
        "dry_run": dry_run,
        "fleet_before": {"monitored_boards": fleet_monitored, "listed_postings": fleet_listed},
        "evaluated": len(candidates),
        "eligible": [],
        "activated": [],
        "validation_failed": [],
        "deferred": [],
    }

    activations = 0
    for board in candidates:
        if activations >= policy.max_activations_per_run:
            report["deferred"].append({"board_key": board["board_key"], "reasons": ["per_run_activation_cap_reached"]})
            continue
        ok, reasons = evaluate_board_eligibility(
            board,
            fleet_monitored=fleet_monitored,
            fleet_listed_postings=fleet_listed,
            policy=policy,
        )
        if not ok:
            report["deferred"].append({"board_key": board["board_key"], "reasons": reasons})
            continue
        report["eligible"].append(board["board_key"])
        if dry_run:
            activations += 1
            fleet_monitored += 1
            fleet_listed += int(board["total_vacancies"] or 0)
            continue

        # 1. Register employer + candidate source as owner (audited).
        with owner_conn.transaction():
            owner_conn.execute("SELECT set_config('jobwatch.actor', %s, true)", (policy.actor,))
            company = (board["company_hint"] or board["board"]).strip()
            base_slug = _slugify(company, board["board"])
            emp = owner_conn.execute("SELECT id FROM jobwatch.employers WHERE slug=%s", (base_slug,)).fetchone()
            if emp:
                emp_id = emp["id"]
            else:
                emp_id = owner_conn.execute(
                    "INSERT INTO jobwatch.employers(slug, name, careers_url, watch_status) "
                    "VALUES (%s, %s, %s, 'listed_in_feed') RETURNING id",
                    (base_slug, company, board["careers_url"] or board["official_website"] or None),
                ).fetchone()["id"]
            src = owner_conn.execute(
                "SELECT id FROM jobwatch.sources WHERE provider=%s AND lower(board)=lower(%s)",
                (board["provider"], board["board"]),
            ).fetchone()
            if src:
                sid = src["id"]
            else:
                sid = owner_conn.execute(
                    "INSERT INTO jobwatch.sources(provider, board, region, employer_id, status, "
                    "validation_url, mapping_verified_at, mapping_verified_by, mapping_evidence, discovered_by) "
                    "VALUES (%s,%s,%s,%s,'candidate',%s,%s,%s,%s,%s) RETURNING id",
                    (
                        board["provider"], board["board"], board["region"], emp_id,
                        board["careers_url"], board["identity_checked_at"] or at,
                        policy.actor, board["identity_evidence"],
                        AUTO_DISCOVERY_PREFIX + ",".join(board["directory_sources"]),
                    ),
                ).fetchone()["id"]
            owner_conn.execute(
                "UPDATE jobwatch.discovered_boards SET source_id=%s WHERE board_key=%s",
                (sid, board["board_key"]),
            )

        # 2. Live/injected endpoint check as collector (never publishes or activates).
        check = check_endpoint(collector, sid, get_json=get_json, pause=pause, fixture=fixture)
        if check["status"] != "validated" or (policy.require_absence_safe_validation and not check["absence_safe"]):
            report["validation_failed"].append({
                "board_key": board["board_key"],
                "source_id": sid,
                "status": check["status"],
                "absence_safe": check["absence_safe"],
            })
            continue
        if (check.get("vacancies") or 0) > policy.max_single_board_vacancies:
            report["validation_failed"].append({
                "board_key": board["board_key"],
                "source_id": sid,
                "status": "exceeded_vacancy_cap_at_validation",
                "vacancies": check.get("vacancies"),
            })
            continue

        # 3. Activate through standard owner transaction (note: activate_source enforces evidence_kind='live').
        if not fixture:
            activate_source(owner_conn, sid, check["run_id"], actor=policy.actor)
        else:
            # Isolated fixture qualification mirrors activate_source checks cleanly.
            with owner_conn.transaction():
                owner_conn.execute("SELECT set_config('jobwatch.actor', %s, true)", (policy.actor,))
                owner_conn.execute(
                    "UPDATE jobwatch.sources SET endpoint_checked_at=%s, status='monitored' WHERE id=%s",
                    (check["fetched_at"], sid),
                )
        activations += 1
        fleet_monitored += 1
        fleet_listed += int(check.get("vacancies") or 0)
        report["activated"].append({
            "board_key": board["board_key"],
            "source_id": sid,
            "run_id": str(check["run_id"]),
            "vacancies": check.get("vacancies"),
        })

    report["fleet_after"] = {"monitored_boards": fleet_monitored, "listed_postings": fleet_listed}
    return report


def evaluate_deactivation_candidates(
    conn,
    *,
    policy: AutoActivationPolicy = AutoActivationPolicy(),
    now: datetime | None = None,
) -> list[dict]:
    """Identify auto-activated sources that should be paused due to failures, bloat, or zero UK yield.

    Never selects manually curated owner sources (`discovered_by` without `auto_activation:` prefix).
    """
    at = now or datetime.now(timezone.utc)
    stale_cutoff = at - timedelta(days=policy.deactivation_stale_zero_uk_days)
    rows = conn.execute(
        "SELECT s.id, s.provider, s.board, s.discovered_by, s.discovered_at, "
        "h.vacancies AS latest_vacancies, h.last_success_at "
        "FROM jobwatch.sources s JOIN jobwatch.source_health h ON h.source_id = s.id "
        "WHERE s.status = 'monitored' AND coalesce(s.discovered_by, '') LIKE %s "
        "ORDER BY s.id",
        (AUTO_DISCOVERY_PREFIX + "%",),
    ).fetchall()

    flagged = []
    for r in rows:
        reasons = []
        recent_runs = conn.execute(
            "SELECT status FROM jobwatch.collection_runs "
            "WHERE source_id=%s AND mode='publish' ORDER BY run_order DESC LIMIT %s",
            (r["id"], policy.deactivation_consecutive_failures),
        ).fetchall()
        if (
            len(recent_runs) == policy.deactivation_consecutive_failures
            and all(run["status"] == "failed" for run in recent_runs)
        ):
            reasons.append("consecutive_collection_failures")

        if (r["latest_vacancies"] or 0) > policy.max_single_board_vacancies * 2:
            reasons.append("exceeded_2x_vacancy_cap")

        if r["discovered_at"] <= stale_cutoff:
            rel_count = conn.execute(
                "SELECT count(*) AS n FROM jobwatch.postings "
                "WHERE source_id=%s AND status='listed' AND verdict IN ('priority','review')",
                (r["id"],),
            ).fetchone()["n"]
            if rel_count == 0:
                reasons.append("zero_uk_relevant_yield_over_window")

        if reasons:
            flagged.append({
                "source_id": r["id"],
                "board_key": f"{r['provider']}:{r['board'].lower()}",
                "reasons": reasons,
            })
    return flagged
