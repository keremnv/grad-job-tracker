"""Bounded multi-provider directory discovery experiment (Greenhouse, Ashby, Lever).

Uses open-source ATS board directories (`kalil0321/ats-scrapers` MIT) to run two
explicitly separated bounded live probes:
  1. `manual_watch_matches`: Live validation & UK graduate screening of existing
     manual-watch employers whose ATS slugs appear in public directories.
  2. `unknown_directory_sample`: Bounded live collection of previously unknown
     boards from `ats-scrapers` (both a UK-heuristic stratum and a pure random
     baseline stratum) to measure real UK graduate/early-career yield, dead/empty
     rates, custom-host frequency, network bytes, wall time, and memory.

Screens every returned vacancy with unchanged `jobwatch.filtering.screen()`.
No production writes. Strict request budgets and polite pacing.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import random
import re
import resource
import sys
import time
import tracemalloc
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from jobwatch import feeds, filtering

USER_AGENT = "PersonalGradJobWatch/0.1 (bounded directory discovery research; read-only)"
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

UK_NAME_HINT = re.compile(
    r"\b(?:uk|london|cambridge|oxford|manchester|edinburgh|bristol|leeds|glasgow|britain|british|england|scotland|wales)\b|(?:\.co\.uk|\buk$)",
    re.I,
)

# Existing manual-watch employers matched in public ATS directories (excluding Wise US false-collision)
MANUAL_WATCH_TARGETS = (
    ("Autotrader", "greenhouse", "autotrader", "eu"),
    ("Canonical", "greenhouse", "canonical", "global"),
    ("DV Trading", "greenhouse", "dvtrading", "global"),
    ("Faculty", "ashby", "faculty", "global"),
    ("Graphcore", "greenhouse", "graphcore", "global"),
    ("Jane Street", "greenhouse", "janestreet", "global"),
    ("Ocado Group", "greenhouse", "ocadogroup", "eu"),
    ("Quantexa", "ashby", "quantexa", "global"),
    ("Snyk", "ashby", "snyk", "global"),
    ("Stripe", "greenhouse", "stripe", "global"),
    ("Thought Machine", "ashby", "thought-machine", "global"),
)


def _norm_company(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").casefold())


def _http_get_bytes(url: str, *, timeout: int = 20) -> tuple[int, bytes]:
    req = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=timeout) as r:
            raw = r.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                return -1, b""
            return r.status, raw
    except HTTPError as exc:
        return exc.code, b""
    except (URLError, TimeoutError):
        return 0, b""


def fetch_board_raw(provider: str, slug: str, *, include_content: bool = True) -> dict:
    """Fetch one public ATS board and normalize records for screening (tolerant of custom URLs)."""
    t0 = time.monotonic()
    if provider == "greenhouse":
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs" + ("?content=true" if include_content else "")
    elif provider == "ashby":
        url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    elif provider == "lever":
        url = f"https://api.lever.co/v0/postings/{slug}?mode=json&limit=100"
    else:
        raise ValueError(f"unsupported provider: {provider}")

    status, raw = _http_get_bytes(url)
    elapsed = round(time.monotonic() - t0, 3)
    if status != 200:
        return {"provider": provider, "board": slug, "status": status, "bytes": len(raw), "elapsed_s": elapsed, "jobs": []}

    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"provider": provider, "board": slug, "status": -2, "bytes": len(raw), "elapsed_s": elapsed, "jobs": []}

    parsed = []
    custom_host_urls = 0
    if provider == "greenhouse":
        raw_jobs = data.get("jobs") or []
        for j in raw_jobs:
            if not isinstance(j, dict) or j.get("internal_job_id") is None:
                continue
            link = j.get("absolute_url") or ""
            if link and "greenhouse.io" not in link:
                custom_host_urls += 1
            locs = [(j.get("location") or {}).get("name") or ""]
            for off in j.get("offices") or []:
                if isinstance(off, dict):
                    locs += [off.get("name") or "", off.get("location") or ""]
            depts = [d.get("name") or "" for d in (j.get("departments") or []) if isinstance(d, dict)]
            parsed.append({
                "external_id": str(j.get("id") or ""),
                "title": j.get("title") or "",
                "location": ", ".join(dict.fromkeys(x for x in locs if x)),
                "country": "",
                "countries": [],
                "department": ", ".join(dict.fromkeys(x for x in depts if x)),
                "team": "",
                "employment_type": "",
                "description": j.get("content") if include_content else None,
                "url": link,
                "published_at": j.get("first_published") or j.get("updated_at") or "",
            })
    elif provider == "ashby":
        raw_jobs = data.get("jobs") or []
        for j in raw_jobs:
            if not isinstance(j, dict) or j.get("isListed") is False:
                continue
            addr = ((j.get("address") or {}).get("postalAddress") or {})
            countries = [addr.get("addressCountry") or ""]
            locs = [j.get("location") or ""]
            for sec in j.get("secondaryLocations") or []:
                if isinstance(sec, dict):
                    locs.append(sec.get("location") or "")
                    countries.append(((sec.get("address") or {}).get("addressCountry") or ""))
            countries = [c for c in dict.fromkeys(countries) if c]
            parsed.append({
                "external_id": str(j.get("id") or ""),
                "title": j.get("title") or "",
                "location": ", ".join(dict.fromkeys(x for x in locs if x)),
                "country": countries[0] if countries else "",
                "countries": countries,
                "department": j.get("department") or "",
                "team": j.get("team") or "",
                "employment_type": j.get("employmentType") or "",
                "description": j.get("descriptionPlain") or "",
                "url": j.get("jobUrl") or j.get("applyUrl") or "",
                "published_at": j.get("publishedAt") or "",
            })
    elif provider == "lever":
        raw_jobs = data if isinstance(data, list) else []
        for j in raw_jobs:
            if not isinstance(j, dict):
                continue
            cats = j.get("categories") or {}
            locs = [cats.get("location") or ""] + list(cats.get("allLocations") or [])
            country = j.get("country") or ""
            desc_parts = [j.get("descriptionPlain") or ""]
            for lst in j.get("lists") or []:
                if isinstance(lst, dict) and lst.get("content"):
                    desc_parts.append(str(lst["content"]))
            desc_parts.append(j.get("additionalPlain") or "")
            parsed.append({
                "external_id": str(j.get("id") or ""),
                "title": j.get("text") or "",
                "location": ", ".join(dict.fromkeys(x for x in locs if x)),
                "country": country,
                "countries": [country] if country else [],
                "department": cats.get("department") or "",
                "team": cats.get("team") or "",
                "employment_type": cats.get("commitment") or "",
                "description": "\n".join(p for p in desc_parts if p),
                "url": j.get("hostedUrl") or j.get("applyUrl") or "",
                "published_at": str(j.get("createdAt") or ""),
            })

    return {
        "provider": provider,
        "board": slug,
        "status": status,
        "bytes": len(raw),
        "elapsed_s": elapsed,
        "jobs": parsed,
        "custom_host_urls": custom_host_urls,
        "truncated_lever_page": provider == "lever" and len(parsed) == 100,
    }


def run_directory_experiment(
    *,
    uk_hint_per_provider: int = 12,
    random_per_provider: int = 10,
    delay_s: float = 0.4,
    seed: int = 20261124,
    prod_snapshot_path: Path | None = None,
) -> dict:
    tracemalloc.start()
    t0 = time.monotonic()
    rng = random.Random(seed)
    now = datetime.now(timezone.utc)

    prod_known_boards = set()
    prod_known_companies = set()
    if prod_snapshot_path and prod_snapshot_path.is_file():
        prod = json.loads(prod_snapshot_path.read_text())
        prod_known_boards = {(s["provider"], s["board"].lower()) for s in prod.get("sources", [])}
        prod_known_companies = {_norm_company(e["name"]) for e in prod.get("employers", [])}

    # 1. Probe the 11 existing manual-watch employers matched in public ATS directories
    manual_watch_results = []
    manual_watch_candidates = []
    total_requests = 0
    total_bytes = 0

    for company, provider, slug, _region in MANUAL_WATCH_TARGETS:
        res = fetch_board_raw(provider, slug, include_content=True)
        total_requests += 1
        total_bytes += res["bytes"]
        verdicts = {"priority": 0, "review": 0, "excluded": 0}
        for job in res["jobs"]:
            scr = filtering.screen(job)
            verdicts[scr["verdict"]] += 1
            if scr["verdict"] in ("priority", "review"):
                manual_watch_candidates.append({
                    "company": company,
                    "provider": provider,
                    "board": slug,
                    "title": job["title"],
                    "location": job["location"],
                    "url": job["url"],
                    "published_at": job["published_at"],
                    "verdict": scr["verdict"],
                    "family": scr["family"],
                    "codes": scr["codes"],
                })
        manual_watch_results.append({
            "company": company,
            "provider": provider,
            "board": slug,
            "http_status": res["status"],
            "bytes": res["bytes"],
            "elapsed_s": res["elapsed_s"],
            "jobs": len(res["jobs"]),
            "custom_host_urls": res.get("custom_host_urls", 0),
            "verdicts": verdicts,
        })
        time.sleep(delay_s)

    # 2. Load ats-scrapers CSVs and sample unknown boards (UK-hinted + pure random)
    strata_summary = {}
    unknown_candidates = []
    unknown_employers_with_uk_relevant = set()

    for provider in ("greenhouse", "ashby", "lever"):
        csv_url = f"https://raw.githubusercontent.com/kalil0321/ats-scrapers/main/ats-companies/{provider}.csv"
        st, raw = _http_get_bytes(csv_url)
        if st != 200:
            raise RuntimeError(f"Failed to fetch ats-scrapers {provider}.csv: HTTP {st}")
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8", "replace"))))
        eligible = [
            r for r in rows
            if r.get("slug")
            and (provider, r["slug"].lower()) not in prod_known_boards
            and _norm_company(r.get("name", "")) not in prod_known_companies
        ]
        uk_pool = [r for r in eligible if UK_NAME_HINT.search(f"{r.get('name','')} {r.get('slug','')}")]
        uk_sample = rng.sample(uk_pool, min(uk_hint_per_provider, len(uk_pool)))
        uk_slugs = {r["slug"].lower() for r in uk_sample}
        rand_pool = [r for r in eligible if r["slug"].lower() not in uk_slugs]
        rand_sample = rng.sample(rand_pool, min(random_per_provider, len(rand_pool)))

        for stratum_name, sample in (("uk_hint", uk_sample), ("random", rand_sample)):
            key = f"{provider}:{stratum_name}"
            active = empty = dead = err = total_jobs = custom_host_boards = 0
            verdicts = {"priority": 0, "review": 0, "excluded": 0}
            stratum_bytes = 0
            for r in sample:
                slug = r["slug"]
                company = r.get("name") or slug
                res = fetch_board_raw(provider, slug, include_content=True)
                total_requests += 1
                total_bytes += res["bytes"]
                stratum_bytes += res["bytes"]
                if res["status"] == 200:
                    if res["jobs"]:
                        active += 1
                        total_jobs += len(res["jobs"])
                        if res.get("custom_host_urls", 0) > 0:
                            custom_host_boards += 1
                        for job in res["jobs"]:
                            scr = filtering.screen(job)
                            verdicts[scr["verdict"]] += 1
                            if scr["verdict"] in ("priority", "review"):
                                unknown_employers_with_uk_relevant.add((provider, slug, company))
                                unknown_candidates.append({
                                    "stratum": key,
                                    "company": company,
                                    "provider": provider,
                                    "board": slug,
                                    "title": job["title"],
                                    "location": job["location"],
                                    "url": job["url"],
                                    "published_at": job["published_at"],
                                    "verdict": scr["verdict"],
                                    "family": scr["family"],
                                    "codes": scr["codes"],
                                })
                    else:
                        empty += 1
                elif res["status"] == 404:
                    dead += 1
                else:
                    err += 1
                time.sleep(delay_s)

            strata_summary[key] = {
                "directory_total": len(rows),
                "uk_hint_pool_size": len(uk_pool),
                "sampled": len(sample),
                "active_nonempty": active,
                "empty_200": empty,
                "dead_404": dead,
                "other_errors": err,
                "custom_host_boards": custom_host_boards,
                "total_jobs": total_jobs,
                "bytes": stratum_bytes,
                "verdicts": verdicts,
            }

    elapsed_s = round(time.monotonic() - t0, 2)
    _, peak_py_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    # Sort candidates: priority first, then early-title signal
    def _rank(c):
        return (
            0 if c["verdict"] == "priority" else 1,
            0 if "level.early_title" in c["codes"] else 1,
            c["company"].casefold(),
            c["title"].casefold(),
        )

    manual_watch_candidates.sort(key=_rank)
    unknown_candidates.sort(key=_rank)

    return {
        "experiment": "multi_provider_directory_discovery",
        "executed_at_utc": now.isoformat().replace("+00:00", "Z"),
        "seed": seed,
        "telemetry": {
            "elapsed_s": elapsed_s,
            "total_requests": total_requests,
            "total_bytes": total_bytes,
            "peak_python_bytes": peak_py_bytes,
            "peak_rss_kb": rss_kb,
        },
        "manual_watch_boards": manual_watch_results,
        "manual_watch_relevant_count": len(manual_watch_candidates),
        "manual_watch_priority_count": sum(1 for c in manual_watch_candidates if c["verdict"] == "priority"),
        "manual_watch_top_candidates": manual_watch_candidates[:30],
        "unknown_strata_summary": strata_summary,
        "unknown_employers_with_relevant_roles": len(unknown_employers_with_uk_relevant),
        "unknown_relevant_count": len(unknown_candidates),
        "unknown_priority_count": sum(1 for c in unknown_candidates if c["verdict"] == "priority"),
        "unknown_top_candidates": unknown_candidates[:30],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded multi-provider directory discovery experiment")
    parser.add_argument("--uk-hint-per-provider", type=int, default=12)
    parser.add_argument("--random-per-provider", type=int, default=10)
    parser.add_argument("--delay", type=float, default=0.4)
    parser.add_argument("--prod-snapshot", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    result = run_directory_experiment(
        uk_hint_per_provider=args.uk_hint_per_provider,
        random_per_provider=args.random_per_provider,
        delay_s=args.delay,
        prod_snapshot_path=args.prod_snapshot,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({
        "telemetry": result["telemetry"],
        "manual_watch_relevant": result["manual_watch_relevant_count"],
        "manual_watch_priority": result["manual_watch_priority_count"],
        "unknown_strata_summary": result["unknown_strata_summary"],
        "unknown_relevant": result["unknown_relevant_count"],
        "unknown_priority": result["unknown_priority_count"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
