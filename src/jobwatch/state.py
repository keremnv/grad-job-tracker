"""Discovery index: every observed posting identity, separate from judgments and owner state.

On-disk layout for a state path such as ``data/jobs.json``:

- ``data/jobs.json`` (schema 2): one record per posting ever observed on a
  successful board, *including* excluded ones. Source fields, observation
  fields and the ``screening`` judgment are kept apart. It changes only when
  something material changes.
- ``data/jobs-health.json``: per-board health and the last-scan summary. This
  is the only file an unchanged scan rewrites.
- ``data/jobs-text/<aa>/<sha256>.txt``: exact source description strings,
  content-addressed and immutable. Written before the state that references
  them; unreferenced files are removed after the state is saved.

In memory the three are combined: ``{"schema": 2, "postings", "boards",
"last_scan", "texts"}``. Schema-1 files (and dicts) are upgraded on load.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
from .feeds import Posting
from .filtering import RULES_VERSION, inputs_from_posting, screen
from .timing import deadline_evidence

SCHEMA = 2
SOURCE_FIELDS = ('title', 'location', 'url', 'salary', 'country', 'countries', 'department', 'team',
                 'employment_type', 'published_at', 'source_dates', 'description_sha256')
SCREENING_EVENT_LIMIT = 20
TEXT_RETENTION_DAYS = 180
LEGACY_RULES = 'legacy-schema-1'
SHA = re.compile(r'[0-9a-f]{64}')


def health_path(path: Path) -> Path:
    return path.with_name(path.stem + '-health.json')


def text_dir(path: Path) -> Path:
    return path.with_name(path.stem + '-text')


def text_hash(value: str) -> str:
    return sha256(value.encode('utf-8')).hexdigest()


def _legacy_metadata(provider, raw):
    """Recover screening metadata from a schema-1 `raw` payload before dropping it."""
    raw = raw if isinstance(raw, dict) else {}
    def s(value):
        return value.strip() if isinstance(value, str) else ''
    if provider == 'greenhouse':
        names = [s(d.get('name')) for d in raw.get('departments') or [] if isinstance(d, dict)]
        return {'department': ', '.join(x for x in names if x), 'team': '', 'employment_type': ''}
    if provider == 'lever':
        c = raw.get('categories') if isinstance(raw.get('categories'), dict) else {}
        return {'department': s(c.get('department')), 'team': s(c.get('team')), 'employment_type': s(c.get('commitment'))}
    return {'department': s(raw.get('department')), 'team': s(raw.get('team')), 'employment_type': s(raw.get('employmentType'))}


def upgrade(state: dict) -> dict:
    """Upgrade a schema-1 state in place. Deterministic; schema 2 is returned unchanged.

    Inline descriptions move to ``texts``; ``raw`` payloads are dropped after
    extracting department/team metadata (they remain in Git history); the
    legacy mixed source/judgment ``history`` is preserved verbatim as
    ``legacy_history``; the old judgment is kept as a ``legacy-schema-1``
    screening so the next screening pass records an explicit rescreen."""
    state.setdefault('texts', {})
    state.setdefault('boards', {})
    if state.get('schema') == SCHEMA:
        return state
    if state.get('schema') != 1:
        raise ValueError('unknown or invalid state schema; refusing to overwrite')
    for row in state['postings'].values():
        if 'screening' in row:
            continue
        description = row.pop('description', None)
        if isinstance(description, str):
            state['texts'][text_hash(description)] = description
            row['description_sha256'] = text_hash(description)
            row['description_chars'] = len(description)
        raw = row.pop('raw', None)
        for k, v in _legacy_metadata(row.get('provider'), raw).items():
            row.setdefault(k, v)
        row['screening'] = {'verdict': row.pop('priority', 'review'), 'family': row.pop('role_family', None) or 'other',
                            'codes': [], 'explanation': row.pop('reason', ''), 'rules_version': LEGACY_RULES,
                            'basis': 'legacy', 'input_sha256': None, 'screened_at': row.get('last_seen') or row.get('first_seen')}
        row.pop('location_judgment', None)
        if 'history' in row:
            row['legacy_history'] = row.pop('history')
        row.setdefault('first_seen_basis', 'legacy_retained')
        row.setdefault('source_events', [])
        row.setdefault('screening_events', [])
        if row.get('feed_status') == 'listed':
            row.pop('last_seen', None)  # derived from board health while listed
    state['schema'] = SCHEMA
    state['migration'] = {'from_schema': 1, 'reference_scan': state.get('last_scan', {}).get('at'),
                          'note': 'Schema-1 raw payloads and inline descriptions were removed or moved to '
                                  'content-addressed text; legacy history is preserved verbatim and mixes '
                                  'source and judgment changes. Excluded postings never retained before this '
                                  'migration cannot be reconstructed. See Git history before the migration commit.'}
    for health in state['boards'].values():
        health.setdefault('index_complete_since', None)
    return state


def _validate(data):
    if not isinstance(data, dict) or data.get('schema') not in (1, SCHEMA) or not isinstance(data.get('postings'), dict):
        raise ValueError('unknown or invalid state schema; refusing to overwrite')
    required = ('provider', 'board', 'title', 'company', 'first_seen', 'feed_status')
    for key, row in data['postings'].items():
        if not isinstance(row, dict) or any(k not in row for k in required):
            raise ValueError(f'invalid posting state: {key}; refusing to overwrite')
        if data['schema'] == SCHEMA and not isinstance(row.get('screening'), dict):
            raise ValueError(f'invalid posting state: {key}; missing screening; refusing to overwrite')
        if row['feed_status'] not in ('listed', 'not_seen'):
            raise ValueError(f'invalid posting state: {key}; unknown feed status; refusing to overwrite')


def load_state(path: Path) -> dict:
    path = Path(path)
    if not path.exists():
        return {'schema': SCHEMA, 'postings': {}, 'boards': {}, 'texts': {}}
    data = json.loads(path.read_text(encoding='utf-8'))
    _validate(data)
    hp = health_path(path)
    if hp.exists():
        health = json.loads(hp.read_text(encoding='utf-8'))
        if not isinstance(health, dict) or health.get('schema') != 1 or not isinstance(health.get('boards'), dict):
            raise ValueError('invalid scan health file; refusing to overwrite')
        data['boards'] = health['boards']
        if 'last_scan' in health:
            data['last_scan'] = health['last_scan']
    data['texts'] = {}
    upgrade(data)
    directory = text_dir(path)
    for row in data['postings'].values():
        for sha in _row_texts(row):
            if sha in data['texts']:
                continue
            file = directory / sha[:2] / (sha + '.txt')
            if file.exists():
                content = file.read_bytes().decode('utf-8')
                if text_hash(content) != sha:
                    raise ValueError(f'source text {sha[:12]} does not match its hash; refusing to continue')
                data['texts'][sha] = content
    return data


def _row_texts(row):
    """Text hashes a record can still use: current description, and the
    'before' side of its most recent description change (for diffs)."""
    shas = [row.get('description_sha256')]
    for event in reversed(row.get('source_events', [])):
        change = event.get('changes', {}).get('description_sha256')
        if change:
            shas.append(change.get('before'))
            break
    return [s for s in shas if isinstance(s, str) and SHA.fullmatch(s)]


def referenced_texts(state: dict, now: str | None = None) -> set[str]:
    """Texts to retain. Postings unseen for longer than TEXT_RETENTION_DAYS
    release their text; Git history still holds it."""
    cutoff = _parse(now) - timedelta(days=TEXT_RETENTION_DAYS) if now else None
    keep = set()
    for row in state['postings'].values():
        if cutoff and row.get('feed_status') == 'not_seen' and row.get('missing_since') and _parse(row['missing_since']) < cutoff:
            continue
        keep.update(_row_texts(row))
    return keep


def _parse(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def description_of(state: dict, row: dict):
    """Exact source description string, '' when the source had none, None when not retained."""
    if 'description' in row:  # schema-1 record held in memory
        return row['description']
    sha = row.get('description_sha256')
    if sha is None:
        return None
    return state.get('texts', {}).get(sha)


def description_sha(row: dict):
    if 'description' in row:
        return text_hash(row['description'])
    return row.get('description_sha256')


def last_seen(row: dict, boards: dict | None) -> str | None:
    """Last successful observation that listed the posting."""
    if row.get('feed_status') == 'listed':
        board = (boards or {}).get(f"{row.get('provider')}:{str(row.get('board')).lower()}", {})
        return board.get('last_success') or row.get('last_seen')
    return row.get('last_seen')


def stored_inputs(state: dict, row: dict) -> dict:
    return {'title': row.get('title', ''), 'location': row.get('location', ''), 'country': row.get('country', ''),
            'countries': row.get('countries', []), 'department': row.get('department', ''),
            'team': row.get('team', ''), 'employment_type': row.get('employment_type', ''),
            'description': description_of(state, row)}


def _apply_screening(key, row, result, now, events):
    """Record a judgment. Only verdict/code changes produce a `rescreened` event."""
    prior = row.get('screening')
    # Explanations are derived from codes on read (filtering.explanation_of); only codes are stored.
    current = {**{k: v for k, v in result.items() if k != 'explanation'}, 'screened_at': now}
    if prior is None:
        row['screening'] = current
        return
    same = prior.get('verdict') == result['verdict'] and prior.get('codes') == result['codes']
    if same and prior.get('rules_version') == result['rules_version'] and prior.get('input_sha256') == result['input_sha256'] \
            and prior.get('basis') == result['basis'] and 'explanation' not in prior:
        return
    if not same:
        rules_changed = prior.get('rules_version') != result['rules_version']
        input_changed = prior.get('input_sha256') != result['input_sha256']
        cause = ('rules_and_source_changed' if rules_changed and input_changed else 'rules_changed' if rules_changed
                 else 'source_changed' if input_changed else 'rules_changed_unversioned')
        if prior.get('rules_version') == LEGACY_RULES:
            cause = 'legacy_migration'
        event = {'type': 'rescreened', 'category': 'screening', 'key': key, 'title': row['title'],
                 'company': row['company'], 'at': now, 'cause': cause,
                 'before': {k: prior.get(k) for k in ('verdict', 'codes', 'rules_version')},
                 'after': {k: result[k] for k in ('verdict', 'codes', 'rules_version')}}
        events.append(event)
        row.setdefault('screening_events', []).append({k: v for k, v in event.items() if k not in ('key', 'title', 'company', 'category')})
        del row['screening_events'][:-SCREENING_EVENT_LIMIT]
    row['screening'] = current if not same else {**current, 'screened_at': prior.get('screened_at', now)}


def _source(p: Posting, sha: str, now: str) -> dict:
    return {'company': p.company, 'provider': p.provider, 'board': p.board, 'external_id': p.external_id,
            'title': p.title, 'location': p.location, 'url': p.url, 'salary': p.salary, 'country': p.country,
            'countries': list(p.countries), 'department': p.department, 'team': p.team,
            'employment_type': p.employment_type, 'published_at': p.published_at, 'source_dates': p.source_dates,
            'description_sha256': sha, 'description_chars': len(p.description),
            'deadlines': deadline_evidence(p.raw, p.description, p.url, now)}


def _strip_observed(deadlines):
    return [{k: v for k, v in d.items() if k != 'observed_at'} for d in deadlines]


def merge(state: dict, successful: dict[str, list[Posting]], now: str) -> tuple[dict, list[dict]]:
    """Fold successful board observations into the index and rescreen every record.

    Feed presence never depends on relevance: every posting from a successful
    board is recorded. Boards absent from `successful` (failed, disabled) keep
    their source state untouched; their records are only rescreened."""
    upgrade(state)
    existing, boards, texts, events = state['postings'], state['boards'], state['texts'], []
    observed = set()
    for boardkey, posts in sorted(successful.items()):
        health = boards.setdefault(boardkey, {})
        previous_success = health.get('last_success')
        backfill = previous_success is not None and health.get('index_complete_since', 'unset') is None
        seen = set()
        for p in posts:
            if f"{p.provider}:{p.board.lower()}" != boardkey:
                raise ValueError("posting belongs to another board")
            seen.add(p.key)
            observed.add(p.key)
            sha = text_hash(p.description)
            texts[sha] = p.description
            snapshot = _source(p, sha, now)
            prior = existing.get(p.key)
            if prior is None:
                basis = 'index_backfill' if backfill else 'observed'
                row = {**snapshot, 'first_seen': now, 'first_seen_basis': basis, 'feed_status': 'listed',
                       'application_verification': {'status': 'unknown'}, 'source_events': [], 'screening_events': []}
                existing[p.key] = row
                events.append({'type': 'backfilled' if backfill else 'new', 'category': 'source', 'key': p.key,
                               'title': p.title, 'company': p.company, 'at': now})
            else:
                row = prior
                changes = {}
                for k in SOURCE_FIELDS:
                    if k in ('department', 'team', 'employment_type') and k not in prior:
                        continue  # metadata first collected now is not an employer edit
                    before = description_sha(prior) if k == 'description_sha256' else prior.get(k)
                    if before != snapshot[k]:
                        changes[k] = {'before': before, 'after': snapshot[k]}
                if _strip_observed(prior.get('deadlines', [])) != _strip_observed(snapshot['deadlines']):
                    changes['deadlines'] = {'before': prior.get('deadlines', []), 'after': snapshot['deadlines']}
                else:
                    snapshot['deadlines'] = prior.get('deadlines', [])  # keep first observation time
                event_type = 'reappeared' if prior.get('feed_status') == 'not_seen' else 'changed' if changes else None
                prior.pop('description', None)
                prior.pop('raw', None)
                prior.update(snapshot)
                if event_type:
                    event = {'type': event_type, 'category': 'source', 'key': p.key, 'title': p.title,
                             'company': p.company, 'at': now, 'changes': changes}
                    events.append(event)
                    prior.setdefault('source_events', []).append({k: v for k, v in event.items()
                                                                  if k not in ('key', 'title', 'company', 'category')})
                if prior.get('feed_status') == 'not_seen':
                    prior['feed_status'] = 'listed'
                    prior.pop('missing_since', None)
                    prior.pop('last_seen', None)
            _apply_screening(p.key, row, screen(inputs_from_posting(p)), now, events)
        for key, prior in existing.items():
            if (f"{prior.get('provider')}:{str(prior.get('board')).lower()}" == boardkey
                    and key not in seen and prior.get("feed_status") == "listed"):
                prior["feed_status"], prior["missing_since"] = "not_seen", now
                prior['last_seen'] = previous_success or prior.get('last_seen')
                event = {"type": "not_seen", 'category': 'source', "key": key, "title": prior["title"],
                         "company": prior["company"], "at": now}
                prior.setdefault('source_events', []).append({'type': 'not_seen', 'at': now})
                events.append(event)
        health['last_success'] = now
        if health.get('index_complete_since') is None:
            health['index_complete_since'] = now
    # Judgments of records not observed now are refreshed from stored source fields.
    for key, row in sorted(existing.items()):
        if key in observed:
            continue
        screening = row.get('screening') or {}
        if screening.get('rules_version') != RULES_VERSION:
            _apply_screening(key, row, screen(stored_inputs(state, row)), now, events)
    return state, events


def rescreen_preview(state: dict, now: str) -> list[dict]:
    """What the current rules would change, without writing anything."""
    preview = []
    for key, row in sorted(state['postings'].items()):
        result = screen(stored_inputs(state, row))
        prior = row.get('screening') or {}
        if prior.get('verdict') != result['verdict'] or prior.get('codes') != result['codes']:
            preview.append({'key': key, 'company': row['company'], 'title': row['title'],
                            'before': {k: prior.get(k) for k in ('verdict', 'codes', 'rules_version')},
                            'after': {k: result[k] for k in ('verdict', 'codes', 'rules_version')},
                            'basis': result['basis']})
    return preview


def serialise(state: dict, now: str | None = None) -> tuple[str, str]:
    """Return (jobs.json content, health content)."""
    postings = {}
    for key, row in state['postings'].items():
        row = dict(row)
        row.pop('description', None); row.pop('raw', None)
        postings[key] = row
    jobs = {'schema': SCHEMA, 'postings': postings}
    if state.get('migration'):
        jobs['migration'] = state['migration']
    health = {'schema': 1, 'boards': state.get('boards', {})}
    if 'last_scan' in state:
        health['last_scan'] = state['last_scan']
    dump = lambda value: json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    return dump(jobs), dump(health)


def save_state(path: Path, state: dict, now: str | None = None) -> None:
    """Write texts first, then state and health atomically, then remove unreferenced texts."""
    path = Path(path)
    upgrade(state)
    keep = referenced_texts(state, now)
    directory = text_dir(path)
    for sha in sorted(keep):
        content = state['texts'].get(sha)
        file = directory / sha[:2] / (sha + '.txt')
        if content is None or file.exists():
            continue
        if text_hash(content) != sha:
            raise ValueError('source text does not match its hash; no state written')
        atomic_write(file, content)
    jobs, health = serialise(state, now)
    atomic_write(path, jobs)
    atomic_write(health_path(path), health)
    if directory.is_dir():
        for file in directory.glob('*/*.txt'):
            if file.stem not in keep and SHA.fullmatch(file.stem):
                file.unlink()
        for sub in directory.iterdir():
            if sub.is_dir() and not any(sub.iterdir()):
                sub.rmdir()


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".jobwatch-", delete=False) as f:
        tmp = Path(f.name)
        try:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    try:
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
