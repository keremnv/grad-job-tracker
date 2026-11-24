"""Bounded, reproducible Workable provider-wide UK graduate tech discovery experiment.

Searches Workable's public cross-employer job search endpoint
(https://jobs.workable.com/api/v1/jobs) using general UK graduate and
early-career technology queries — without seeding any employer name or posting
identifier — screens every hit with Graduate Job Watch's unchanged deterministic
screener (`jobwatch.filtering.screen`), and resolves candidate postings to their
official `apply.workable.com` board slug and native shortcode via Workable's
public widget API (`https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true`).

Held-out acceptance evaluation is performed strictly *after* discovery finishes
and never influences queries, pagination, or widget resolution.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import resource
import sys
import time
import tracemalloc
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

# Allow running directly as a script from repo root.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from jobwatch import feeds, filtering
from jobwatch.postgres.collection import normalize_v1
from jobwatch.postgres.evidence import encode_evidence

USER_AGENT = "PersonalGradJobWatch/0.1 (bounded discovery feasibility research; read-only)"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
SHORTCODE_RE = re.compile(r"^[A-Za-z0-9_-]{4,32}$")
ACCOUNT_SLUG_RE = re.compile(r"^[A-Za-z0-9_-]+$")

# General, domain-standard UK graduate / early-career tech search queries.
# Deliberately contains NO employer names and NO posting identifiers.
DEFAULT_QUERIES: tuple[dict, ...] = (
    {"query": "graduate software engineer", "location": "United Kingdom", "max_pages": 2},
    {"query": "graduate ai", "location": "United Kingdom", "max_pages": 2},
    {"query": "graduate machine learning", "location": "United Kingdom", "max_pages": 2},
    {"query": "graduate data science", "location": "United Kingdom", "max_pages": 2},
    {"query": "graduate developer", "location": "United Kingdom", "max_pages": 2},
    {"query": "junior software engineer", "location": "United Kingdom", "max_pages": 2},
    {"query": "junior developer", "location": "United Kingdom", "max_pages": 2},
    {"query": "associate software engineer", "location": "United Kingdom", "max_pages": 2},
    {"query": "research engineer", "location": "United Kingdom", "max_pages": 1},
    {"query": "graduate", "location": "United Kingdom", "day_range": "14", "max_pages": 2},
)

GRAD_SWE_RE = re.compile(
    r"\b(?:graduate|grad|new[ -]grad|entry[ -]level)\b.*\b(?:software|developer|swe|backend|full[ -]?stack|platform|devops|cloud|systems)\b"
    r"|\b(?:software|developer|swe|backend|full[ -]?stack|platform)\b.*\b(?:graduate|grad|new[ -]grad)\b",
    re.I,
)
GRAD_AI_ML_RE = re.compile(
    r"\b(?:graduate|grad|new[ -]grad|entry[ -]level|junior|associate)\b.*\b(?:ai|artificial intelligence|machine learning|ml|mlops|data scien\w*|research engineer|nlp|computer vision)\b"
    r"|\b(?:ai|artificial intelligence|machine learning|ml|data scien\w*|research engineer)\b.*\b(?:graduate|grad|new[ -]grad|junior|associate)\b",
    re.I,
)
JUNIOR_NON_GRAD_RE = re.compile(
    r"\b(?:junior|associate|entry[ -]level)\b",
    re.I,
)
EXPLICIT_GRAD_RE = re.compile(r"\b(?:graduate|new[ -]grad)\b", re.I)


@dataclass
class HttpTelemetry:
    requests: int = 0
    bytes_transferred: int = 0
    http_errors: dict[str, int] | None = None
    rate_limited: bool = False

    def __post_init__(self):
        if self.http_errors is None:
            self.http_errors = {}


def fetch_json(url: str, *, timeout: int = 25, telemetry: HttpTelemetry | None = None) -> tuple[int, dict]:
    req = Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT})
    if telemetry is not None:
        telemetry.requests += 1
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError("response exceeded 4 MiB safety bound")
            if telemetry is not None:
                telemetry.bytes_transferred += len(raw)
            return resp.status, json.loads(raw.decode("utf-8"))
    except HTTPError as exc:
        if telemetry is not None:
            key = str(exc.code)
            telemetry.http_errors[key] = telemetry.http_errors.get(key, 0) + 1
            if exc.code == 429:
                telemetry.rate_limited = True
        return exc.code, {}
    except (URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        if telemetry is not None:
            key = type(exc).__name__
            telemetry.http_errors[key] = telemetry.http_errors.get(key, 0) + 1
        return 0, {}


def _norm_company(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").casefold())


def _norm_title(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "").strip()).casefold()


def search_hit_to_screening_inputs(job: dict) -> dict:
    """Convert a jobs.workable.com/api/v1/jobs item into jobwatch.filtering.screen inputs."""
    locations = [loc for loc in (job.get("locations") or []) if isinstance(loc, str) and loc.strip()]
    loc_obj = job.get("location") if isinstance(job.get("location"), dict) else {}
    country = (loc_obj.get("countryName") or "").strip()
    countries = [country] if country else []
    location_parts = list(locations)
    city = (loc_obj.get("city") or "").strip()
    subregion = (loc_obj.get("subregion") or "").strip()
    if city and not any(city.lower() in part.lower() for part in location_parts):
        location_parts.append(city)
    if subregion and not any(subregion.lower() in part.lower() for part in location_parts):
        location_parts.append(subregion)
    if country and not any(country.lower() in part.lower() for part in location_parts):
        location_parts.append(country)

    desc_parts = [
        job.get("description") or "",
        job.get("requirementsSection") or "",
        job.get("benefitsSection") or "",
    ]
    description = "\n".join(part for part in desc_parts if isinstance(part, str) and part.strip())
    return {
        "title": (job.get("title") or "").strip(),
        "location": ", ".join(dict.fromkeys(location_parts)),
        "country": country,
        "countries": countries,
        "department": (job.get("department") or "").strip(),
        "team": "",
        "employment_type": (job.get("employmentType") or "").strip(),
        "description": description,
    }


def candidate_account_slugs(company: dict) -> list[str]:
    """Derive bounded candidate Workable widget account slugs from a jobs.workable.com company object.

    These are *candidates only*; a slug is accepted only when the live widget
    API confirms both company-name correspondence and an exact posting match.
    """
    candidates: list[str] = []
    company_url = (company.get("url") or "").strip()
    match = re.search(r"/jobs-at-([a-z0-9-]+)$", company_url)
    if match:
        slug = match.group(1)
        candidates.append(slug)
        if "-" in slug:
            candidates.append(slug.replace("-", ""))
    website = (company.get("website") or "").strip()
    if website.startswith(("http://", "https://")):
        host = (urlsplit(website).hostname or "").lower()
        host = host.removeprefix("www.")
        stem = host.split(".")[0] if host else ""
        if stem and ACCOUNT_SLUG_RE.fullmatch(stem):
            candidates.append(stem)
    return [c for c in dict.fromkeys(candidates) if ACCOUNT_SLUG_RE.fullmatch(c)]


def widget_job_to_posting(account: str, company_name: str, item: dict) -> feeds.Posting:
    """Map one apply.workable.com widget job item to Graduate Job Watch's feeds.Posting contract."""
    if not ACCOUNT_SLUG_RE.fullmatch(account):
        raise ValueError("invalid Workable account slug")
    shortcode = str(item.get("shortcode") or "").strip()
    if not SHORTCODE_RE.fullmatch(shortcode):
        raise ValueError("invalid Workable posting shortcode")
    url = (item.get("url") or item.get("shortlink") or "").strip()
    if not feeds.public_url(url):
        raise ValueError("invalid Workable posting URL")
    parsed_url = urlsplit(url)
    parts = parsed_url.path.strip("/").split("/")
    valid_short = parsed_url.hostname == "apply.workable.com" and parts == ["j", shortcode]
    valid_account = parsed_url.hostname == "apply.workable.com" and parts == [account, "j", shortcode]
    if not (valid_short or valid_account):
        raise ValueError("Workable posting URL does not match shortcode/account contract")

    title = (item.get("title") or "").strip()
    if not title:
        raise ValueError("empty Workable title")

    locations: list[str] = []
    countries: list[str] = []
    for key in ("city", "state", "country"):
        val = (item.get(key) or "").strip()
        if val:
            locations.append(val)
    if (item.get("country") or "").strip():
        countries.append(item["country"].strip())
    for loc in item.get("locations") or []:
        if not isinstance(loc, dict):
            continue
        loc_parts = [str(loc.get(k) or "").strip() for k in ("city", "region", "country") if (loc.get(k) or "").strip()]
        if loc_parts:
            locations.append(", ".join(loc_parts))
        for ck in ("country", "countryCode"):
            cval = (loc.get(ck) or "").strip()
            if cval:
                countries.append(cval)
    if item.get("telecommuting") is True:
        locations.append("Remote")

    source_dates = {}
    for k in ("published_on", "created_at"):
        if item.get(k):
            source_dates[k] = str(item[k])

    return feeds.Posting(
        company=company_name.strip() or account,
        provider="workable",
        board=account,
        external_id=shortcode,
        title=title,
        location=", ".join(dict.fromkeys(locations)),
        url=url,
        description=item.get("description") or "",
        published_at=str(item.get("published_on") or ""),
        salary="",
        country=countries[0] if countries else "",
        countries=tuple(dict.fromkeys(countries)),
        source_dates=source_dates,
        raw=item,
        department=(item.get("department") or "").strip(),
        team=(item.get("function") or "").strip(),
        employment_type=(item.get("employment_type") or "").strip(),
    )


def group_widget_jobs_by_shortcode(jobs: list[dict]) -> list[dict]:
    """Merge multi-location Workable widget entries that share a single shortcode.

    Workable's public widget API emits one array item per country/location for
    multi-location postings (for example, UK + Ireland) with identical
    `shortcode`, `title`, `url`, and `description`. Grouping by shortcode
    preserves every raw location record while keeping one canonical posting
    identity (`workable:{account}:{shortcode}`).
    """
    grouped: dict[str, list[dict]] = {}
    order: list[str] = []
    for item in jobs:
        if not isinstance(item, dict):
            continue
        sc = str(item.get("shortcode") or "").strip()
        if not sc:
            continue
        if sc not in grouped:
            grouped[sc] = []
            order.append(sc)
        grouped[sc].append(item)

    merged: list[dict] = []
    for sc in order:
        entries = grouped[sc]
        if len(entries) == 1:
            merged.append(entries[0])
            continue
        base = dict(entries[0])
        # Prefer a GB/UK entry as primary display fields when present, while retaining ALL locations.
        for ent in entries:
            if (ent.get("country") or "").strip().lower() in ("united kingdom", "uk", "gb"):
                base = dict(ent)
                break
        all_locations: list[dict] = []
        seen_loc_keys: set[tuple] = set()
        for ent in entries:
            locs = ent.get("locations") or []
            if locs:
                for loc in locs:
                    if not isinstance(loc, dict):
                        continue
                    lk = (
                        str(loc.get("country") or ""),
                        str(loc.get("countryCode") or ""),
                        str(loc.get("region") or ""),
                        str(loc.get("city") or ""),
                    )
                    if lk not in seen_loc_keys:
                        seen_loc_keys.add(lk)
                        all_locations.append(loc)
            elif ent.get("country") or ent.get("city"):
                loc = {
                    "country": ent.get("country") or "",
                    "city": ent.get("city") or "",
                    "region": ent.get("state") or "",
                }
                lk = (loc["country"], "", loc["region"], loc["city"])
                if lk not in seen_loc_keys:
                    seen_loc_keys.add(lk)
                    all_locations.append(loc)
        base["locations"] = all_locations
        base["_widget_multi_location_entries"] = entries
        merged.append(base)
    return merged


def match_widget_posting(
    search_job: dict,
    account: str,
    widget_payload: dict,
) -> tuple[dict | None, str]:
    """Verify whether a widget account payload contains the exact search_job posting."""
    company_title = ((search_job.get("company") or {}).get("title") or "").strip()
    widget_name = (widget_payload.get("name") or "").strip()
    target_title = _norm_title(search_job.get("title") or "")
    target_date = (search_job.get("created") or "")[:10]

    # Check company name compatibility (allow prefix/substring for legal suffixes like Ltd/Inc)
    nc, nw = _norm_company(company_title), _norm_company(widget_name)
    name_ok = bool(nc and nw and (nc == nw or nc in nw or nw in nc))
    if not name_ok:
        return None, "company_name_mismatch"

    grouped_jobs = group_widget_jobs_by_shortcode(widget_payload.get("jobs") or [])
    candidates = [
        wj for wj in grouped_jobs
        if _norm_title(wj.get("title") or "") == target_title
    ]
    if not candidates:
        return None, "title_not_in_widget"
    if len(candidates) == 1:
        basis = (
            "exact_title_company_merged_locations"
            if "_widget_multi_location_entries" in candidates[0]
            else "exact_title_and_company"
        )
        return candidates[0], basis

    # Disambiguate multiple distinct-shortcode same-title postings by created/published date
    dated = [
        wj for wj in candidates
        if target_date and target_date in {str(wj.get("created_at") or "")[:10], str(wj.get("published_on") or "")[:10]}
    ]
    if len(dated) == 1:
        return dated[0], "exact_title_company_and_date"
    return None, "ambiguous_multiple_matches"


def classify_buckets(job: dict, screening: dict, now: datetime) -> list[str]:
    buckets: list[str] = []
    title = job.get("title") or ""
    if screening["verdict"] != "excluded":
        if GRAD_SWE_RE.search(title):
            buckets.append("grad_swe")
        if GRAD_AI_ML_RE.search(title):
            buckets.append("grad_ai_ml_research")
        if JUNIOR_NON_GRAD_RE.search(title) and not EXPLICIT_GRAD_RE.search(title):
            buckets.append("junior_associate_non_grad_wording")
        created_str = job.get("created") or ""
        try:
            created = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
            if now - created <= timedelta(days=14):
                buckets.append("recent_14d")
        except ValueError:
            pass
    return buckets


def evaluate_held_out_control(
    discovered_jobs: dict[str, dict],
    resolutions: dict[str, dict],
    *,
    expected_company: str = "Stalis",
    expected_shortcode: str = "1C9CCEA6B4",
) -> dict:
    """Post-hoc held-out acceptance evaluation (never used during discovery)."""
    matched_search = None
    for jid, rec in discovered_jobs.items():
        comp = ((rec["raw"].get("company") or {}).get("title") or "").strip()
        if comp.casefold() == expected_company.casefold() and "data science" in (rec["raw"].get("title") or "").casefold():
            matched_search = rec
            break

    matched_widget = None
    for jid, res in resolutions.items():
        if (res.get("shortcode") or "").upper() == expected_shortcode.upper():
            matched_widget = res
            break

    return {
        "control_employer": expected_company,
        "control_shortcode": expected_shortcode,
        "discovered_via_search": matched_search is not None,
        "search_record": (
            {
                "workable_uuid": matched_search["raw"].get("id"),
                "title": matched_search["raw"].get("title"),
                "company": (matched_search["raw"].get("company") or {}).get("title"),
                "url": matched_search["raw"].get("url"),
                "created": matched_search["raw"].get("created"),
                "locations": matched_search["raw"].get("locations"),
                "matched_queries": matched_search["matched_queries"],
                "screening": matched_search["screening"],
            }
            if matched_search
            else None
        ),
        "resolved_to_shortcode": matched_widget is not None,
        "widget_resolution": matched_widget,
        "passed": matched_search is not None and matched_widget is not None,
    }


def run_experiment(
    *,
    queries: tuple[dict, ...] = DEFAULT_QUERIES,
    max_search_requests: int = 20,
    max_widget_requests: int = 35,
    max_resolve_candidates: int = 25,
    search_delay_s: float = 0.5,
    widget_delay_s: float = 1.5,
    prod_snapshot_path: Path | None = None,
    get_json: Callable = fetch_json,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict:
    tracemalloc.start()
    t0 = time.monotonic()
    now = datetime.now(timezone.utc)

    search_tel = HttpTelemetry()
    widget_tel = HttpTelemetry()

    raw_hits = 0
    duplicate_hits = 0
    discovered: dict[str, dict] = {}
    query_stats: list[dict] = []

    for qspec in queries:
        if search_tel.requests >= max_search_requests or search_tel.rate_limited:
            break
        base_params = {k: v for k, v in qspec.items() if k != "max_pages"}
        max_pages = int(qspec.get("max_pages", 1))
        token = None
        pages_fetched = 0
        q_hits = 0
        q_new = 0
        total_size = None

        for _ in range(max_pages):
            if search_tel.requests >= max_search_requests or search_tel.rate_limited:
                break
            params = dict(base_params)
            if token:
                params["pageToken"] = token
            url = "https://jobs.workable.com/api/v1/jobs?" + urlencode(params)
            if search_tel.requests > 0:
                sleep_fn(search_delay_s)
            status, payload = get_json(url, telemetry=search_tel)
            if status != 200 or not isinstance(payload, dict):
                break
            pages_fetched += 1
            total_size = payload.get("totalSize")
            jobs = payload.get("jobs") or []
            for job in jobs:
                if not isinstance(job, dict) or not job.get("id"):
                    continue
                raw_hits += 1
                q_hits += 1
                jid = str(job["id"])
                qlabel = base_params.get("query", "") + (f" (day_range={base_params['day_range']})" if "day_range" in base_params else "")
                if jid in discovered:
                    duplicate_hits += 1
                    if qlabel not in discovered[jid]["matched_queries"]:
                        discovered[jid]["matched_queries"].append(qlabel)
                    continue
                q_new += 1
                inputs = search_hit_to_screening_inputs(job)
                screening = filtering.screen(inputs)
                buckets = classify_buckets(job, screening, now)
                discovered[jid] = {
                    "raw": job,
                    "screening": screening,
                    "buckets": buckets,
                    "matched_queries": [qlabel],
                }
            token = payload.get("nextPageToken")
            if not token or not jobs:
                break

        query_stats.append({
            "params": base_params,
            "pages_fetched": pages_fetched,
            "reported_total_size": total_size,
            "hits_returned": q_hits,
            "new_unique_jobs": q_new,
        })

    # Rank candidate jobs for bounded official widget resolution:
    # Priority first, then review with early-career/grad signal, newest first.
    relevant_ids = [
        jid for jid, rec in discovered.items()
        if rec["screening"]["verdict"] in ("priority", "review")
    ]
    relevant_ids.sort(
        key=lambda jid: (
            0 if discovered[jid]["screening"]["verdict"] == "priority" else 1,
            0 if any(b in discovered[jid]["buckets"] for b in ("grad_swe", "grad_ai_ml_research", "junior_associate_non_grad_wording")) else 1,
            -(datetime.fromisoformat((discovered[jid]["raw"].get("created") or "1970-01-01T00:00:00Z").replace("Z", "+00:00")).timestamp()),
            jid,
        )
    )

    widget_cache: dict[str, tuple[int, dict]] = {}
    resolutions: dict[str, dict] = {}

    for jid in relevant_ids[:max_resolve_candidates]:
        if widget_tel.requests >= max_widget_requests or widget_tel.rate_limited:
            break
        rec = discovered[jid]
        company = rec["raw"].get("company") or {}
        slugs = candidate_account_slugs(company)
        resolved = None
        last_reason = "no_candidate_slug"

        for slug in slugs:
            if slug in widget_cache:
                status, payload = widget_cache[slug]
            else:
                if widget_tel.requests >= max_widget_requests or widget_tel.rate_limited:
                    break
                if widget_tel.requests > 0:
                    sleep_fn(widget_delay_s)
                wurl = f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"
                status, payload = get_json(wurl, telemetry=widget_tel)
                widget_cache[slug] = (status, payload)
            if status != 200 or not isinstance(payload, dict):
                last_reason = f"widget_http_{status}"
                continue
            matched_item, reason = match_widget_posting(rec["raw"], slug, payload)
            if matched_item is not None:
                posting = widget_job_to_posting(slug, payload.get("name") or company.get("title") or slug, matched_item)
                widget_screening = filtering.screen(filtering.inputs_from_posting(posting))
                norm = normalize_v1(posting, now)
                evidence_bytes = encode_evidence(matched_item)
                resolved = {
                    "workable_uuid": jid,
                    "account_slug": slug,
                    "shortcode": posting.external_id,
                    "posting_key": posting.key,
                    "company": posting.company,
                    "title": posting.title,
                    "posting_url": posting.url,
                    "application_url": matched_item.get("application_url"),
                    "location": posting.location,
                    "countries": list(posting.countries),
                    "published_on": matched_item.get("published_on"),
                    "created_at": matched_item.get("created_at"),
                    "match_basis": reason,
                    "widget_board_job_count": len(payload.get("jobs") or []),
                    "widget_screening": widget_screening,
                    "evidence_bytes_len": len(evidence_bytes),
                    "normalized_keys": sorted(norm.keys()),
                    "company_website": company.get("website"),
                    "widget_raw_item": matched_item,
                }
                break
            last_reason = reason

        resolutions[jid] = resolved or {
            "workable_uuid": jid,
            "resolved": False,
            "reason": last_reason,
            "attempted_slugs": slugs,
            "company": company.get("title"),
            "title": rec["raw"].get("title"),
            "search_url": rec["raw"].get("url"),
        }

    # Compare against read-only production DB snapshot if provided
    prod_comparison = None
    if prod_snapshot_path and prod_snapshot_path.is_file():
        prod = json.loads(prod_snapshot_path.read_text())
        known_employers = {_norm_company(e["name"]) for e in prod.get("employers", [])}
        known_urls = {l.get("url", "").rstrip("/").lower() for l in prod.get("leads_public", []) if l.get("url")}
        unique_companies = {
            (rec["raw"].get("company") or {}).get("id"): (rec["raw"].get("company") or {}).get("title", "")
            for rec in discovered.values()
        }
        unknown_employers = [
            name for cid, name in unique_companies.items()
            if _norm_company(name) not in known_employers
        ]
        relevant_missing = []
        for jid in relevant_ids:
            rec = discovered[jid]
            res = resolutions.get(jid) or {}
            comp = (rec["raw"].get("company") or {}).get("title", "")
            url_check = (res.get("posting_url") or rec["raw"].get("url") or "").rstrip("/").lower()
            if url_check not in known_urls:
                relevant_missing.append(jid)
        prod_comparison = {
            "prod_employers_count": prod.get("employers_count"),
            "prod_monitored_sources": prod.get("monitored_sources_count"),
            "prod_listed_postings": prod.get("postings_listed"),
            "prod_listed_priority": (prod.get("listed_by_verdict") or {}).get("priority", 0),
            "prod_listed_review": (prod.get("listed_by_verdict") or {}).get("review", 0),
            "discovered_unique_employers": len(unique_companies),
            "previously_unknown_employers": len(unknown_employers),
            "known_employer_overlaps": sorted(
                name for name in unique_companies.values() if _norm_company(name) in known_employers
            ),
            "relevant_candidates_total": len(relevant_ids),
            "relevant_candidates_missing_from_prod": len(relevant_missing),
        }

    elapsed_s = round(time.monotonic() - t0, 2)
    _, peak_py_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    verdict_counts = {"priority": 0, "review": 0, "excluded": 0}
    bucket_counts = {
        "grad_swe": 0,
        "grad_ai_ml_research": 0,
        "junior_associate_non_grad_wording": 0,
        "recent_14d": 0,
    }
    for rec in discovered.values():
        verdict_counts[rec["screening"]["verdict"]] += 1
        for b in rec["buckets"]:
            bucket_counts[b] += 1

    resolved_ok = [r for r in resolutions.values() if r.get("shortcode")]

    # Build clean public summary items (no raw HTML bloat)
    top_candidates = []
    for jid in relevant_ids[:40]:
        rec = discovered[jid]
        raw = rec["raw"]
        res = resolutions.get(jid)
        top_candidates.append({
            "workable_uuid": jid,
            "company": (raw.get("company") or {}).get("title"),
            "company_website": (raw.get("company") or {}).get("website"),
            "title": raw.get("title"),
            "locations": raw.get("locations"),
            "workplace": raw.get("workplace"),
            "department": raw.get("department"),
            "created": raw.get("created"),
            "search_url": raw.get("url"),
            "matched_queries": rec["matched_queries"],
            "buckets": rec["buckets"],
            "verdict": rec["screening"]["verdict"],
            "family": rec["screening"]["family"],
            "codes": rec["screening"]["codes"],
            "resolved_posting_key": res.get("posting_key") if res and res.get("shortcode") else None,
            "resolved_posting_url": res.get("posting_url") if res and res.get("shortcode") else None,
            "resolved_published_on": res.get("published_on") if res and res.get("shortcode") else None,
        })

    held_out = evaluate_held_out_control(discovered, resolutions)

    return {
        "experiment": "workable_provider_wide_discovery",
        "executed_at_utc": now.isoformat().replace("+00:00", "Z"),
        "config": {
            "query_count": len(queries),
            "max_search_requests": max_search_requests,
            "max_widget_requests": max_widget_requests,
            "max_resolve_candidates": max_resolve_candidates,
            "search_delay_s": search_delay_s,
            "widget_delay_s": widget_delay_s,
        },
        "telemetry": {
            "elapsed_s": elapsed_s,
            "peak_python_bytes": peak_py_bytes,
            "peak_rss_kb": rss_kb,
            "search_requests": search_tel.requests,
            "search_bytes": search_tel.bytes_transferred,
            "search_errors": search_tel.http_errors,
            "widget_requests": widget_tel.requests,
            "widget_bytes": widget_tel.bytes_transferred,
            "widget_errors": widget_tel.http_errors,
            "rate_limited": search_tel.rate_limited or widget_tel.rate_limited,
        },
        "metrics": {
            "raw_search_hits": raw_hits,
            "duplicate_hits": duplicate_hits,
            "duplicate_rate": round(duplicate_hits / raw_hits, 4) if raw_hits else 0.0,
            "unique_vacancies": len(discovered),
            "unique_employers": len({(r["raw"].get("company") or {}).get("id") for r in discovered.values()}),
            "verdicts": verdict_counts,
            "relevant_candidates": verdict_counts["priority"] + verdict_counts["review"],
            "buckets": bucket_counts,
            "widget_resolution_attempted": len(resolutions),
            "widget_resolution_succeeded": len(resolved_ok),
        },
        "query_stats": query_stats,
        "prod_comparison": prod_comparison,
        "held_out_acceptance_test": held_out,
        "top_candidates": top_candidates,
        "resolved_records": [
            {k: v for k, v in r.items() if k != "widget_raw_item"}
            for r in resolved_ok
        ],
        "held_out_widget_raw": (
            held_out["widget_resolution"].get("widget_raw_item")
            if held_out.get("widget_resolution")
            else None
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded Workable UK graduate tech discovery experiment")
    parser.add_argument("--max-search-requests", type=int, default=20)
    parser.add_argument("--max-widget-requests", type=int, default=35)
    parser.add_argument("--max-resolve-candidates", type=int, default=25)
    parser.add_argument("--search-delay", type=float, default=0.5)
    parser.add_argument("--widget-delay", type=float, default=1.5)
    parser.add_argument("--prod-snapshot", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    result = run_experiment(
        max_search_requests=args.max_search_requests,
        max_widget_requests=args.max_widget_requests,
        max_resolve_candidates=args.max_resolve_candidates,
        search_delay_s=args.search_delay,
        widget_delay_s=args.widget_delay,
        prod_snapshot_path=args.prod_snapshot,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")

    m = result["metrics"]
    t = result["telemetry"]
    h = result["held_out_acceptance_test"]
    print(json.dumps({
        "metrics": m,
        "telemetry": t,
        "prod_comparison": result["prod_comparison"],
        "held_out_passed": h["passed"],
        "held_out_posting_key": (h.get("widget_resolution") or {}).get("posting_key"),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
