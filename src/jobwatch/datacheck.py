"""Validation of the *current operational data*, separate from deterministic unit tests.

Unit tests must not fail because the watchlist changed, a deadline passed or a
hand-maintained record was updated. Those facts are checked here instead, by
``jobwatch validate-data``, which reports errors (a file cannot be loaded or is
internally inconsistent) and warnings (data worth an owner's attention).
"""
from __future__ import annotations

import json
from pathlib import Path

from . import calendar
from . import triage as tri
from .applications import read_applications
from .config import load_config, timestamp
from .contract import error_from, warning
from .feeds import public_url
from .leads import load_leads
from .state import SHA, description_sha, health_path, load_state, referenced_texts, text_dir
from .verifications import load_verifications


def _check(results, errors, name, path, fn):
    path = Path(path)
    if not path.exists():
        results[name] = {'path': str(path), 'status': 'missing'}
        return None
    try:
        value = fn(path)
    except Exception as exc:  # noqa: BLE001 - converted to a coded error
        detail = error_from(exc)
        errors.append({**detail, 'file': name})
        results[name] = {'path': str(path), 'status': 'error', 'code': detail['code']}
        return None
    results[name] = {'path': str(path), 'status': 'ok'}
    return value


def role_sample_problems(data, checks):
    problems = []
    rows = data.get('postings', [])
    if len({r.get('id') for r in rows}) != len(rows):
        problems.append('duplicate role-sample ids')
    if set(r.get('family') for r in rows) - set(data.get('families', [])):
        problems.append('role-sample family not declared in families')
    for r in rows:
        if not public_url(r.get('url', '')):
            problems.append(f"{r.get('id')}: url must be public HTTPS")
        try:
            timestamp(r.get('checked_at'))
        except (ValueError, TypeError):
            problems.append(f"{r.get('id')}: checked_at must be timezone-aware")
        if type(r.get('official')) is not bool:
            problems.append(f"{r.get('id')}: official must be boolean")
        if r.get('posting_key') and (checks.get(r['posting_key']) or {}).get('description_url') != r.get('url'):
            problems.append(f"{r.get('id')}: description check URL disagrees with verifications.json")
    return problems


def validate_data(paths: dict, now: str) -> tuple[dict, list, list]:
    results, errors, notes = {}, [], []
    cfg = _check(results, errors, 'config', paths['config'], load_config)
    if cfg:
        undocumented = [e['company'] for e in cfg['employers'] if not e.get('verification_notes')]
        if undocumented:
            notes.append(warning('undocumented_watchlist_entry', companies=undocumented))
    state = _check(results, errors, 'state', paths['state'], load_state)
    postings = state['postings'] if state else {}
    if state:
        on_disk = json.loads(Path(paths['state']).read_text(encoding='utf-8')).get('schema')
        results['state'].update(schema_on_disk=on_disk, records=len(postings),
                                health_file=health_path(Path(paths['state'])).exists())
        if on_disk != 2:
            notes.append(warning('migration_pending'))
        keep = referenced_texts(state, now)
        missing = sorted(s for s in keep if s not in state['texts'])
        directory = text_dir(Path(paths['state']))
        stored = {p.stem for p in directory.glob('*/*.txt') if SHA.fullmatch(p.stem)} if directory.is_dir() else set()
        results['state'].update(texts_referenced=len(keep), texts_missing=len(missing), texts_unreferenced=len(stored - keep))
        if missing and on_disk == 2:
            notes.append(warning('source_text_unavailable', count=len(missing)))
    checks = _check(results, errors, 'verifications', paths['verifications'], load_verifications) or {}
    for key, row in checks.items():
        if postings and key not in postings:
            notes.append(warning('orphan_owner_record', 'Verification refers to an identity absent from the index.', key=key))
        elif key in postings and row.get('description_sha256') and row['description_sha256'] != description_sha(postings[key]):
            notes.append(warning('stale_description_check', key=key))
    leads = _check(results, errors, 'leads', paths['leads'], load_leads)
    _check(results, errors, 'applications', paths['applications'], read_applications)
    decisions = _check(results, errors, 'triage', paths['triage'], tri.load) or {}
    for key in decisions:
        if postings and key not in postings:
            notes.append(warning('orphan_owner_record', key=key))
    sample = _check(results, errors, 'role_sample', paths['role_sample'], lambda p: json.loads(p.read_text(encoding='utf-8')))
    if sample is not None:
        problems = role_sample_problems(sample, checks)
        if problems:
            errors.append({'code': 'invalid_state', 'file': 'role_sample', 'message': '; '.join(problems)})
            results['role_sample']['status'] = 'error'
    if state is not None:
        _, cal_warnings = calendar.deadline_events(state, checks, {'vacancies': (leads or {}).get('vacancies', [])})
        for message in cal_warnings:
            notes.append(warning('calendar_evidence_problem', message))
    return {'files': results, 'errors': len(errors), 'warnings': len(notes)}, notes, errors
