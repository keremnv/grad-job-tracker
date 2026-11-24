"""Isolated, replaceable Workable provider-wide vacancy discovery adapter.

Uses Workable's public unauthenticated search endpoint
(`https://jobs.workable.com/api/v1/jobs`) and optionally corroborates candidate
vacancies against Workable's public per-account widget endpoint
(`https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true`).

IMPORTANT OPERATIONAL BOUNDARIES:
- `jobs.workable.com/api/v1/jobs` is an undocumented public frontend JSON API
  (not a formally versioned developer API). Keep it strictly isolated behind
  this replaceable adapter, enforce conservative pacing, halt immediately on
  HTTP 429, and never bypass access restrictions or rate limits.
- Individual vacancy discoveries are NEVER authoritative board snapshots:
  absence from search results never establishes closure or `not_seen`.
- Recurring production scheduling remains disabled unless explicitly approved.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
import re
import time
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from jobwatch import feeds, filtering
from jobwatch.postgres.evidence import encode_evidence
from jobwatch.timing import deadline_evidence

USER_AGENT = 'PersonalGradJobWatch/0.1 (public vacancy discovery; read-only; no applications)'
MAX_SEARCH_RESPONSE_BYTES = 4 * 1024 * 1024
EXCERPT_LIMIT = 2000

DEFAULT_WORKABLE_UK_QUERIES: tuple[dict, ...] = (
    {'query': 'graduate software engineer', 'location': 'United Kingdom', 'max_pages': 2},
    {'query': 'graduate ai', 'location': 'United Kingdom', 'max_pages': 2},
    {'query': 'graduate machine learning', 'location': 'United Kingdom', 'max_pages': 2},
    {'query': 'graduate data science', 'location': 'United Kingdom', 'max_pages': 2},
    {'query': 'graduate developer', 'location': 'United Kingdom', 'max_pages': 2},
    {'query': 'junior software engineer', 'location': 'United Kingdom', 'max_pages': 2},
    {'query': 'junior developer', 'location': 'United Kingdom', 'max_pages': 2},
    {'query': 'associate software engineer', 'location': 'United Kingdom', 'max_pages': 2},
    {'query': 'research engineer', 'location': 'United Kingdom', 'max_pages': 1},
    {'query': 'graduate', 'location': 'United Kingdom', 'day_range': '14', 'max_pages': 2},
)


@dataclass(frozen=True)
class DiscoveredVacancy:
    """Normalized public discovery observation (NOT an authoritative board snapshot)."""
    candidate_key: str
    provider: str
    board: str | None
    native_id: str | None
    company: str
    title: str
    posting_url: str
    location_text: str
    country: str
    countries: tuple[str, ...]
    department: str
    team: str
    employment_type: str
    workplace_type: str
    published_at: str
    source_dates: dict
    deadlines: list
    description_full: str
    description_excerpt: str
    description_sha256: str | None
    evidence_sha256: str
    mechanism: str
    provenance: dict
    authoritative_board_observation: bool = False


@dataclass(frozen=True)
class DiscoveryReport:
    mechanism: str
    executed_at: str
    vacancies: tuple[DiscoveredVacancy, ...]
    raw_hits: int
    duplicate_hits: int
    search_requests: int
    widget_requests: int
    bytes_transferred: int
    rate_limited: bool
    errors: tuple[dict, ...] = field(default_factory=tuple)


def _norm_company(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', (name or '').casefold())


def _norm_title(title: str) -> str:
    return re.sub(r'\s+', ' ', (title or '').strip()).casefold()


def _excerpt(html_text: str | None, limit: int = EXCERPT_LIMIT) -> str:
    plain = filtering.plain(html_text or '')
    if len(plain) <= limit:
        return plain
    return plain[: limit - 1].rstrip() + '…'


def _http_get_json(url: str, *, timeout: int = 25) -> tuple[int, dict, int]:
    req = Request(url, headers={'Accept': 'application/json', 'User-Agent': USER_AGENT})
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_SEARCH_RESPONSE_BYTES + 1)
            if len(raw) > MAX_SEARCH_RESPONSE_BYTES:
                return -1, {}, len(raw)
            return resp.status, json.loads(raw.decode('utf-8')), len(raw)
    except HTTPError as exc:
        return exc.code, {}, 0
    except (URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError):
        return 0, {}, 0


def candidate_workable_slugs(company: dict) -> list[str]:
    """Derive bounded candidate Workable account slugs for corroboration only."""
    candidates: list[str] = []
    company_url = (company.get('url') or '').strip()
    match = re.search(r'/jobs-at-([a-z0-9-]+)$', company_url)
    if match:
        slug = match.group(1)
        candidates.append(slug)
        if '-' in slug:
            candidates.append(slug.replace('-', ''))
    website = (company.get('website') or '').strip()
    if website.startswith(('http://', 'https://')):
        host = (urlsplit(website).hostname or '').lower().removeprefix('www.')
        stem = host.split('.')[0] if host else ''
        if stem and feeds.BOARD.fullmatch(stem):
            candidates.append(stem)
    return [c for c in dict.fromkeys(candidates) if feeds.BOARD.fullmatch(c)]


def corroborate_widget_posting(search_job: dict, account: str, widget_payload: dict) -> tuple[feeds.Posting | None, str]:
    """Verify whether an apply.workable.com widget response contains the discovered job."""
    company_title = ((search_job.get('company') or {}).get('title') or '').strip()
    widget_name = (widget_payload.get('name') or '').strip()
    nc, nw = _norm_company(company_title), _norm_company(widget_name)
    if not (nc and nw and (nc == nw or nc in nw or nw in nc)):
        return None, 'company_name_mismatch'

    try:
        postings = feeds.collect_board(
            {'company': widget_name or company_title or account, 'provider': 'workable', 'board': account},
            get_json=lambda _: widget_payload,
        )
    except ValueError:
        return None, 'widget_validation_failed'

    target_title = _norm_title(search_job.get('title') or '')
    target_date = (search_job.get('created') or '')[:10]
    matches = [p for p in postings if _norm_title(p.title) == target_title]
    if not matches:
        return None, 'title_not_in_widget'
    if len(matches) == 1:
        return matches[0], 'exact_title_and_company'
    dated = [
        p for p in matches
        if target_date and target_date in {str(p.source_dates.get('created_at') or '')[:10], str(p.source_dates.get('published_on') or '')[:10]}
    ]
    if len(dated) == 1:
        return dated[0], 'exact_title_company_and_date'
    return None, 'ambiguous_multiple_matches'


def _search_job_inputs(job: dict) -> tuple[str, str, list[str], str]:
    locations = [loc for loc in (job.get('locations') or []) if isinstance(loc, str) and loc.strip()]
    loc_obj = job.get('location') if isinstance(job.get('location'), dict) else {}
    country = (loc_obj.get('countryName') or '').strip()
    countries = [country] if country else []
    for k in ('city', 'subregion', 'countryName'):
        val = (loc_obj.get(k) or '').strip()
        if val and not any(val.lower() in part.lower() for part in locations):
            locations.append(val)
    desc_parts = [job.get('description') or '', job.get('requirementsSection') or '', job.get('benefitsSection') or '']
    description = '\n'.join(part for part in desc_parts if isinstance(part, str) and part.strip())
    return ', '.join(dict.fromkeys(locations)), country, countries, description


class WorkableSearchAdapter:
    """Bounded, rate-limited adapter for Workable's public cross-employer search endpoint."""

    mechanism = 'workable_search_v1'

    def __init__(
        self,
        *,
        queries: tuple[dict, ...] = DEFAULT_WORKABLE_UK_QUERIES,
        max_search_requests: int = 20,
        max_widget_requests: int = 35,
        max_resolve_candidates: int = 25,
        search_delay_s: float = 0.5,
        widget_delay_s: float = 1.5,
        get_json: Callable[[str], tuple[int, dict, int]] = _http_get_json,
        sleep_fn: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ):
        if not 1 <= max_search_requests <= 100 or not 0 <= max_widget_requests <= 100:
            raise ValueError('Discovery request budgets must be strictly bounded (search 1..100, widget 0..100)')
        if search_delay_s < 0 or widget_delay_s < 0:
            raise ValueError('Rate-limit delays must be non-negative')
        self.queries = queries
        self.max_search_requests = max_search_requests
        self.max_widget_requests = max_widget_requests
        self.max_resolve_candidates = max_resolve_candidates
        self.search_delay_s = search_delay_s
        self.widget_delay_s = widget_delay_s
        self.get_json = get_json
        self.sleep_fn = sleep_fn
        self.clock = clock

    def discover(self) -> DiscoveryReport:
        now = self.clock().astimezone(timezone.utc)
        now_iso = now.isoformat().replace('+00:00', 'Z')
        search_reqs = 0
        widget_reqs = 0
        total_bytes = 0
        raw_hits = 0
        dup_hits = 0
        rate_limited = False
        errors: list[dict] = []

        seen_by_uuid: dict[str, dict] = {}
        matched_queries: dict[str, list[str]] = {}

        for qspec in self.queries:
            if search_reqs >= self.max_search_requests or rate_limited:
                break
            base_params = {k: v for k, v in qspec.items() if k != 'max_pages'}
            max_pages = int(qspec.get('max_pages', 1))
            qlabel = base_params.get('query', '') + (f" (day_range={base_params['day_range']})" if 'day_range' in base_params else '')
            token = None

            for _ in range(max_pages):
                if search_reqs >= self.max_search_requests or rate_limited:
                    break
                params = dict(base_params)
                if token:
                    params['pageToken'] = token
                url = 'https://jobs.workable.com/api/v1/jobs?' + urlencode(params)
                if search_reqs > 0 and self.search_delay_s:
                    self.sleep_fn(self.search_delay_s)
                status, payload, nbytes = self.get_json(url)
                search_reqs += 1
                total_bytes += nbytes
                if status == 429:
                    rate_limited = True
                    errors.append({'stage': 'search', 'status': 429, 'query': qlabel})
                    break
                if status != 200 or not isinstance(payload, dict) or not isinstance(payload.get('jobs'), list):
                    errors.append({'stage': 'search', 'status': status, 'query': qlabel})
                    break

                jobs = payload['jobs']
                for job in jobs:
                    if not isinstance(job, dict) or not job.get('id'):
                        continue
                    raw_hits += 1
                    jid = str(job['id'])
                    if jid in seen_by_uuid:
                        dup_hits += 1
                        if qlabel not in matched_queries[jid]:
                            matched_queries[jid].append(qlabel)
                        continue
                    seen_by_uuid[jid] = job
                    matched_queries[jid] = [qlabel]

                token = payload.get('nextPageToken')
                if not token or not jobs:
                    break

        # Screen hits first so bounded widget resolution focuses on priority/review candidates.
        screened_order: list[tuple[int, str, str]] = []
        for jid, job in seen_by_uuid.items():
            loc_text, country, countries, desc = _search_job_inputs(job)
            verdict = filtering.screen({
                'title': (job.get('title') or '').strip(),
                'location': loc_text,
                'country': country,
                'countries': countries,
                'department': (job.get('department') or '').strip(),
                'team': '',
                'employment_type': (job.get('employmentType') or '').strip(),
                'description': desc,
            })['verdict']
            rank = 0 if verdict == 'priority' else 1 if verdict == 'review' else 2
            screened_order.append((rank, str(job.get('created') or ''), jid))
        screened_order.sort(key=lambda x: (x[0], x[2]))

        widget_cache: dict[str, tuple[int, dict]] = {}
        resolved_by_uuid: dict[str, tuple[feeds.Posting, str]] = {}
        resolve_budget = [jid for rank, _, jid in screened_order if rank < 2][: self.max_resolve_candidates]

        for jid in resolve_budget:
            if widget_reqs >= self.max_widget_requests or rate_limited:
                break
            job = seen_by_uuid[jid]
            company = job.get('company') if isinstance(job.get('company'), dict) else {}
            for slug in candidate_workable_slugs(company):
                if slug in widget_cache:
                    st, wp = widget_cache[slug]
                else:
                    if widget_reqs >= self.max_widget_requests or rate_limited:
                        break
                    if widget_reqs > 0 and self.widget_delay_s:
                        self.sleep_fn(self.widget_delay_s)
                    wurl = f'https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true'
                    st, wp, nbytes = self.get_json(wurl)
                    widget_reqs += 1
                    total_bytes += nbytes
                    if st == 429:
                        rate_limited = True
                        errors.append({'stage': 'widget', 'status': 429, 'slug': slug})
                        break
                    widget_cache[slug] = (st, wp)
                if st == 200 and isinstance(wp, dict):
                    posting, basis = corroborate_widget_posting(job, slug, wp)
                    if posting is not None:
                        resolved_by_uuid[jid] = (posting, basis)
                        break

        # Deduplicate final output when multiple search UUIDs resolve to one canonical shortcode.
        deduped: dict[str, DiscoveredVacancy] = {}
        for jid, job in seen_by_uuid.items():
            company_obj = job.get('company') if isinstance(job.get('company'), dict) else {}
            resolved = resolved_by_uuid.get(jid)
            if resolved is not None:
                posting, basis = resolved
                key = posting.key
                if key in deduped:
                    dup_hits += 1
                    continue
                ev_sha = sha256(encode_evidence(posting.raw)).hexdigest()
                desc_full = posting.description or ''
                desc_sha = sha256(desc_full.encode('utf-8')).hexdigest() if desc_full else None
                dls = deadline_evidence(posting.raw, desc_full, posting.url, now_iso)
                deduped[key] = DiscoveredVacancy(
                    candidate_key=key,
                    provider='workable',
                    board=posting.board,
                    native_id=posting.external_id,
                    company=posting.company,
                    title=posting.title,
                    posting_url=posting.url,
                    location_text=posting.location,
                    country=posting.country,
                    countries=posting.countries,
                    department=posting.department,
                    team=posting.team,
                    employment_type=posting.employment_type,
                    workplace_type=str(job.get('workplace') or ''),
                    published_at=posting.published_at,
                    source_dates=dict(posting.source_dates),
                    deadlines=dls,
                    description_full=desc_full,
                    description_excerpt=_excerpt(desc_full),
                    description_sha256=desc_sha,
                    evidence_sha256=ev_sha,
                    mechanism=self.mechanism,
                    provenance={
                        'workable_search_uuid': jid,
                        'search_url': job.get('url'),
                        'matched_queries': matched_queries[jid],
                        'widget_corroboration': basis,
                        'company_website': company_obj.get('website'),
                        'authoritative_board_observation': False,
                    },
                )
            else:
                url = (job.get('url') or '').strip()
                title = (job.get('title') or '').strip()
                company = (company_obj.get('title') or '').strip()
                if not (feeds.public_url(url) and title and company):
                    continue
                key = f'discovery:workable:{jid.lower()}'
                loc_text, country, countries, desc_full = _search_job_inputs(job)
                ev_sha = sha256(encode_evidence(job)).hexdigest()
                desc_sha = sha256(desc_full.encode('utf-8')).hexdigest() if desc_full else None
                created = str(job.get('created') or '')
                source_dates = {'created': created} if created else {}
                if job.get('updated'):
                    source_dates['updated'] = str(job['updated'])
                dls = deadline_evidence(job, desc_full, url, now_iso)
                deduped[key] = DiscoveredVacancy(
                    candidate_key=key,
                    provider='workable',
                    board=None,
                    native_id=None,
                    company=company,
                    title=title,
                    posting_url=url,
                    location_text=loc_text,
                    country=country,
                    countries=tuple(countries),
                    department=(job.get('department') or '').strip(),
                    team='',
                    employment_type=(job.get('employmentType') or '').strip(),
                    workplace_type=str(job.get('workplace') or ''),
                    published_at=created[:10] if created else '',
                    source_dates=source_dates,
                    deadlines=dls,
                    description_full=desc_full,
                    description_excerpt=_excerpt(desc_full),
                    description_sha256=desc_sha,
                    evidence_sha256=ev_sha,
                    mechanism=self.mechanism,
                    provenance={
                        'workable_search_uuid': jid,
                        'search_url': url,
                        'matched_queries': matched_queries[jid],
                        'widget_corroboration': None,
                        'company_website': company_obj.get('website'),
                        'authoritative_board_observation': False,
                    },
                )

        return DiscoveryReport(
            mechanism=self.mechanism,
            executed_at=now_iso,
            vacancies=tuple(sorted(deduped.values(), key=lambda v: v.candidate_key)),
            raw_hits=raw_hits,
            duplicate_hits=dup_hits,
            search_requests=search_reqs,
            widget_requests=widget_reqs,
            bytes_transferred=total_bytes,
            rate_limited=rate_limited,
            errors=tuple(errors),
        )


def workable_search_discover(**kwargs) -> DiscoveryReport:
    return WorkableSearchAdapter(**kwargs).discover()
