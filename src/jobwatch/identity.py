"""One definition of opportunity identity, shared by ledger, calendar and discovery.

Identity levels, strongest first:

1. Posting key ``provider:board:external_id``: the ATS's own identifier,
   validated against the hosted URL when collected. Never derived from text.
2. Exact URL: scheme/host case and the default HTTPS port are normalised;
   path, query **and fragment** are preserved, because client-rendered careers
   sites use fragments and query parameters to distinguish openings.
3. Loose URL: exact URL without the fragment. Used only for conservative
   *refusal* (the ledger rejects a second row that differs only by fragment)
   and for "possible match" warnings. It never merges two records.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

from .feeds import public_url

POSTING_KEY = re.compile(r'(?:greenhouse|lever|ashby):[a-z0-9_.-]+:[A-Za-z0-9_.-]+')


def posting_key(provider: str, board: str, external_id: str) -> str:
    return f'{provider}:{board.lower()}:{external_id}'


def is_posting_key(value) -> bool:
    return isinstance(value, str) and bool(POSTING_KEY.fullmatch(value))


def exact_url(url: str) -> str:
    if not public_url(url):
        raise ValueError('URL must be HTTPS without credentials or whitespace')
    u = urlsplit(url)
    host = u.hostname.lower()
    if ':' in host:
        host = '[' + host + ']'
    return urlunsplit(('https', host, u.path or '/', u.query, u.fragment))


def loose_url(url: str) -> str:
    if not public_url(url):
        raise ValueError('application URL must be HTTPS without credentials or whitespace')
    u = urlsplit(url)
    return urlunsplit(('https', u.hostname.lower(), u.path or '/', u.query, ''))


def link_application(key: str, url: str, applications: list[dict]) -> tuple[dict | None, str, list[dict]]:
    """Find the ledger row for an opportunity.

    Returns ``(row, basis, possible)``. ``basis`` is ``job_key``, ``exact_url``
    or ``none``. Rows matching only by loose URL are returned in ``possible``
    for a warning, never linked automatically."""
    try:
        exact, loose = exact_url(url), loose_url(url)
    except ValueError:
        exact = loose = None
    by_key = [r for r in applications if r.get('job_key') == key]
    if by_key:
        return by_key[0], 'job_key', []
    exact_rows, possible = [], []
    for row in applications:
        try:
            if exact and exact_url(row.get('url', '')) == exact:
                exact_rows.append(row)
            elif loose and loose_url(row.get('url', '')) == loose:
                possible.append(row)
        except ValueError:
            continue
    if len(exact_rows) == 1:
        return exact_rows[0], 'exact_url', possible
    return None, 'none', exact_rows + possible
