"""Separately recorded checks of official pages (`data/verifications.json`).

Two kinds of evidence are kept apart:

- **Source observations** an agent may research and record on its own after a
  read-only visit: deadline wording (`deadline`) and a description check
  (`description`, bound to the indexed text by SHA-256).
- **Application-form status** (`status`: open_verified / closed_verified): a
  claim that a form accepts or refuses submissions *at check time*. It is
  recorded only by the owner or by an agent on explicit owner instruction.

Neither establishes the owner's eligibility. Every write records
`verified_by`; records written before this field existed are reported as
"unrecorded (legacy record)".
"""
from __future__ import annotations

import json
from pathlib import Path
import re

from .calendar import ZONED_TIME
from .config import timestamp
from .contract import JobwatchError
from .feeds import public_url
from .identity import is_posting_key
from .storage import locked, replace_checked, snapshot

STATUSES = {'unknown', 'open_verified', 'closed_verified'}
OBSERVERS = ('owner', 'agent', 'agent_on_owner_instruction')
STATUS_OBSERVERS = ('owner', 'agent_on_owner_instruction')
LABEL = 'verification ledger'
CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')


def validate(data):
    if not isinstance(data, dict) or data.get("schema") != 1 or not isinstance(data.get("postings"), dict):
        raise ValueError("invalid verification ledger")
    for row in data["postings"].values():
        if not isinstance(row, dict) or row.get("status") not in STATUSES:
            raise ValueError("invalid verification status")
        if row["status"] != "unknown":
            timestamp(row.get("verified_at"))
            if not public_url(row.get("url", "")):
                raise ValueError("verification requires source URL")
        if row.get('verified_by') is not None and row['verified_by'] not in OBSERVERS:
            raise ValueError("unknown verification observer")
    return data["postings"]


def load_verifications(path):
    path = Path(path)
    if not path.exists():
        return {}
    return validate(json.loads(path.read_text(encoding="utf-8")))


def _write(path, mutate):
    path = Path(path)
    with locked(path, LABEL):
        before = snapshot(path, LABEL)
        data = json.loads(before[0].decode('utf-8')) if before else {'schema': 1, 'postings': {}}
        validate(data)
        status = mutate(data['postings'])
        if status != 'unchanged':
            validate(data)
            replace_checked(path, json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + '\n', before, LABEL)
    return status


def _check(key, postings, url, verified_at, now, verified_by, allowed=OBSERVERS):
    if not is_posting_key(key) or key not in postings:
        raise JobwatchError('unknown_opportunity', f'{key} is not in the discovery index', key=key)
    if not public_url(url or ''):
        raise JobwatchError('invalid_argument', '--url must be the public HTTPS page that was checked')
    try:
        checked = timestamp(verified_at)
    except (ValueError, TypeError):
        raise JobwatchError('invalid_argument', '--verified-at must be a timezone-aware timestamp') from None
    if checked > timestamp(now):
        raise JobwatchError('invalid_argument', '--verified-at is in the future')
    if verified_by not in allowed:
        raise JobwatchError('invalid_argument', f'--verified-by must be one of {", ".join(allowed)} for this evidence')


def record_status(path, postings, key, status, url, method, verified_by, verified_at, now, replace=False):
    _check(key, postings, url, verified_at, now, verified_by, STATUS_OBSERVERS)
    if status not in STATUSES or status == 'unknown':
        raise JobwatchError('invalid_argument', '--status must be open_verified or closed_verified')
    if not method or CONTROL.search(method):
        raise JobwatchError('invalid_argument', '--method must describe the read-only check performed')

    def mutate(rows):
        row = rows.setdefault(key, {'status': 'unknown'})
        new = {'status': status, 'url': url, 'method': method, 'verified_at': verified_at, 'verified_by': verified_by}
        if all(row.get(k) == v for k, v in new.items() if k != 'verified_at') and row.get('verified_at'):
            return 'unchanged'
        if row.get('status', 'unknown') != 'unknown' and row['status'] != status and not replace:
            raise JobwatchError('verification_conflict', f'{key} already recorded as {row["status"]}', key=key,
                                existing={k: row.get(k) for k in ('status', 'verified_at', 'verified_by')})
        row.update(new)
        return 'recorded'
    return _write(path, mutate)


def record_deadline(path, postings, key, wording, url, value, verified_by, verified_at, now):
    _check(key, postings, url, verified_at, now, verified_by)
    if not wording or not wording.strip() or CONTROL.search(wording):
        raise JobwatchError('invalid_argument', '--wording must quote the source text exactly')
    if value is not None and not (re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) or ZONED_TIME.fullmatch(value)):
        raise JobwatchError('invalid_argument', '--value must be YYYY-MM-DD or an ISO timestamp with Z/offset, and only if the source states it')

    def mutate(rows):
        row = rows.setdefault(key, {'status': 'unknown'})
        evidence = {'wording': wording, 'url': url, 'verified_at': verified_at, 'verified_by': verified_by}
        if value is not None:
            evidence['value'] = value
        same = lambda d: (d.get('wording'), d.get('url'), d.get('value')) == (wording, url, value)
        if any(same(d) for d in row.get('deadlines', [])):
            return 'unchanged'
        row.setdefault('deadlines', []).append(evidence)
        return 'recorded'
    return _write(path, mutate)


def record_description(path, postings, key, url, description_sha256, verified_by, verified_at, now):
    """Bind a read-only description check to the exact indexed text hash."""
    _check(key, postings, url, verified_at, now, verified_by)
    if not description_sha256:
        raise JobwatchError('invalid_argument', 'indexed posting has no retained description to bind the check to')

    def mutate(rows):
        row = rows.setdefault(key, {'status': 'unknown'})
        if row.get('description_sha256') == description_sha256 and row.get('description_url') == url:
            return 'unchanged'
        row.update(description_url=url, description_verified_at=verified_at,
                   description_sha256=description_sha256, description_verified_by=verified_by)
        return 'recorded'
    return _write(path, mutate)
