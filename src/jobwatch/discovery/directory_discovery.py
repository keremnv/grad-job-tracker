"""Bounded recurring employer-directory discovery for Greenhouse, Ashby, and Lever.

Refreshes open-source ATS board directories, identifies previously unknown boards,
probes them conservatively for UK graduate/entry-level technical relevance using
unchanged `jobwatch.filtering.screen()`, and optionally checks official employer
websites for bidirectional link corroboration.

Design constraints:
- Read-only HTTP only; standard library; bounded response sizes and request budgets.
- Immediate halt on HTTP 429; polite inter-request delays; never bypasses WAF/JS gates.
- Two-pass Greenhouse probing: fetches lightweight `/jobs` metadata first and only
  requests `?content=true` when UK location signals are present.
- Public observations only; never activates `sources` or alters owner records.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

from jobwatch import feeds, filtering
from jobwatch.discovery.workable_search import DiscoveredVacancy
from jobwatch.postgres.evidence import encode_evidence
from jobwatch.timing import deadline_evidence

MAX_EXCERPT_CHARS = 2500


def _excerpt(text: str, limit: int = MAX_EXCERPT_CHARS) -> str:
    clean = re.sub(r"\s+", " ", filtering.plain(text or "")).strip()
    if len(clean) <= limit:
        return clean
    return clean[: limit - 1].rstrip() + "…"

USER_AGENT = "PersonalGradJobWatch/0.1 (bounded ATS directory discovery; read-only)"
MAX_DIRECTORY_BYTES = 8 * 1024 * 1024
MAX_BOARD_BYTES = 8 * 1024 * 1024
MAX_SITE_BYTES = 512 * 1024

DEFAULT_DIRECTORIES: dict[str, dict[str, str]] = {
    "greenhouse": {
        "source": "ats-scrapers:greenhouse",
        "url": "https://raw.githubusercontent.com/kalil0321/ats-scrapers/main/ats-companies/greenhouse.csv",
    },
    "ashby": {
        "source": "ats-scrapers:ashby",
        "url": "https://raw.githubusercontent.com/kalil0321/ats-scrapers/main/ats-companies/ashby.csv",
    },
    "lever": {
        "source": "ats-scrapers:lever",
        "url": "https://raw.githubusercontent.com/kalil0321/ats-scrapers/main/ats-companies/lever.csv",
    },
}

UK_NAME_HINT = re.compile(
    r"\b(?:uk|london|cambridge|oxford|manchester|edinburgh|bristol|leeds|glasgow|"
    r"birmingham|belfast|cardiff|britain|british|england|scotland|wales)\b|"
    r"(?:\.co\.uk|\.org\.uk|\buk$)",
    re.I,
)

UK_LOCATION_SIGNAL = re.compile(
    r"\b(?:united kingdom|u\.?k\.?|great britain|england|scotland|wales|northern ireland|"
    r"london|cambridge|oxford|manchester|edinburgh|bristol|leeds|glasgow|birmingham|"
    r"reading|guildford|hatfield|dundee|bridgend|sheffield|nottingham|bath|york|belfast|cardiff)\b|"
    r"(?:^|[,;\s/(-])gb(?:$|[,;\s/)-])",
    re.I,
)

_ANCHOR_HREF_URL = re.compile(r"""<a\b[^>]*\bhref=["'](https?://[^"'<>#\s]+)""", re.I)
_STRUCTURED_WEBSITE_URL = re.compile(
    r'"(?:publicWebsite|companyWebsite|websiteUrl|homepageUrl)"\s*:\s*"(https?:\\?/\\?/[^"\\<>#\s]+(?:\\?/[^"\\<>#\s]*)*)"'
)
_HOSTED_ATS_DOMAINS = (
    "greenhouse.io",
    "greenhouse.com",
    "ashbyhq.com",
    "ashbyprd.com",
    "lever.co",
    "workable.com",
    "linkedin.com",
    "twitter.com",
    "x.com",
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "glassdoor.",
    "indeed.",
    "google.com",
    "apple.com",
    "w3.org",
    "schema.org",
    "cloudfront.net",
    "cloudflare.com",
    "googleapis.com",
    "gstatic.com",
)
_NON_HTML_ASSET_SUFFIXES = (".svg", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".css", ".js", ".woff", ".woff2")


class DiscoveryRateLimitError(RuntimeError):
    """Raised immediately when an upstream returns HTTP 429."""


@dataclass(frozen=True)
class DirectoryBoard:
    provider: str
    board: str
    company_hint: str
    uk_hint: bool
    directory_source: str
    directory_url: str

    @property
    def board_key(self) -> str:
        return f"{self.provider}:{self.board.lower()}"


@dataclass(frozen=True)
class DirectoryRefreshResult:
    provider: str
    directory_source: str
    directory_url: str
    sha256: str
    bytes_read: int
    boards: tuple[DirectoryBoard, ...]
    skipped_invalid: int
    fetched_at: str


@dataclass(frozen=True)
class IdentityCheckResult:
    status: str  # verified_official_link | hosted_link_only | unverified_js_or_blocked | domain_mismatch | not_checked
    official_website: str = ""
    careers_url: str = ""
    evidence: str = ""
    checked_at: str = ""
    requests_made: int = 0
    bytes_read: int = 0


@dataclass(frozen=True)
class BoardProbeResult:
    provider: str
    board: str
    region: str
    http_status: int
    probe_status: str  # active_uk_relevant | active_non_uk | empty | dead | error
    total_vacancies: int
    uk_vacancies: int
    priority_vacancies: int
    review_vacancies: int
    custom_host_urls: int
    requests_made: int
    bytes_read: int
    elapsed_s: float
    company_name: str = ""
    hosted_website: str = ""
    sample_posting_id: str = ""
    vacancies: tuple[DiscoveredVacancy, ...] = ()
    truncated_lever_page: bool = False
    error_code: str = ""
    etag: str = ""
    not_modified: bool = False

    @property
    def board_key(self) -> str:
        return f"{self.provider}:{self.board.lower()}"


def default_http_get(
    url: str,
    *,
    max_bytes: int = MAX_BOARD_BYTES,
    timeout: int = 20,
    accept: str = "application/json,text/plain,*/*",
    allow_truncate: bool = False,
) -> tuple[int, bytes, str]:
    """Bounded read-only HTTP GET returning (status_code, body_bytes, final_url).

    When `allow_truncate=False` (default for JSON/CSV endpoints), responses exceeding
    `max_bytes` return `(-1, b"", final_url)` so partial structured payloads never parse.
    When `allow_truncate=True` (used ONLY for HTML header/link inspection in identity
    verification), the bounded `max_bytes` prefix is returned safely.
    """
    req = Request(url, headers={"Accept": accept, "User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read(max_bytes + 1)
            if len(raw) > max_bytes:
                if allow_truncate:
                    return resp.status, raw[:max_bytes], resp.geturl()
                return -1, b"", resp.geturl()
            return resp.status, raw, resp.geturl()
    except HTTPError as exc:
        return exc.code, b"", url
    except (URLError, TimeoutError, OSError):
        return 0, b"", url


def parse_directory_csv(
    provider: str,
    csv_text: str,
    *,
    directory_source: str,
    directory_url: str,
) -> tuple[tuple[DirectoryBoard, ...], int]:
    """Parse a public ATS board directory CSV into deduplicated validated DirectoryBoard entries."""
    if provider not in ("greenhouse", "ashby", "lever"):
        raise ValueError(f"unsupported directory provider: {provider!r}")
    seen: set[str] = set()
    boards: list[DirectoryBoard] = []
    skipped = 0
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        if not isinstance(row, dict):
            skipped += 1
            continue
        slug = (
            row.get("slug")
            or row.get("board")
            or row.get("token")
            or row.get("board_token")
            or ""
        ).strip()
        if not slug or not feeds.BOARD.fullmatch(slug):
            skipped += 1
            continue
        key = slug.lower()
        if key in seen:
            continue
        seen.add(key)
        company = (row.get("company") or row.get("name") or slug).strip() or slug
        uk_hint = bool(UK_NAME_HINT.search(company) or UK_NAME_HINT.search(slug))
        boards.append(
            DirectoryBoard(
                provider=provider,
                board=slug,
                company_hint=company,
                uk_hint=uk_hint,
                directory_source=directory_source,
                directory_url=directory_url,
            )
        )
    return tuple(boards), skipped


def fetch_directory(
    provider: str,
    *,
    url: str | None = None,
    directory_source: str | None = None,
    get_bytes=default_http_get,
    now: datetime | None = None,
) -> DirectoryRefreshResult:
    """Download and parse one public ATS board directory with SHA-256 provenance."""
    cfg = DEFAULT_DIRECTORIES.get(provider)
    if not url and not cfg:
        raise ValueError(f"no default directory configured for provider {provider!r}")
    target_url = url or cfg["url"]
    source_label = directory_source or (cfg["source"] if cfg else f"directory:{provider}")
    status, raw, _final = get_bytes(target_url, max_bytes=MAX_DIRECTORY_BYTES, timeout=25, accept="text/csv,text/plain,*/*")
    if status == 429:
        raise DiscoveryRateLimitError(f"HTTP 429 fetching directory {target_url}")
    if status != 200 or not raw:
        raise RuntimeError(f"directory fetch failed ({status}) for {source_label}")
    digest = sha256(raw).hexdigest()
    boards, skipped = parse_directory_csv(
        provider,
        raw.decode("utf-8", "replace"),
        directory_source=source_label,
        directory_url=target_url,
    )
    at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return DirectoryRefreshResult(
        provider=provider,
        directory_source=source_label,
        directory_url=target_url,
        sha256=digest,
        bytes_read=len(raw),
        boards=boards,
        skipped_invalid=skipped,
        fetched_at=at,
    )


def _is_uk_location(location: str, countries: list[str] | tuple[str, ...]) -> bool:
    for c in countries:
        cl = (c or "").strip().lower()
        if cl in ("gb", "uk", "united kingdom", "great britain", "england", "scotland", "wales", "northern ireland"):
            return True
    return bool(UK_LOCATION_SIGNAL.search(location or ""))


def _make_discovered_vacancy(
    *,
    provider: str,
    board: str,
    native_id: str,
    company: str,
    title: str,
    posting_url: str,
    location_text: str,
    country: str,
    countries: tuple[str, ...],
    department: str,
    team: str,
    employment_type: str,
    workplace_type: str,
    published_at: str,
    source_dates: dict,
    description_raw: str,
    raw_item: dict,
    mechanism: str,
    observed_at_iso: str,
    directory_source: str,
) -> DiscoveredVacancy | None:
    if not native_id or not title.strip() or not feeds.public_url(posting_url):
        return None
    full_plain = filtering.plain(description_raw or "")
    excerpt = _excerpt(description_raw or "", MAX_EXCERPT_CHARS)
    desc_sha = sha256(full_plain.encode("utf-8")).hexdigest() if full_plain else None
    ev_sha = sha256(encode_evidence(raw_item)).hexdigest()
    deadlines = deadline_evidence(raw_item, description_raw or "", posting_url, observed_at_iso)
    return DiscoveredVacancy(
        candidate_key=f"{provider}:{board.lower()}:{native_id}",
        provider=provider,
        board=board,
        native_id=native_id,
        company=company.strip() or board,
        title=title.strip(),
        posting_url=posting_url,
        location_text=location_text,
        country=country,
        countries=countries,
        department=department,
        team=team,
        employment_type=employment_type,
        workplace_type=workplace_type,
        published_at=published_at,
        source_dates=source_dates,
        deadlines=deadlines,
        description_excerpt=excerpt,
        description_full=full_plain,
        description_sha256=desc_sha,
        evidence_sha256=ev_sha,
        mechanism=mechanism,
        provenance={
            "directory_source": directory_source,
            "board": board,
            "authoritative_board_observation": False,
            "observed_at": observed_at_iso,
        },
    )


def _extract_greenhouse_jobs(data: dict, *, include_content: bool) -> tuple[list[dict], int, str]:
    raw_jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(raw_jobs, list):
        return [], 0, "global"
    parsed = []
    custom_hosts = 0
    region = "global"
    for j in raw_jobs:
        if not isinstance(j, dict) or j.get("internal_job_id") is None:
            continue
        link = j.get("absolute_url") or ""
        if "eu.greenhouse.io" in link:
            region = "eu"
        if link and "greenhouse.io" not in link:
            custom_hosts += 1
        locs = [(j.get("location") or {}).get("name") or ""]
        for off in j.get("offices") or []:
            if isinstance(off, dict):
                locs += [off.get("name") or "", off.get("location") or ""]
        depts = [d.get("name") or "" for d in (j.get("departments") or []) if isinstance(d, dict)]
        dates = {}
        for k in ("first_published", "updated_at"):
            if isinstance(j.get(k), str) and j[k]:
                dates[k] = j[k]
        parsed.append({
            "native_id": str(j.get("id") or ""),
            "title": str(j.get("title") or ""),
            "location": ", ".join(dict.fromkeys(x for x in locs if isinstance(x, str) and x.strip())),
            "country": "",
            "countries": (),
            "department": ", ".join(dict.fromkeys(x for x in depts if isinstance(x, str) and x.strip())),
            "team": "",
            "employment_type": "",
            "workplace_type": "",
            "description": str(j.get("content") or "") if include_content else "",
            "url": link,
            "published_at": dates.get("first_published") or dates.get("updated_at") or "",
            "source_dates": dates,
            "raw": j,
        })
    return parsed, custom_hosts, region


def _http_get_conditional(
    url: str,
    *,
    if_none_match: str = "",
    max_bytes: int = MAX_BOARD_BYTES,
    timeout: int = 20,
    accept: str = "application/json,text/plain,*/*",
    allow_truncate: bool = False,
    get_bytes=default_http_get,
) -> tuple[int, bytes, str, str]:
    """Conditional-GET wrapper returning (status_code, body_bytes, final_url, etag).

    When `get_bytes` is the real `default_http_get`, sends `If-None-Match` when a cached
    ETag is available, handles HTTP 304 without body transfer, and captures the response ETag.
    Custom `get_bytes` callables (e.g., offline test stubs) remain 100% compatible.
    """
    if get_bytes is not default_http_get:
        st, raw, final = get_bytes(url, max_bytes=max_bytes)
        return st, raw, final, ""
    headers = {"Accept": accept, "User-Agent": USER_AGENT}
    if if_none_match:
        headers["If-None-Match"] = if_none_match
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:
            etag = (resp.headers.get("ETag") or "").strip()
            raw = resp.read(max_bytes + 1)
            if len(raw) > max_bytes:
                if allow_truncate:
                    return resp.status, raw[:max_bytes], resp.geturl(), etag
                return -1, b"", resp.geturl(), etag
            return resp.status, raw, resp.geturl(), etag
    except HTTPError as exc:
        if exc.code == 304:
            etag = (exc.headers.get("ETag") if exc.headers else None) or if_none_match
            return 304, b"", url, etag.strip()
        return exc.code, b"", url, ""
    except (URLError, TimeoutError, OSError):
        return 0, b"", url, ""


def probe_ats_board(
    provider: str,
    board: str,
    *,
    company_hint: str = "",
    directory_source: str = "ats_directory",
    get_bytes=default_http_get,
    fetch_full_on_uk: bool = True,
    cached_etag: str = "",
    now: datetime | None = None,
) -> BoardProbeResult:
    """Conservatively probe one Greenhouse, Ashby, or Lever board for UK relevant vacancies."""
    if not feeds.BOARD.fullmatch(board):
        raise ValueError(f"invalid board slug: {board!r}")
    t0 = time.monotonic()
    at_iso = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    requests_made = 0
    bytes_read = 0
    region = "global"
    company_name = company_hint or board
    hosted_website = ""
    custom_host_urls = 0
    truncated_lever = False
    parsed_jobs: list[dict] = []
    response_etag = ""

    if provider == "greenhouse":
        meta_url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs"
        status, raw, _, response_etag = _http_get_conditional(
            meta_url, if_none_match=cached_etag, max_bytes=MAX_BOARD_BYTES, get_bytes=get_bytes,
        )
        requests_made += 1
        bytes_read += len(raw)
        if status == 304:
            return BoardProbeResult(provider, board, region, 304, "unchanged_304", 0, 0, 0, 0, 0, requests_made, 0, round(time.monotonic() - t0, 3), company_name=company_name, etag=response_etag or cached_etag, not_modified=True)
        if status == 429:
            raise DiscoveryRateLimitError(f"HTTP 429 probing greenhouse:{board}")
        if status == 404:
            return BoardProbeResult(provider, board, region, status, "dead", 0, 0, 0, 0, 0, requests_made, bytes_read, round(time.monotonic() - t0, 3), company_name=company_name)
        if status != 200:
            return BoardProbeResult(provider, board, region, status, "error", 0, 0, 0, 0, 0, requests_made, bytes_read, round(time.monotonic() - t0, 3), company_name=company_name, error_code=f"http_{status}")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return BoardProbeResult(provider, board, region, -2, "error", 0, 0, 0, 0, 0, requests_made, bytes_read, round(time.monotonic() - t0, 3), company_name=company_name, error_code="invalid_json")
        parsed_jobs, custom_host_urls, region = _extract_greenhouse_jobs(data, include_content=False)
        uk_prelim = sum(1 for j in parsed_jobs if _is_uk_location(j["location"], j["countries"]))
        if fetch_full_on_uk and uk_prelim > 0:
            full_url = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true"
            status2, raw2, _ = get_bytes(full_url, max_bytes=MAX_BOARD_BYTES)
            requests_made += 1
            bytes_read += len(raw2)
            if status2 == 429:
                raise DiscoveryRateLimitError(f"HTTP 429 fetching greenhouse:{board} content")
            if status2 == 200 and raw2:
                try:
                    data2 = json.loads(raw2.decode("utf-8"))
                    parsed_jobs, custom_host_urls, region = _extract_greenhouse_jobs(data2, include_content=True)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    pass

    elif provider == "ashby":
        url = f"https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true"
        status, raw, _, response_etag = _http_get_conditional(
            url, if_none_match=cached_etag, max_bytes=MAX_BOARD_BYTES, get_bytes=get_bytes,
        )
        requests_made += 1
        bytes_read += len(raw)
        if status == 304:
            return BoardProbeResult(provider, board, region, 304, "unchanged_304", 0, 0, 0, 0, 0, requests_made, 0, round(time.monotonic() - t0, 3), company_name=company_name, etag=response_etag or cached_etag, not_modified=True)
        if status == 429:
            raise DiscoveryRateLimitError(f"HTTP 429 probing ashby:{board}")
        if status == 404:
            return BoardProbeResult(provider, board, region, status, "dead", 0, 0, 0, 0, 0, requests_made, bytes_read, round(time.monotonic() - t0, 3), company_name=company_name)
        if status != 200:
            return BoardProbeResult(provider, board, region, status, "error", 0, 0, 0, 0, 0, requests_made, bytes_read, round(time.monotonic() - t0, 3), company_name=company_name, error_code=f"http_{status}")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return BoardProbeResult(provider, board, region, -2, "error", 0, 0, 0, 0, 0, requests_made, bytes_read, round(time.monotonic() - t0, 3), company_name=company_name, error_code="invalid_json")
        if isinstance(data, dict):
            if isinstance(data.get("organizationName"), str) and data["organizationName"].strip():
                company_name = data["organizationName"].strip()
            for k in ("companyWebsite", "websiteUrl"):
                if isinstance(data.get(k), str) and feeds.public_url(data[k]):
                    hosted_website = data[k]
                    break
        for j in (data.get("jobs") if isinstance(data, dict) else None) or []:
            if not isinstance(j, dict) or j.get("isListed") is False:
                continue
            addr = (j.get("address") or {}).get("postalAddress") or {}
            countries = [addr.get("addressCountry") or ""]
            locs = [j.get("location") or ""]
            for sec in j.get("secondaryLocations") or []:
                if isinstance(sec, dict):
                    locs.append(sec.get("location") or "")
                    countries.append((sec.get("address") or {}).get("addressCountry") or "")
            clean_countries = tuple(c for c in dict.fromkeys(countries) if isinstance(c, str) and c.strip())
            pub = str(j.get("publishedAt") or "")
            parsed_jobs.append({
                "native_id": str(j.get("id") or ""),
                "title": str(j.get("title") or ""),
                "location": ", ".join(dict.fromkeys(x for x in locs if isinstance(x, str) and x.strip())),
                "country": clean_countries[0] if clean_countries else "",
                "countries": clean_countries,
                "department": str(j.get("department") or ""),
                "team": str(j.get("team") or ""),
                "employment_type": str(j.get("employmentType") or ""),
                "workplace_type": str(j.get("workplaceType") or ""),
                "description": str(j.get("descriptionPlain") or ""),
                "url": str(j.get("jobUrl") or j.get("applyUrl") or ""),
                "published_at": pub,
                "source_dates": {"publishedAt": pub} if pub else {},
                "raw": j,
            })

    elif provider == "lever":
        url = f"https://api.lever.co/v0/postings/{board}?mode=json&limit=100"
        status, raw, _, response_etag = _http_get_conditional(
            url, if_none_match=cached_etag, max_bytes=MAX_BOARD_BYTES, get_bytes=get_bytes,
        )
        requests_made += 1
        bytes_read += len(raw)
        if status == 304:
            return BoardProbeResult(provider, board, region, 304, "unchanged_304", 0, 0, 0, 0, 0, requests_made, 0, round(time.monotonic() - t0, 3), company_name=company_name, etag=response_etag or cached_etag, not_modified=True)
        if status == 429:
            raise DiscoveryRateLimitError(f"HTTP 429 probing lever:{board}")
        if status == 404:
            # EU-hosted Lever boards return 404 on api.lever.co; try EU endpoint once.
            eu_url = f"https://api.eu.lever.co/v0/postings/{board}?mode=json&limit=100"
            status_eu, raw_eu, _, response_etag = _http_get_conditional(
                eu_url, if_none_match=cached_etag, max_bytes=MAX_BOARD_BYTES, get_bytes=get_bytes,
            )
            requests_made += 1
            bytes_read += len(raw_eu)
            if status_eu == 304:
                return BoardProbeResult(provider, board, "eu", 304, "unchanged_304", 0, 0, 0, 0, 0, requests_made, 0, round(time.monotonic() - t0, 3), company_name=company_name, etag=response_etag or cached_etag, not_modified=True)
            if status_eu == 429:
                raise DiscoveryRateLimitError(f"HTTP 429 probing lever-eu:{board}")
            if status_eu == 200:
                status, raw, region = status_eu, raw_eu, "eu"
            else:
                return BoardProbeResult(provider, board, region, status, "dead", 0, 0, 0, 0, 0, requests_made, bytes_read, round(time.monotonic() - t0, 3), company_name=company_name)
        if status != 200:
            return BoardProbeResult(provider, board, region, status, "error", 0, 0, 0, 0, 0, requests_made, bytes_read, round(time.monotonic() - t0, 3), company_name=company_name, error_code=f"http_{status}")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return BoardProbeResult(provider, board, region, -2, "error", 0, 0, 0, 0, 0, requests_made, bytes_read, round(time.monotonic() - t0, 3), company_name=company_name, error_code="invalid_json")
        if not isinstance(data, list):
            return BoardProbeResult(provider, board, region, -2, "error", 0, 0, 0, 0, 0, requests_made, bytes_read, round(time.monotonic() - t0, 3), company_name=company_name, error_code="unexpected_shape")
        truncated_lever = len(data) >= 100
        for j in data:
            if not isinstance(j, dict):
                continue
            cats = j.get("categories") or {}
            locs = [cats.get("location") or ""] + list(cats.get("allLocations") or [])
            country = str(j.get("country") or "")
            desc_parts = [str(j.get("descriptionPlain") or "")]
            for lst in j.get("lists") or []:
                if isinstance(lst, dict) and lst.get("content"):
                    desc_parts.append(str(lst["content"]))
            desc_parts.append(str(j.get("additionalPlain") or ""))
            dates = {"createdAt": j["createdAt"]} if isinstance(j.get("createdAt"), int) else {}
            parsed_jobs.append({
                "native_id": str(j.get("id") or ""),
                "title": str(j.get("text") or ""),
                "location": ", ".join(dict.fromkeys(x for x in locs if isinstance(x, str) and x.strip())),
                "country": country,
                "countries": (country,) if country else (),
                "department": str(cats.get("department") or ""),
                "team": str(cats.get("team") or ""),
                "employment_type": str(cats.get("commitment") or ""),
                "workplace_type": str(j.get("workplaceType") or ""),
                "description": "\n".join(p for p in desc_parts if p),
                "url": str(j.get("hostedUrl") or j.get("applyUrl") or ""),
                "published_at": "",
                "source_dates": dates,
                "raw": j,
            })
    else:
        raise ValueError(f"unsupported provider: {provider!r}")

    total = len(parsed_jobs)
    if total == 0:
        return BoardProbeResult(
            provider, board, region, status, "empty", 0, 0, 0, 0, custom_host_urls,
            requests_made, bytes_read, round(time.monotonic() - t0, 3),
            company_name=company_name, hosted_website=hosted_website, etag=response_etag,
        )

    uk_count = 0
    priority_count = 0
    review_count = 0
    relevant: list[DiscoveredVacancy] = []
    sample_id = parsed_jobs[0]["native_id"]
    mechanism = f"ats_directory:{directory_source}"

    for j in parsed_jobs:
        is_uk = _is_uk_location(j["location"], j["countries"])
        if is_uk:
            uk_count += 1
        # Conservative screening: only screen UK-located postings (or all if board has UK vacancies).
        if not is_uk:
            continue
        scr = filtering.screen({
            "title": j["title"],
            "location": j["location"],
            "country": j["country"],
            "countries": list(j["countries"]),
            "department": j["department"],
            "team": j["team"],
            "employment_type": j["employment_type"],
            "description": j["description"] or None,
        })
        if scr["verdict"] == "priority":
            priority_count += 1
        elif scr["verdict"] == "review":
            review_count += 1
        else:
            continue
        vac = _make_discovered_vacancy(
            provider=provider,
            board=board,
            native_id=j["native_id"],
            company=company_name,
            title=j["title"],
            posting_url=j["url"],
            location_text=j["location"],
            country=j["country"],
            countries=j["countries"],
            department=j["department"],
            team=j["team"],
            employment_type=j["employment_type"],
            workplace_type=j["workplace_type"],
            published_at=j["published_at"],
            source_dates=j["source_dates"],
            description_raw=j["description"],
            raw_item=j["raw"],
            mechanism=mechanism,
            observed_at_iso=at_iso,
            directory_source=directory_source,
        )
        if vac is not None:
            relevant.append(vac)
            sample_id = j["native_id"]

    probe_status = "active_uk_relevant" if (priority_count > 0 or review_count > 0) else "active_non_uk"
    return BoardProbeResult(
        provider=provider,
        board=board,
        region=region,
        http_status=status,
        probe_status=probe_status,
        total_vacancies=total,
        uk_vacancies=uk_count,
        priority_vacancies=priority_count,
        review_vacancies=review_count,
        custom_host_urls=custom_host_urls,
        requests_made=requests_made,
        bytes_read=bytes_read,
        elapsed_s=round(time.monotonic() - t0, 3),
        company_name=company_name,
        hosted_website=hosted_website,
        sample_posting_id=sample_id,
        vacancies=tuple(relevant),
        truncated_lever_page=truncated_lever,
        etag=response_etag,
    )


def _hosted_board_url(provider: str, board: str, region: str) -> str:
    if provider == "greenhouse":
        host = "job-boards.eu.greenhouse.io" if region == "eu" else "job-boards.greenhouse.io"
        return f"https://{host}/{board}"
    if provider == "ashby":
        return f"https://jobs.ashbyhq.com/{board}"
    if provider == "lever":
        host = "jobs.eu.lever.co" if region == "eu" else "jobs.lever.co"
        return f"https://{host}/{board}"
    raise ValueError(f"unsupported provider: {provider!r}")


def _is_candidate_official_website(candidate: str) -> bool:
    if not feeds.public_url(candidate):
        return False
    parsed = urlsplit(candidate)
    host = (parsed.hostname or "").lower()
    path_low = (parsed.path or "").lower()
    if not host or any(d in host for d in _HOSTED_ATS_DOMAINS):
        return False
    if path_low.endswith(_NON_HTML_ASSET_SUFFIXES):
        return False
    return True


def _extract_official_website_from_hosted_html(html: str) -> str:
    """Extract a candidate employer website URL from hosted ATS HTML (anchors or structured SPA JSON).

    Does NOT establish identity on its own: `verify_board_identity` still requires strict
    bidirectional corroboration from the employer's official site back to the ATS board.
    """
    for match in _ANCHOR_HREF_URL.finditer(html):
        candidate = match.group(1)
        if _is_candidate_official_website(candidate):
            return candidate
    # Ashby (and similar React SPAs) embed `window.__appData` with `"publicWebsite":"https://..."`
    # rather than static `<a>` tags in the initial HTML shell.
    for match in _STRUCTURED_WEBSITE_URL.finditer(html):
        candidate = match.group(1).replace(r"\/", "/")
        if _is_candidate_official_website(candidate):
            return candidate
    return ""


def _html_corroborates_board(html: str, provider: str, board: str, sample_posting_id: str = "") -> str | None:
    """Return concise evidence string if official-site HTML/JS references the exact ATS board."""
    low = html.lower()
    b = board.lower()
    if provider == "greenhouse":
        for needle in (
            f"greenhouse.io/{b}",
            f"greenhouse.io/embed/job_board?for={b}",
            f"boards-api.greenhouse.io/v1/boards/{b}",
            f"gh_jid={sample_posting_id}".lower() if sample_posting_id else "",
        ):
            if needle and needle in low:
                return f"Official site references {needle}"
    elif provider == "ashby":
        for needle in (
            f"ashbyhq.com/{b}",
            f"posting-api/job-board/{b}",
            sample_posting_id.lower() if sample_posting_id and len(sample_posting_id) >= 12 else "",
        ):
            if needle and needle in low:
                return f"Official site references {needle}"
    elif provider == "lever":
        for needle in (
            f"lever.co/{b}",
            f"v0/postings/{b}",
            sample_posting_id.lower() if sample_posting_id and len(sample_posting_id) >= 12 else "",
        ):
            if needle and needle in low:
                return f"Official site references {needle}"
    return None


def verify_board_identity(
    provider: str,
    board: str,
    *,
    region: str = "global",
    hosted_website: str = "",
    sample_posting_id: str = "",
    get_bytes=default_http_get,
    now: datetime | None = None,
) -> IdentityCheckResult:
    """Read-only bidirectional check between an ATS board and the employer's official site.

    Never guesses domains blindly and never bypasses JS/WAF gates. Returns explicit
    verification status + dated evidence for owner review prior to any activation.
    """
    at_iso = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    reqs = 0
    nbytes = 0
    official_site = hosted_website if feeds.public_url(hosted_website) else ""

    if not official_site:
        hosted_url = _hosted_board_url(provider, board, region)
        st, raw, _ = get_bytes(hosted_url, max_bytes=MAX_SITE_BYTES, timeout=12, accept="text/html,*/*", allow_truncate=True)
        reqs += 1
        nbytes += len(raw)
        if st == 429:
            raise DiscoveryRateLimitError(f"HTTP 429 inspecting hosted page {hosted_url}")
        if st == 200 and raw:
            official_site = _extract_official_website_from_hosted_html(raw.decode("utf-8", "replace"))

    if not official_site:
        return IdentityCheckResult(
            status="unverified_js_or_blocked",
            evidence="No official employer website link found on hosted ATS board.",
            checked_at=at_iso,
            requests_made=reqs,
            bytes_read=nbytes,
        )

    # Check the discovered official website (and `/careers` if the link is a root homepage).
    parsed = urlsplit(official_site)
    candidate_pages = [official_site]
    if parsed.path in ("", "/"):
        candidate_pages.append(urljoin(official_site.rstrip("/") + "/", "careers"))

    for page_url in candidate_pages[:2]:
        st, raw, final_url = get_bytes(page_url, max_bytes=MAX_SITE_BYTES, timeout=12, accept="text/html,*/*", allow_truncate=True)
        reqs += 1
        nbytes += len(raw)
        if st == 429:
            raise DiscoveryRateLimitError(f"HTTP 429 checking official site {page_url}")
        if st != 200 or not raw:
            continue
        html = raw.decode("utf-8", "replace")
        corroboration = _html_corroborates_board(html, provider, board, sample_posting_id)
        if corroboration:
            return IdentityCheckResult(
                status="verified_official_link",
                official_website=official_site,
                careers_url=final_url if feeds.public_url(final_url) else page_url,
                evidence=f"{corroboration} at {final_url or page_url}",
                checked_at=at_iso,
                requests_made=reqs,
                bytes_read=nbytes,
            )

    return IdentityCheckResult(
        status="hosted_link_only",
        official_website=official_site,
        careers_url="",
        evidence=f"Hosted board links to {official_site}, but static HTML did not corroborate {provider}:{board} (JS-rendered or separate careers portal; manual review required).",
        checked_at=at_iso,
        requests_made=reqs,
        bytes_read=nbytes,
    )
