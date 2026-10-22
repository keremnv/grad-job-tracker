"""Private owner triage: a decision about whether to pursue a discovered opportunity.

Triage is not an application stage. It answers "do I want this in my queue?"
before any application exists; the ledger records what was actually done.
An absent record means *unreviewed* — never inferred from screening, feed
absence or the ledger.

File: ``data/triage.json``::

    {"schema": 1, "decisions": {"<posting key>": {
        "decision": "interested" | "dismissed" | "later",
        "reason_code": "<REASONS>" | null,
        "note": "<private owner text>" | null,
        "revisit_on": "YYYY-MM-DD" | null,      # only with "later"
        "decided_at": "<UTC timestamp>",
        "recorded_by": "owner" | "agent_on_owner_instruction"}}}
"""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import re

from .config import timestamp
from .contract import JobwatchError
from .identity import exact_url, is_posting_key
from .storage import locked, replace_checked, snapshot

DECISIONS = ('interested', 'dismissed', 'later')
REASONS = ('not_engineering', 'not_entry_level', 'location', 'eligibility', 'employer', 'role_fit',
           'duplicate', 'closed_or_expired', 'already_handled', 'other')
# Dismissal reasons that are judgments about relevance (usable as evaluation
# labels) rather than personal preference about a relevant role.
RELEVANCE_REASONS = {'not_engineering', 'not_entry_level'}
RECORDERS = ('owner', 'agent_on_owner_instruction')
FIELDS = ('decision', 'reason_code', 'note', 'revisit_on', 'decided_at', 'recorded_by')
LABEL = 'triage file'


def _validate_record(key, row):
    if not is_posting_key(key):
        raise JobwatchError('invalid_state', f'triage key is not a posting identity: {key[:80]!r}')
    if not isinstance(row, dict) or set(row) != set(FIELDS):
        raise JobwatchError('invalid_state', f'triage record {key} must contain exactly {", ".join(FIELDS)}')
    if row['decision'] not in DECISIONS:
        raise JobwatchError('invalid_state', f'triage record {key}: unknown decision')
    if row['reason_code'] is not None and row['reason_code'] not in REASONS:
        raise JobwatchError('invalid_state', f'triage record {key}: unknown reason code')
    if row['note'] is not None and (not isinstance(row['note'], str) or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', row['note'])):
        raise JobwatchError('invalid_state', f'triage record {key}: note must be text without control characters')
    if row['revisit_on'] is not None:
        if row['decision'] != 'later' or not isinstance(row['revisit_on'], str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', row['revisit_on']):
            raise JobwatchError('invalid_state', f'triage record {key}: revisit_on needs decision "later" and YYYY-MM-DD')
        date.fromisoformat(row['revisit_on'])
    timestamp(row['decided_at'])
    if row['recorded_by'] not in RECORDERS:
        raise JobwatchError('invalid_state', f'triage record {key}: unknown recorder')


def parse(content: bytes | None) -> dict:
    if content is None:
        return {}
    try:
        data = json.loads(content.decode('utf-8'))
    except (UnicodeError, json.JSONDecodeError):
        raise JobwatchError('invalid_state', 'triage file is not valid UTF-8 JSON') from None
    if not isinstance(data, dict) or data.get('schema') != 1 or not isinstance(data.get('decisions'), dict):
        raise JobwatchError('invalid_state', 'triage file needs {"schema": 1, "decisions": {...}}')
    for key, row in data['decisions'].items():
        _validate_record(key, row)
    return data['decisions']


def load(path: Path) -> dict:
    version = snapshot(Path(path), LABEL)
    return parse(version[0] if version else None)


def _render(decisions) -> str:
    return json.dumps({'schema': 1, 'decisions': decisions}, indent=2, sort_keys=True, ensure_ascii=False) + '\n'


def resolve(postings: dict, key: str | None = None, url: str | None = None) -> str:
    """Resolve an owner reference to exactly one indexed posting key."""
    if bool(key) == bool(url):
        raise JobwatchError('invalid_argument', 'supply exactly one of a posting key or --url')
    if key:
        if key not in postings:
            raise JobwatchError('unknown_opportunity', f'{key} is not in the discovery index', key=key)
        return key
    try:
        target = exact_url(url)
    except ValueError:
        raise JobwatchError('invalid_argument', 'URL must be public HTTPS') from None
    matches = []
    for k, row in postings.items():
        try:
            if exact_url(row.get('url', '')) == target:
                matches.append(k)
        except ValueError:
            continue
    if not matches:
        raise JobwatchError('unknown_opportunity', 'no indexed posting has that exact URL')
    if len(matches) > 1:
        raise JobwatchError('ambiguous_identity', 'URL matches several indexed postings', candidates=sorted(matches))
    return matches[0]


def record(path: Path, key: str, decision: str, now: str, reason_code=None, note=None, revisit_on=None,
           recorded_by='owner', replace=False) -> tuple[str, dict]:
    """Write one decision. Returns (status, record); status is created, replaced or unchanged.

    Retrying an identical decision is a no-op ("unchanged"), keeping the
    original `decided_at`. A different existing decision is refused unless
    `replace` is true, so a stale agent instruction cannot silently overwrite."""
    row = {'decision': decision, 'reason_code': reason_code, 'note': note or None,
           'revisit_on': revisit_on or None, 'decided_at': now, 'recorded_by': recorded_by}
    _validate_record(key, row)
    path = Path(path)
    with locked(path, LABEL):
        before = snapshot(path, LABEL)
        decisions = parse(before[0] if before else None)
        existing = decisions.get(key)
        if existing is not None:
            if all(existing[f] == row[f] for f in ('decision', 'reason_code', 'note', 'revisit_on')):
                return 'unchanged', existing
            if not replace:
                raise JobwatchError('triage_conflict', f'{key} already has decision "{existing["decision"]}"',
                                    key=key, existing={f: existing[f] for f in ('decision', 'reason_code', 'decided_at')})
        decisions[key] = row
        replace_checked(path, _render(decisions), before, LABEL)
    return ('replaced' if existing else 'created'), row


def clear(path: Path, key: str) -> str:
    """Remove a decision (back to unreviewed). Idempotent: 'removed' or 'unchanged'."""
    path = Path(path)
    with locked(path, LABEL):
        before = snapshot(path, LABEL)
        decisions = parse(before[0] if before else None)
        if key not in decisions:
            return 'unchanged'
        del decisions[key]
        replace_checked(path, _render(decisions), before, LABEL)
    return 'removed'


def public_view(row: dict | None, include_note=False) -> dict | None:
    """Triage as shown in command output: the note is private unless requested."""
    if row is None:
        return None
    view = {k: row[k] for k in ('decision', 'reason_code', 'revisit_on', 'decided_at', 'recorded_by')}
    view['has_note'] = bool(row.get('note'))
    if include_note:
        view['note'] = row.get('note')
    return view
