"""PostgreSQL persistence and bounded orchestration for recurring directory discovery.

Manages `jobwatch.discovered_boards` (board-level freshness, deduplication against
`jobwatch.sources`, UK relevance yield, and read-only employer identity evidence)
and feeds discovered UK `priority`/`review` vacancies into `jobwatch.discovery_candidates`
via `jobwatch.postgres.discovery.record_discoveries`.

Never activates `sources`, never modifies `postings`/`posting_versions`, and never
writes owner-curated tables (`leads`, `applications`, `triage`, `calendar_selections`).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import sys
import time

from jobwatch.discovery.directory_discovery import (
    BoardProbeResult,
    DEFAULT_DIRECTORIES,
    DirectoryBoard,
    DiscoveryRateLimitError,
    IdentityCheckResult,
    default_http_get,
    fetch_directory,
    probe_ats_board,
    verify_board_identity,
)
from .collection import Processing, iso, safe
from .connection import DatabaseConfig, connect
from .discovery import record_discoveries


def sync_directory_boards(
    conn,
    boards,
    *,
    directory_source: str,
    provenance: dict,
    observed_at: datetime | None = None,
) -> dict:
    """Upsert directory boards into `jobwatch.discovered_boards` and deduplicate against `sources`."""
    from psycopg.types.json import Jsonb

    at = observed_at or datetime.now(timezone.utc)
    iso(at)
    counts = {"total": 0, "inserted": 0, "updated": 0, "already_registered": 0, "unknown_boards": 0}

    with conn.transaction():
        known_sources = {
            (r["provider"], r["board"].lower()): r["id"]
            for r in conn.execute("SELECT id, provider, board FROM jobwatch.sources")
        }
        existing_keys = {
            r["board_key"]: r
            for r in conn.execute(
                "SELECT board_key, company_hint, uk_hint, directory_sources, directory_provenance, "
                "last_directory_seen_at, source_id FROM jobwatch.discovered_boards"
            )
        }
        for item in boards:
            if not isinstance(item, DirectoryBoard):
                raise TypeError("Expected DirectoryBoard instances")
            counts["total"] += 1
            bkey = item.board_key
            source_id = known_sources.get((item.provider, item.board.lower()))
            if source_id is not None:
                counts["already_registered"] += 1
            else:
                counts["unknown_boards"] += 1
            prev = existing_keys.get(bkey)
            if prev is None:
                conn.execute(
                    "INSERT INTO jobwatch.discovered_boards("
                    "board_key, provider, board, company_hint, uk_hint, "
                    "directory_sources, directory_provenance, first_discovered_at, last_directory_seen_at, source_id"
                    ") VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        bkey, item.provider, item.board, safe(item.company_hint), bool(item.uk_hint),
                        [directory_source], Jsonb(safe({directory_source: provenance})), at, at, source_id,
                    ),
                )
                counts["inserted"] += 1
            else:
                srcs = list(dict.fromkeys(list(prev["directory_sources"]) + [directory_source]))
                prov = dict(prev["directory_provenance"] or {})
                prov[directory_source] = safe(provenance)
                last_seen = max(prev["last_directory_seen_at"], at)
                company_hint = prev["company_hint"] or safe(item.company_hint)
                uk_hint = bool(prev["uk_hint"] or item.uk_hint)
                conn.execute(
                    "UPDATE jobwatch.discovered_boards SET "
                    "company_hint=%s, uk_hint=%s, directory_sources=%s, directory_provenance=%s, "
                    "last_directory_seen_at=%s, source_id=coalesce(%s, source_id) "
                    "WHERE board_key=%s",
                    (company_hint, uk_hint, srcs, Jsonb(prov), last_seen, source_id, bkey),
                )
                counts["updated"] += 1
    return counts


def select_probe_batch(
    conn,
    *,
    limit: int = 40,
    providers: tuple[str, ...] = ("greenhouse", "ashby", "lever"),
    uk_hint_quota: int | None = None,
    reprobe_active_days: int = 7,
    reprobe_inactive_days: int = 30,
    now: datetime | None = None,
) -> list[dict]:
    """Select a bounded rotating batch of unmonitored candidate boards for probing.

    Skips all boards linked to an existing `jobwatch.sources` row (monitored,
    candidate, paused, or retired) and respects cooldown windows so dead or
    non-UK boards are not re-probed every run.
    """
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("Probe batch limit must be 1..500")
    at = now or datetime.now(timezone.utc)
    iso(at)
    active_cutoff = at - timedelta(days=reprobe_active_days)
    inactive_cutoff = at - timedelta(days=reprobe_inactive_days)
    uk_quota = min(limit, max(0, (limit // 2) if uk_hint_quota is None else uk_hint_quota))

    base_where = """
      b.provider = ANY(%s)
      AND b.source_id IS NULL
      AND NOT EXISTS (
        SELECT 1 FROM jobwatch.sources s
        WHERE s.provider = b.provider AND lower(s.board) = lower(b.board)
      )
      AND (
        b.last_probed_at IS NULL
        OR (b.probe_status = 'active_uk_relevant' AND b.last_probed_at < %s)
        OR (b.probe_status IN ('active_non_uk', 'empty', 'error') AND b.last_probed_at < %s)
        OR (b.probe_status = 'dead' AND b.last_probed_at < %s)
      )
    """
    order_clause = """
      ORDER BY
        CASE b.probe_status
          WHEN 'unprobed' THEN 0
          WHEN 'active_uk_relevant' THEN 1
          WHEN 'error' THEN 2
          ELSE 3
        END,
        b.last_probed_at ASC NULLS FIRST,
        b.board_key
    """
    selected: list[dict] = []
    seen: set[str] = set()

    if uk_quota > 0:
        uk_rows = conn.execute(
            "SELECT b.board_key, b.provider, b.board, b.region, b.company_hint, b.uk_hint, "
            "b.probe_status, b.identity_status, b.directory_sources "
            "FROM jobwatch.discovered_boards b WHERE b.uk_hint = true AND " + base_where + order_clause + " LIMIT %s",
            (list(providers), active_cutoff, inactive_cutoff, inactive_cutoff, uk_quota),
        ).fetchall()
        for r in uk_rows:
            seen.add(r["board_key"])
            selected.append(r)

    remaining = limit - len(selected)
    if remaining > 0:
        more = conn.execute(
            "SELECT b.board_key, b.provider, b.board, b.region, b.company_hint, b.uk_hint, "
            "b.probe_status, b.identity_status, b.directory_sources "
            "FROM jobwatch.discovered_boards b WHERE " + base_where + order_clause + " LIMIT %s",
            (list(providers), active_cutoff, inactive_cutoff, inactive_cutoff, remaining + len(seen)),
        ).fetchall()
        for r in more:
            if r["board_key"] not in seen and len(selected) < limit:
                seen.add(r["board_key"])
                selected.append(r)
    return selected


def select_probe_batch_prioritized(
    conn,
    *,
    limit: int = 40,
    providers: tuple[str, ...] = ("greenhouse", "ashby", "lever"),
    exploration_ratio: float = 0.35,
    new_board_days: int = 7,
    reprobe_active_days: int = 7,
    reprobe_non_uk_days: int = 30,
    now: datetime | None = None,
) -> list[dict]:
    """Deterministic multi-signal scheduler with anti-starvation exploration budget and exponential backoff.

    Allocates:
    - `exploration_ratio` (default 35%) strictly to `probe_status='unprobed'` boards
      rotated deterministically across providers (`hashtext(board_key)`) so unexplored
      employers are never starved and alphabetical bias is eliminated.
    - Remaining slots to highest deterministic priority score combining:
      newly added directory entries, verified official UK identity, historical
      early-career & UK technical yield, UK hints/domains, and staleness since last probe.
    - Exponential failure/dead backoff: `7d * 2^(min(probe_count-1, 3))` (7d -> 14d -> 28d -> 56d),
      and `active_non_uk` backoff `30d` (60d after 2+ probes).
    """
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValueError("Probe batch limit must be 1..500")
    if not 0.0 <= exploration_ratio <= 1.0:
        raise ValueError("exploration_ratio must be in [0.0, 1.0]")
    at = now or datetime.now(timezone.utc)
    iso(at)
    new_cutoff = at - timedelta(days=new_board_days)
    active_cutoff = at - timedelta(days=reprobe_active_days)
    non_uk_cutoff_1 = at - timedelta(days=reprobe_non_uk_days)
    non_uk_cutoff_2 = at - timedelta(days=reprobe_non_uk_days * 2)

    eligible_where = """
      b.provider = ANY(%s)
      AND b.source_id IS NULL
      AND NOT EXISTS (
        SELECT 1 FROM jobwatch.sources s
        WHERE s.provider = b.provider AND lower(s.board) = lower(b.board)
      )
      AND (
        b.last_probed_at IS NULL
        OR (b.probe_status = 'active_uk_relevant' AND b.last_probed_at < %s)
        OR (b.probe_status IN ('active_non_uk', 'empty') AND b.probe_count < 2 AND b.last_probed_at < %s)
        OR (b.probe_status IN ('active_non_uk', 'empty') AND b.probe_count >= 2 AND b.last_probed_at < %s)
        OR (b.probe_status IN ('dead', 'error') AND b.last_probed_at < (%s - (interval '7 days' * power(2, least(greatest(b.probe_count - 1, 0), 3)))))
      )
    """
    base_params = [list(providers), active_cutoff, non_uk_cutoff_1, non_uk_cutoff_2, at]

    explore_quota = int(round(limit * exploration_ratio))
    selected: list[dict] = []
    seen: set[str] = set()

    # 1. Reserved exploration budget: evenly balanced across providers via deterministic hash rotation.
    if explore_quota > 0 and providers:
        per_prov = max(1, (explore_quota + len(providers) - 1) // len(providers))
        prov_buckets: list[list[dict]] = []
        for prov in providers:
            rows = conn.execute(
                "SELECT b.board_key, b.provider, b.board, b.region, b.company_hint, b.uk_hint, "
                "b.probe_status, b.identity_status, b.directory_sources, 'exploration' AS selection_reason "
                "FROM jobwatch.discovered_boards b "
                "WHERE b.provider = %s AND b.source_id IS NULL AND b.last_probed_at IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM jobwatch.sources s WHERE s.provider = b.provider AND lower(s.board) = lower(b.board)) "
                "ORDER BY (b.first_discovered_at >= %s) DESC, abs(hashtext(b.board_key)), b.board_key LIMIT %s",
                (prov, new_cutoff, per_prov),
            ).fetchall()
            prov_buckets.append(rows)
        # Round-robin across providers up to explore_quota
        idx = 0
        while len(selected) < explore_quota and any(idx < len(bk) for bk in prov_buckets):
            for bk in prov_buckets:
                if idx < len(bk) and len(selected) < explore_quota:
                    r = bk[idx]
                    if r["board_key"] not in seen:
                        seen.add(r["board_key"])
                        selected.append(r)
            idx += 1

    # 2. Priority slots: multi-signal deterministic score.
    remaining = limit - len(selected)
    if remaining > 0:
        score_sql = """(
            CASE WHEN b.first_discovered_at >= %s THEN 30 ELSE 0 END
          + CASE WHEN b.identity_status = 'verified_official_link' THEN 25 ELSE 0 END
          + (b.priority_vacancies * 20)
          + (least(b.review_vacancies, 10) * 5)
          + CASE WHEN b.uk_hint THEN 20 ELSE 0 END
          + CASE WHEN b.uk_vacancies > 0 THEN 15 ELSE 0 END
          + CASE WHEN b.official_website ~* '\\.co\\.uk(/|$)|\\.uk(/|$)' THEN 20 ELSE 0 END
          + CASE WHEN b.last_probed_at IS NULL THEN 10
                 ELSE least(greatest(floor(extract(epoch from (%s - b.last_probed_at)) / 86400)::int, 0), 60) END
        )"""
        prio_rows = conn.execute(
            "SELECT b.board_key, b.provider, b.board, b.region, b.company_hint, b.uk_hint, "
            "b.probe_status, b.identity_status, b.directory_sources, "
            + score_sql + " AS priority_score, 'priority' AS selection_reason "
            "FROM jobwatch.discovered_boards b WHERE " + eligible_where +
            " ORDER BY priority_score DESC, abs(hashtext(b.board_key)), b.board_key LIMIT %s",
            [new_cutoff, at, *base_params, remaining + len(seen)],
        ).fetchall()
        for r in prio_rows:
            if r["board_key"] not in seen and len(selected) < limit:
                seen.add(r["board_key"])
                selected.append(r)
    return selected


def record_board_probe(
    conn,
    probe: BoardProbeResult,
    *,
    identity: IdentityCheckResult | None = None,
    observed_at: datetime | None = None,
    processing: Processing = Processing(),
) -> dict:
    """Persist board-level probe metrics, identity check, and UK candidate vacancies."""
    at = observed_at or datetime.now(timezone.utc)
    iso(at)
    with conn.transaction():
        src = conn.execute(
            "SELECT id FROM jobwatch.sources WHERE provider=%s AND lower(board)=lower(%s)",
            (probe.provider, probe.board),
        ).fetchone()
        source_id = src["id"] if src else None
        id_status = identity.status if identity else None
        id_at = at if (identity and identity.status != "not_checked") else None
        id_ev = safe(identity.evidence) if identity else None
        official_web = safe(identity.official_website) if (identity and identity.official_website) else safe(probe.hosted_website)
        careers_url = safe(identity.careers_url) if (identity and identity.careers_url) else ""

        conn.execute(
            "UPDATE jobwatch.discovered_boards SET "
            "region=%s, company_hint=coalesce(nullif(%s,''), company_hint), "
            "official_website=coalesce(nullif(%s,''), official_website), "
            "careers_url=coalesce(nullif(%s,''), careers_url), "
            "last_probed_at=%s, probe_count=probe_count+1, probe_status=%s, http_status=%s, "
            "total_vacancies=%s, uk_vacancies=%s, priority_vacancies=%s, review_vacancies=%s, "
            "custom_host_urls=%s, "
            "identity_status=coalesce(%s, identity_status), "
            "identity_checked_at=coalesce(%s, identity_checked_at), "
            "identity_evidence=coalesce(nullif(%s,''), identity_evidence), "
            "source_id=coalesce(%s, source_id) "
            "WHERE board_key=%s",
            (
                probe.region, safe(probe.company_name), official_web, careers_url,
                at, probe.probe_status, probe.http_status,
                probe.total_vacancies, probe.uk_vacancies, probe.priority_vacancies, probe.review_vacancies,
                probe.custom_host_urls,
                id_status, id_at, id_ev, source_id, probe.board_key,
            ),
        )

    vac_counts = {"inserted": 0, "updated": 0, "linked": 0, "verified": 0}
    if probe.vacancies:
        vac_counts = record_discoveries(conn, probe.vacancies, observed_at=at, processing=processing)
    return {"board_key": probe.board_key, "probe_status": probe.probe_status, "candidates": vac_counts}


def run_discovery_cycle(
    conn,
    *,
    refresh_directories: bool = True,
    probe_boards: bool = True,
    providers: tuple[str, ...] = ("greenhouse", "ashby", "lever"),
    directory_urls: dict[str, str] | None = None,
    max_boards: int = 40,
    max_requests: int = 80,
    max_bytes: int = 32 * 1024 * 1024,
    verify_identity_limit: int = 8,
    uk_hint_quota: int | None = None,
    delay_s: float = 0.5,
    get_bytes=default_http_get,
    pause=time.sleep,
    now: datetime | None = None,
    processing: Processing = Processing(),
) -> dict:
    """Execute one bounded recurring directory discovery cycle without activating any sources."""
    t0 = time.monotonic()
    at = now or datetime.now(timezone.utc)
    iso(at)
    summary = {
        "started_at": iso(at),
        "providers": list(providers),
        "directories": {},
        "boards_probed": 0,
        "status_counts": {},
        "candidates_inserted": 0,
        "candidates_updated": 0,
        "priority_vacancies": 0,
        "review_vacancies": 0,
        "identity_checks": {},
        "requests_made": 0,
        "bytes_read": 0,
        "halted_reason": None,
        "elapsed_s": 0.0,
    }

    if refresh_directories:
        for provider in providers:
            if summary["requests_made"] >= max_requests:
                summary["halted_reason"] = "max_requests_reached"
                break
            url = (directory_urls or {}).get(provider)
            try:
                ref = fetch_directory(provider, url=url, get_bytes=get_bytes, now=at)
            except DiscoveryRateLimitError:
                summary["halted_reason"] = "rate_limited_429"
                break
            summary["requests_made"] += 1
            summary["bytes_read"] += ref.bytes_read
            sync_counts = sync_directory_boards(
                conn,
                ref.boards,
                directory_source=ref.directory_source,
                provenance={"url": ref.directory_url, "sha256": ref.sha256, "bytes": ref.bytes_read, "fetched_at": ref.fetched_at},
                observed_at=at,
            )
            summary["directories"][provider] = {**sync_counts, "sha256": ref.sha256, "skipped_invalid": ref.skipped_invalid}

    if probe_boards and not summary["halted_reason"]:
        batch = select_probe_batch(conn, limit=max_boards, providers=providers, uk_hint_quota=uk_hint_quota, now=at)
        id_checks_done = 0
        for idx, row in enumerate(batch):
            if summary["requests_made"] >= max_requests:
                summary["halted_reason"] = "max_requests_reached"
                break
            if summary["bytes_read"] >= max_bytes:
                summary["halted_reason"] = "max_bytes_reached"
                break
            if idx > 0 and delay_s > 0:
                pause(delay_s)
            dir_src = (row["directory_sources"] or ["ats_directory"])[0]
            try:
                probe = probe_ats_board(
                    row["provider"],
                    row["board"],
                    company_hint=row["company_hint"],
                    directory_source=dir_src,
                    get_bytes=get_bytes,
                    now=at,
                )
            except DiscoveryRateLimitError:
                summary["halted_reason"] = "rate_limited_429"
                break
            summary["requests_made"] += probe.requests_made
            summary["bytes_read"] += probe.bytes_read
            summary["boards_probed"] += 1
            summary["status_counts"][probe.probe_status] = summary["status_counts"].get(probe.probe_status, 0) + 1
            summary["priority_vacancies"] += probe.priority_vacancies
            summary["review_vacancies"] += probe.review_vacancies

            identity = None
            if (
                probe.probe_status == "active_uk_relevant"
                and row["identity_status"] == "not_checked"
                and id_checks_done < verify_identity_limit
                and summary["requests_made"] + 2 <= max_requests
            ):
                if delay_s > 0:
                    pause(delay_s)
                try:
                    identity = verify_board_identity(
                        probe.provider,
                        probe.board,
                        region=probe.region,
                        hosted_website=probe.hosted_website,
                        sample_posting_id=probe.sample_posting_id,
                        get_bytes=get_bytes,
                        now=at,
                    )
                except DiscoveryRateLimitError:
                    summary["halted_reason"] = "rate_limited_429"
                    record_board_probe(conn, probe, observed_at=at, processing=processing)
                    break
                id_checks_done += 1
                summary["requests_made"] += identity.requests_made
                summary["bytes_read"] += identity.bytes_read
                summary["identity_checks"][identity.status] = summary["identity_checks"].get(identity.status, 0) + 1

            rec = record_board_probe(conn, probe, identity=identity, observed_at=at, processing=processing)
            summary["candidates_inserted"] += rec["candidates"]["inserted"]
            summary["candidates_updated"] += rec["candidates"]["updated"]

    summary["elapsed_s"] = round(time.monotonic() - t0, 2)
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Bounded recurring ATS directory discovery (never activates sources)")
    parser.add_argument("--providers", default="greenhouse,ashby,lever")
    parser.add_argument("--max-boards", type=int, default=40)
    parser.add_argument("--max-requests", type=int, default=80)
    parser.add_argument("--max-bytes", type=int, default=32 * 1024 * 1024)
    parser.add_argument("--verify-limit", type=int, default=8)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--no-refresh", action="store_true")
    parser.add_argument("--refresh-only", action="store_true")
    args = parser.parse_args(argv)
    if args.no_refresh and args.refresh_only:
        parser.error("Cannot combine --no-refresh and --refresh-only")
    providers = tuple(p.strip() for p in args.providers.split(",") if p.strip())
    cfg = DatabaseConfig.from_env()
    with connect(cfg) as conn:
        result = run_discovery_cycle(
            conn,
            refresh_directories=not args.no_refresh,
            probe_boards=not args.refresh_only,
            providers=providers,
            max_boards=args.max_boards,
            max_requests=args.max_requests,
            max_bytes=args.max_bytes,
            verify_identity_limit=args.verify_limit,
            delay_s=args.delay,
        )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
