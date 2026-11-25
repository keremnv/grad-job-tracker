"""Deterministic, concurrency-safe automatic source activation and lifecycle safeguards.

Evaluates discovered boards against objective, evidence-grounded verification gates
and configurable operational resource caps. Separates least-privilege phases cleanly:
  1. `stage_auto_candidates` (`jobwatch_owner`): acquires advisory lock, evaluates gates,
     registers qualifying `candidate` sources with explicit audited provenance.
  2. `validate_staged_candidates` (`jobwatch_collector`): runs standard `check_endpoint`
     on staged `auto_activation:*` candidates to certify live endpoint + `absence_safe`.
  3. `activate_validated_candidates` (`jobwatch_owner`): acquires advisory lock, re-checks
     fleet caps, activates validated absence-safe candidates via `activate_source`.

Deactivation (`deactivate_auto_source`) pauses only `auto_activation:*` sources, never
touches manually selected sources, never deletes historical vacancy evidence, and is
fully reversible via `reactivate_auto_source`.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import os
import re

from jobwatch import feeds
from .connection import DatabaseConfig, connect
from .endpoints import activate_source, check_endpoint
from .publisher import Collector

AUTO_ACTOR = "policy:auto-activation-v1"
AUTO_DISCOVERY_PREFIX = "auto_activation:"
LOCK_NAMESPACE = "jobwatch.auto_activation"


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

    @classmethod
    def from_env(cls) -> "AutoActivationPolicy":
        def _int(name: str, default: int) -> int:
            raw = os.environ.get(name)
            return int(raw) if raw is not None and raw.strip() else default
        return cls(
            max_monitored_boards=_int("JOBWATCH_AUTO_MAX_BOARDS", cls.max_monitored_boards),
            max_total_listed_postings=_int("JOBWATCH_AUTO_MAX_POSTINGS", cls.max_total_listed_postings),
            max_single_board_vacancies=_int("JOBWATCH_AUTO_MAX_BOARD_VACANCIES", cls.max_single_board_vacancies),
            max_activations_per_run=_int("JOBWATCH_AUTO_MAX_PER_RUN", cls.max_activations_per_run),
            min_uk_relevant_vacancies=_int("JOBWATCH_AUTO_MIN_UK_RELEVANT", cls.min_uk_relevant_vacancies),
            min_priority_vacancies=_int("JOBWATCH_AUTO_MIN_PRIORITY", cls.min_priority_vacancies),
        )


def _slugify(name: str, fallback: str) -> str:
    raw = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    if not raw or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", raw):
        raw = re.sub(r"[^a-z0-9]+", "-", fallback.lower()).strip("-")
    return raw or "employer"


@contextmanager
def _advisory_lock(conn):
    """Non-blocking session advisory lock ensuring concurrent runs cannot exceed budgets."""
    got = conn.execute(
        "SELECT pg_try_advisory_lock(hashtextextended(%s, 0)) AS ok",
        (LOCK_NAMESPACE,),
    ).fetchone()["ok"]
    try:
        yield bool(got)
    finally:
        if got:
            conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 0))", (LOCK_NAMESPACE,))


def evaluate_board_eligibility(
    board: dict,
    *,
    fleet_monitored: int,
    fleet_listed_postings: int,
    policy: AutoActivationPolicy = AutoActivationPolicy(),
) -> tuple[bool, list[str]]:
    """Pure deterministic gate returning (eligible, inspectable_rejection_reasons)."""
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


def _fleet_counts(conn) -> tuple[int, int]:
    mon = conn.execute("SELECT count(*) AS n FROM jobwatch.sources WHERE status='monitored'").fetchone()["n"]
    listed = conn.execute("SELECT count(*) AS n FROM jobwatch.postings WHERE status='listed'").fetchone()["n"]
    return int(mon), int(listed)


def stage_auto_candidates(
    owner_conn,
    *,
    policy: AutoActivationPolicy = AutoActivationPolicy(),
    dry_run: bool = True,
    now: datetime | None = None,
) -> dict:
    """Phase 1 (`jobwatch_owner`): evaluate discovered boards and stage eligible candidates."""
    at = now or datetime.now(timezone.utc)
    with _advisory_lock(owner_conn) as acquired:
        if not acquired:
            return {"phase": "stage", "dry_run": dry_run, "locked_by_concurrent_run": True,
                    "evaluated": 0, "eligible": [], "staged": [], "deferred": []}
        fleet_monitored, fleet_listed = _fleet_counts(owner_conn)
        pending_staged = owner_conn.execute(
            "SELECT count(*) AS n FROM jobwatch.sources WHERE status='candidate' AND coalesce(discovered_by,'') LIKE %s",
            (AUTO_DISCOVERY_PREFIX + "%",),
        ).fetchone()["n"]

        candidates = owner_conn.execute(
            "SELECT * FROM jobwatch.discovered_boards "
            "WHERE source_id IS NULL AND probe_status = 'active_uk_relevant' "
            "ORDER BY priority_vacancies DESC, review_vacancies DESC, board_key"
        ).fetchall()

        report = {
            "phase": "stage",
            "dry_run": dry_run,
            "locked_by_concurrent_run": False,
            "policy": asdict(policy),
            "fleet_before": {"monitored_boards": fleet_monitored, "listed_postings": fleet_listed, "pending_staged": int(pending_staged)},
            "evaluated": len(candidates),
            "eligible": [],
            "staged": [],
            "deferred": [],
        }

        staged_this_run = 0
        projected_monitored = fleet_monitored + int(pending_staged)
        projected_listed = fleet_listed
        for board in candidates:
            if staged_this_run + int(pending_staged) >= policy.max_activations_per_run:
                report["deferred"].append({"board_key": board["board_key"], "reasons": ["per_run_activation_cap_reached"]})
                continue
            ok, reasons = evaluate_board_eligibility(
                board,
                fleet_monitored=projected_monitored,
                fleet_listed_postings=projected_listed,
                policy=policy,
            )
            if not ok:
                report["deferred"].append({"board_key": board["board_key"], "reasons": reasons})
                continue
            report["eligible"].append(board["board_key"])
            if not dry_run:
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
                report["staged"].append({"board_key": board["board_key"], "source_id": sid})
            staged_this_run += 1
            projected_monitored += 1
            projected_listed += int(board["total_vacancies"] or 0)

        report["fleet_after"] = {"monitored_boards": projected_monitored, "listed_postings": projected_listed}
        return report


def validate_staged_candidates(
    collector: Collector,
    *,
    policy: AutoActivationPolicy = AutoActivationPolicy(),
    get_json=None,
    pause=None,
    fixture: bool = False,
) -> dict:
    """Phase 2 (`jobwatch_collector`): validate staged `auto_activation:*` candidate endpoints."""
    with connect(collector.config) as conn:
        rows = conn.execute(
            "SELECT id, provider, board FROM jobwatch.sources "
            "WHERE status='candidate' AND coalesce(discovered_by,'') LIKE %s "
            "ORDER BY id LIMIT %s",
            (AUTO_DISCOVERY_PREFIX + "%", policy.max_activations_per_run * 2),
        ).fetchall()

    report = {"phase": "validate", "checked": 0, "validated": [], "failed": []}
    for r in rows:
        report["checked"] += 1
        bk = f"{r['provider']}:{r['board'].lower()}"
        try:
            check = check_endpoint(collector, r["id"], get_json=get_json, pause=pause, fixture=fixture)
        except Exception:
            report["failed"].append({"board_key": bk, "source_id": r["id"], "status": "check_error"})
            continue
        if check["status"] != "validated" or (policy.require_absence_safe_validation and not check["absence_safe"]):
            report["failed"].append({
                "board_key": bk, "source_id": r["id"],
                "status": check["status"], "absence_safe": check["absence_safe"],
            })
        elif (check.get("vacancies") or 0) > policy.max_single_board_vacancies:
            report["failed"].append({
                "board_key": bk, "source_id": r["id"],
                "status": "exceeded_vacancy_cap_at_validation", "vacancies": check.get("vacancies"),
            })
        else:
            report["validated"].append({
                "board_key": bk, "source_id": r["id"],
                "run_id": str(check["run_id"]), "vacancies": int(check.get("vacancies") or 0),
                "absence_safe": bool(check["absence_safe"]),
            })
    return report


def activate_validated_candidates(
    owner_conn,
    *,
    policy: AutoActivationPolicy = AutoActivationPolicy(),
    dry_run: bool = True,
    fixture: bool = False,
) -> dict:
    """Phase 3 (`jobwatch_owner`): activate validated absence-safe candidates under advisory lock."""
    with _advisory_lock(owner_conn) as acquired:
        if not acquired:
            return {"phase": "activate", "dry_run": dry_run, "locked_by_concurrent_run": True,
                    "activated": [], "deferred": []}
        fleet_monitored, fleet_listed = _fleet_counts(owner_conn)
        rows = owner_conn.execute(
            "SELECT s.id, s.provider, s.board, r.id AS run_id, r.fetched_at, r.vacancies, r.absence_safe, "
            "coalesce(r.details->>'evidence_kind', 'live') AS evidence_kind "
            "FROM jobwatch.sources s "
            "JOIN LATERAL ("
            "  SELECT * FROM jobwatch.collection_runs cr "
            "  WHERE cr.source_id = s.id AND cr.mode = 'validate' AND cr.status = 'validated' "
            "  ORDER BY cr.run_order DESC LIMIT 1"
            ") r ON true "
            "WHERE s.status = 'candidate' AND coalesce(s.discovered_by, '') LIKE %s "
            "ORDER BY s.id",
            (AUTO_DISCOVERY_PREFIX + "%",),
        ).fetchall()

        report = {
            "phase": "activate",
            "dry_run": dry_run,
            "locked_by_concurrent_run": False,
            "fleet_before": {"monitored_boards": fleet_monitored, "listed_postings": fleet_listed},
            "activated": [],
            "deferred": [],
        }

        activations = 0
        for r in rows:
            bk = f"{r['provider']}:{r['board'].lower()}"
            vac = int(r["vacancies"] or 0)
            reasons = []
            if activations >= policy.max_activations_per_run:
                reasons.append("per_run_activation_cap_reached")
            if policy.require_absence_safe_validation and not r["absence_safe"]:
                reasons.append("validation_not_absence_safe")
            if vac <= 0:
                reasons.append("zero_vacancies_at_validation")
            if vac > policy.max_single_board_vacancies:
                reasons.append("exceeds_single_board_vacancy_cap")
            if fleet_monitored >= policy.max_monitored_boards:
                reasons.append("fleet_board_cap_reached")
            if fleet_listed + vac > policy.max_total_listed_postings:
                reasons.append("fleet_posting_cap_reached")
            if not fixture and r["evidence_kind"] != "live":
                reasons.append("non_live_validation_evidence")
            if reasons:
                report["deferred"].append({"board_key": bk, "source_id": r["id"], "reasons": reasons})
                continue

            if not dry_run:
                if not fixture:
                    activate_source(owner_conn, r["id"], r["run_id"], actor=policy.actor)
                else:
                    with owner_conn.transaction():
                        owner_conn.execute("SELECT set_config('jobwatch.actor', %s, true)", (policy.actor,))
                        owner_conn.execute(
                            "UPDATE jobwatch.sources SET endpoint_checked_at=%s, status='monitored' WHERE id=%s",
                            (r["fetched_at"], r["id"]),
                        )
            activations += 1
            fleet_monitored += 1
            fleet_listed += vac
            report["activated"].append({
                "board_key": bk, "source_id": r["id"], "run_id": str(r["run_id"]), "vacancies": vac,
            })

        report["fleet_after"] = {"monitored_boards": fleet_monitored, "listed_postings": fleet_listed}
        return report


def run_auto_activation_pass(
    owner_conn,
    collector: Collector,
    *,
    policy: AutoActivationPolicy = AutoActivationPolicy(),
    dry_run: bool = True,
    get_json=None,
    pause=None,
    fixture: bool = False,
    now: datetime | None = None,
) -> dict:
    """Combined 3-phase pass when both `owner_conn` and `collector` are supplied."""
    staged = stage_auto_candidates(owner_conn, policy=policy, dry_run=dry_run, now=now)
    if staged.get("locked_by_concurrent_run"):
        return {
            "dry_run": dry_run, "locked_by_concurrent_run": True,
            "evaluated": 0, "eligible": [], "activated": [], "validation_failed": [], "deferred": [],
        }
    if dry_run:
        return {
            "dry_run": True,
            "locked_by_concurrent_run": False,
            "fleet_before": staged["fleet_before"],
            "fleet_after": staged["fleet_after"],
            "evaluated": staged["evaluated"],
            "eligible": staged["eligible"],
            "activated": [],
            "validation_failed": [],
            "deferred": staged["deferred"],
        }
    validated = validate_staged_candidates(collector, policy=policy, get_json=get_json, pause=pause, fixture=fixture)
    activated = activate_validated_candidates(owner_conn, policy=policy, dry_run=False, fixture=fixture)
    return {
        "dry_run": False,
        "locked_by_concurrent_run": bool(activated.get("locked_by_concurrent_run")),
        "fleet_before": staged["fleet_before"],
        "fleet_after": activated.get("fleet_after", staged["fleet_after"]),
        "evaluated": staged["evaluated"],
        "eligible": staged["eligible"],
        "activated": activated["activated"],
        "validation_failed": validated["failed"],
        "deferred": staged["deferred"] + activated["deferred"],
    }


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


def deactivate_auto_source(owner_conn, source_id: int, *, reason: str, actor: str = AUTO_ACTOR) -> dict:
    """Safely pause an auto-activated source without deleting any historical vacancy evidence.

    Refuses manually curated sources (`discovered_by` without `auto_activation:` prefix).
    """
    if not reason or not reason.strip():
        raise ValueError("Deactivation requires an explicit reason")
    with owner_conn.transaction():
        owner_conn.execute("SELECT set_config('jobwatch.actor', %s, true)", (actor,))
        src = owner_conn.execute(
            "SELECT id, provider, board, status, discovered_by, mapping_evidence "
            "FROM jobwatch.sources WHERE id=%s FOR UPDATE",
            (source_id,),
        ).fetchone()
        if not src:
            raise ValueError(f"Unknown source_id: {source_id}")
        if not (src["discovered_by"] or "").startswith(AUTO_DISCOVERY_PREFIX):
            raise PermissionError("Refusing to auto-deactivate a manually curated source")
        if src["status"] == "paused":
            return {"source_id": source_id, "status": "paused", "changed": False}
        note = (src["mapping_evidence"] or "").strip()
        updated_evidence = f"{note}; auto_paused: {reason.strip()}" if note else f"auto_paused: {reason.strip()}"
        owner_conn.execute(
            "UPDATE jobwatch.sources SET status='paused', mapping_evidence=%s WHERE id=%s",
            (updated_evidence, source_id),
        )
    return {
        "source_id": source_id,
        "board_key": f"{src['provider']}:{src['board'].lower()}",
        "status": "paused",
        "changed": True,
        "reason": reason.strip(),
        "historical_evidence_preserved": True,
    }


def reactivate_auto_source(owner_conn, source_id: int, *, reason: str, actor: str = AUTO_ACTOR) -> dict:
    """Reverse an automatic pause cleanly after verification."""
    if not reason or not reason.strip():
        raise ValueError("Reactivation requires an explicit reason")
    with owner_conn.transaction():
        owner_conn.execute("SELECT set_config('jobwatch.actor', %s, true)", (actor,))
        src = owner_conn.execute(
            "SELECT id, provider, board, status, discovered_by, endpoint_checked_at, mapping_evidence "
            "FROM jobwatch.sources WHERE id=%s FOR UPDATE",
            (source_id,),
        ).fetchone()
        if not src:
            raise ValueError(f"Unknown source_id: {source_id}")
        if not (src["discovered_by"] or "").startswith(AUTO_DISCOVERY_PREFIX):
            raise PermissionError("Refusing to auto-reactivate a manually curated source")
        if src["endpoint_checked_at"] is None:
            raise ValueError("Cannot reactivate source without prior endpoint validation")
        if src["status"] == "monitored":
            return {"source_id": source_id, "status": "monitored", "changed": False}
        note = (src["mapping_evidence"] or "").strip()
        updated_evidence = f"{note}; auto_reactivated: {reason.strip()}" if note else f"auto_reactivated: {reason.strip()}"
        owner_conn.execute(
            "UPDATE jobwatch.sources SET status='monitored', mapping_evidence=%s WHERE id=%s",
            (updated_evidence, source_id),
        )
    return {
        "source_id": source_id,
        "board_key": f"{src['provider']}:{src['board'].lower()}",
        "status": "monitored",
        "changed": True,
        "reason": reason.strip(),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Bounded, audited automatic source activation")
    parser.add_argument("--phase", choices=("stage", "validate", "activate", "deactivation-report"), required=True)
    parser.add_argument("--apply", action="store_true", help="Commit changes (default is dry-run for owner phases)")
    parser.add_argument("--actor", default=AUTO_ACTOR)
    args = parser.parse_args(argv)

    policy = AutoActivationPolicy.from_env()
    if args.actor != policy.actor:
        policy = AutoActivationPolicy(**{**asdict(policy), "actor": args.actor})
    cfg = DatabaseConfig.from_env()

    if args.phase == "validate":
        collector = Collector(cfg, actor=args.actor)
        result = validate_staged_candidates(collector, policy=policy)
    else:
        with connect(cfg) as conn:
            if args.phase == "stage":
                result = stage_auto_candidates(conn, policy=policy, dry_run=not args.apply)
            elif args.phase == "activate":
                result = activate_validated_candidates(conn, policy=policy, dry_run=not args.apply)
            else:
                flagged = evaluate_deactivation_candidates(conn, policy=policy)
                paused = []
                if args.apply:
                    for item in flagged:
                        paused.append(deactivate_auto_source(
                            conn, item["source_id"], reason=",".join(item["reasons"]), actor=policy.actor,
                        ))
                result = {"phase": "deactivation-report", "dry_run": not args.apply, "flagged": flagged, "paused": paused}
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
