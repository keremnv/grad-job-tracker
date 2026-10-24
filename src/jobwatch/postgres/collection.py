"""Network/validation boundary and immutable publication input; no database locks here."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import json
import time
from typing import Callable
from uuid import UUID

from jobwatch import feeds, filtering
from jobwatch.timing import deadline_evidence
from .evidence import encode_evidence
from .material import COMPARISON_VERSION

REQUESTS = {'greenhouse': 'greenhouse:list-content-v1', 'lever': 'lever:offset100-v1', 'ashby': 'ashby:compensation-v1'}
NORMALIZER_VERSION = 'normalizer-v1'


def iso(value):
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('Observation time requires a timezone')
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def safe(value):
    if isinstance(value, str):
        value = value.replace('\0', '\ufffd')
        value.encode('utf-8')  # reject unpaired surrogates, never invent Unicode evidence
    elif isinstance(value, (list, tuple)):
        value = [safe(v) for v in value]
    elif isinstance(value, dict):
        pairs = [(safe(k), safe(v)) for k, v in value.items()]
        value = dict(pairs)
        if len(value) != len(pairs):
            raise ValueError('NUL replacement would merge distinct object keys')
    return value


def normalize_v1(posting, at):
    return {'company': posting.company, 'title': posting.title, 'posting_url': posting.url,
        'location_text': posting.location, 'country': posting.country, 'countries': list(posting.countries),
        'department': posting.department, 'team': posting.team, 'employment_type': posting.employment_type,
        'salary': posting.salary, 'published_at': posting.published_at, 'source_dates': posting.source_dates,
        'description_text': posting.description, 'deadlines': deadline_evidence(posting.raw, posting.description, posting.url, at),
        'extra_fields': {}}


def inputs(snapshot):
    return {**{k: snapshot[k] for k in ('title', 'country', 'countries', 'department', 'team', 'employment_type')},
            'location': snapshot['location_text'], 'description': snapshot['description_text']}


def content(snapshot):
    # Observation times are not normalized content. Preserve first observation of unchanged deadline wording.
    return {**snapshot, 'deadlines': [{k: v for k, v in d.items() if k != 'observed_at'} for d in snapshot['deadlines']]}


@dataclass(frozen=True)
class Processing:
    revision: str = 'pipeline-v1'
    normalizer_version: str = NORMALIZER_VERSION
    rules_version: str = filtering.RULES_VERSION
    normalize: Callable = field(default=normalize_v1, repr=False)
    screen: Callable = field(default=filtering.screen, repr=False)

    def __post_init__(self):
        if not all(isinstance(v, str) and v.strip() for v in (self.revision, self.normalizer_version, self.rules_version)):
            raise ValueError('Processing release identifiers must be nonempty')

    def judge(self, snapshot):
        result = self.screen(inputs(snapshot))
        if result['rules_version'] != self.rules_version:
            raise ValueError('Screener does not match its pinned rules version')
        return {k: result[k] for k in ('verdict', 'family', 'codes', 'rules_version', 'basis', 'input_sha256')}


@dataclass(frozen=True)
class Run:
    id: UUID
    source_id: int
    run_order: int
    started_at: datetime
    request_version: str
    processing_revision: str
    status: str
    details: bytes = field(repr=False)

    @property
    def metadata(self):
        return json.loads(self.details)


@dataclass(frozen=True)
class Record:
    key: str
    native_id: str
    evidence: bytes = field(repr=False)
    normalized: bytes = field(repr=False)
    screening: bytes = field(repr=False)
    nul_replaced: bool = False


@dataclass(frozen=True)
class Observation:
    run: Run
    records: tuple[Record, ...]
    fetched_at: datetime
    source_records: int
    skipped: int
    request_count: int
    absence_safe: bool

    @property
    def digest(self):
        return sha256(encode_evidence({'run_id': str(self.run.id), 'source_id': self.run.source_id,
            'receipt': iso(self.fetched_at), 'request': self.run.request_version,
            'revision': self.run.processing_revision, 'source_records': self.source_records,
            'skipped': self.skipped, 'request_count': self.request_count, 'absence_safe': self.absence_safe,
            'records': [{'key': r.key, 'native_id': r.native_id, 'evidence': sha256(r.evidence).hexdigest(),
                         'normalized': sha256(r.normalized).hexdigest(), 'screening': sha256(r.screening).hexdigest(),
                         'nul_replaced': r.nul_replaced} for r in self.records]})).hexdigest()


def prepare(posting, at, processing):
    native = encode_evidence(posting.raw)
    original = processing.normalize(posting, iso(at))
    snapshot = safe(original)
    if set(snapshot) != set(normalize_v1(posting, iso(at))):
        raise ValueError('Normalizer must produce the stored posting contract')
    # Identity URLs must already have passed provider validation, never be repaired with replacement characters.
    if snapshot['posting_url'] != posting.url or not feeds.public_url(snapshot['posting_url']):
        raise ValueError('Normalizer changed posting identity URL')
    return Record(posting.key, posting.external_id, native, encode_evidence(snapshot),
                  encode_evidence(processing.judge(snapshot)), snapshot != original)


def replay_native(entry, raw):
    provider = entry['provider']
    response = [raw] if provider == 'lever' else {'jobs': [raw], **({'meta': {'total': 1}} if provider == 'greenhouse' else {'apiVersion': '1'})}
    rows = feeds.collect_board(entry, get_json=lambda _: response, pause=lambda _: None)
    if len(rows) != 1:
        raise ValueError('Retained evidence is not a normalizable vacancy')
    return rows[0]


def fetch_records(entry, *, get_json=feeds.request_json, pause=time.sleep, clock=lambda: datetime.now(timezone.utc)):
    """Validate a feed and certify its coverage without screening or publishing it."""
    responses = []
    receipts = []

    def get(url):
        response = get_json(url)
        responses.append(response)
        receipts.append(clock())
        return response

    stats = {}
    postings = feeds.collect_board(entry, get_json=get, pause=pause, stats=stats)
    if any(isinstance(response, dict) and any(response.get(k) for k in ('hasMore', 'nextCursor', 'nextPage', 'next')) for response in responses):
        raise ValueError('Response explicitly indicates an incomplete/paginated feed')
    at = receipts[-1]
    iso(at)
    if len({p.key for p in postings}) != len(postings) or stats['source_records'] != len(postings) + stats['skipped_non_vacancies']:
        raise ValueError('Collection accounting mismatch')
    response = responses[0]
    no_paging = isinstance(response, dict) and not any(response.get(k) for k in ('hasMore', 'nextCursor', 'nextPage', 'next'))
    if entry['provider'] == 'greenhouse':
        total = response.get('meta', {}).get('total')
        absence_safe = len(responses) == 1 and no_paging and type(total) is int and total == stats['source_records']
    elif entry['provider'] == 'ashby':
        absence_safe = len(responses) == 1 and no_paging and response.get('apiVersion') == '1'
    else:
        absence_safe = len(responses) == 1 and len(response) < 100
    return postings, at, stats, len(responses), absence_safe


def fetch(run, processing=Processing(), *, get_json=feeds.request_json, pause=time.sleep, clock=lambda: datetime.now(timezone.utc)):
    if run.status != 'running' or run.processing_revision != processing.revision:
        raise ValueError('Fetch requires a running registration for this processing release')
    postings, at, stats, count, absence_safe = fetch_records(run.metadata['source'],get_json=get_json,pause=pause,clock=clock)
    records = tuple(sorted((prepare(p, at, processing) for p in postings),key=lambda r:r.key))
    return Observation(run,records,at,stats['source_records'],stats['skipped_non_vacancies'],count,absence_safe)
