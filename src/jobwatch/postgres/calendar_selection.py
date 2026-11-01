"""One evaluation of calendar membership for Research diagnostics and Owner reviews.

Reuses the publisher's selection and projection code against the current database.
It reads and reports only. Publisher status is a separate observation, and nothing
here can observe whether Proton has fetched a feed.
"""
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo

from jobwatch import calendar
from jobwatch.config import timestamp

from . import calendar_feed, reports, research
from .collection import iso
from .importer import primitive

MAX_ITEMS = 25
PROTON = 'Publication status covers the feed objects only; it does not show whether Proton has fetched them.'
RANK = {'independent_verification': 0, 'checked_official_lead': 1, 'feed_observation': 2, 'unverified_lead': 3}


def evaluate(conn, policy_sql, *, shared=True, past_days=30, future_days=366):
    now = iso(conn.execute('SELECT current_timestamp AS at').fetchone()['at'])
    records = primitive(research.deadlines(conn, now, shared=shared))
    rows = primitive(reports.selection_rows(conn))
    policy = reports.policy_keys(conn, policy_sql)
    kept = reports.select_deadlines(records, policy, rows)
    events, _ = reports.calendar_events({'generated_at': now, 'deadlines': deepcopy(kept),
                                         'applications': [], 'revisits': []}, minimal=True)
    today = timestamp(now).astimezone(ZoneInfo('Europe/London')).date()
    window = (today-timedelta(days=past_days), today+timedelta(days=future_days))
    postings = conn.execute('SELECT posting_key,company,title,posting_url,status FROM jobwatch.operational_postings ORDER BY posting_key').fetchall()
    aliases = defaultdict(list)
    for p in postings:
        if (u := research.url(p['posting_url'])) is not None:
            aliases[u].append(p['posting_key'])
    leads = conn.execute('SELECT lead_key,kind,company,title,url,state,posting_key FROM jobwatch.'
                         + ('agent_leads' if shared else 'leads') + ' ORDER BY lead_key').fetchall()
    identity, entities, members = {}, {}, defaultdict(list)
    for p in postings:
        identity[p['posting_key']] = p['posting_key']
        entities[p['posting_key']] = {'key': p['posting_key'], 'type': 'posting', 'company': p['company'],
            'title': p['title'], 'url': research.url(p['posting_url']), 'state': p['status']}
    for l in leads:
        identity[l['lead_key']] = research.lead_identity(l, aliases)[0]
        entities[l['lead_key']] = {'key': l['lead_key'], 'type': 'lead', 'kind': l['kind'], 'company': l['company'],
            'title': l['title'], 'url': research.url(l['url']), 'state': l['state']}
    for key, value in identity.items():
        members[value].append(key)
    claims = defaultdict(list)
    for d in records:
        claims[d['identity']].append(d)
    return {'now': now, 'window': window,
            'policy': policy, 'rows': {r['evidence_key']: r for r in rows}, 'kept': {id(d) for d in kept},
            'claims': claims, 'identity': identity, 'entities': entities, 'members': members,
            'events': {e['uid'] for e in events['deadlines'] if in_window(window, e['date'])}}


def evidence_view(d):
    return {'ref': d.get('claim_id') or d.get('lead_key'), 'provenance': d['provenance'], 'value': d['value'],
            'url': d['url'], 'checked_at': d['checked_at'], 'active': d.get('active', True),
            'wording': (d['wording'] or '')[:300], 'wording_is_untrusted': True}


def in_window(window, when):
    """The publisher's own Europe/London day rule for its rolling window."""
    value = when[2][5:]
    day = (datetime.strptime(value, '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc).astimezone(ZoneInfo('Europe/London')).date()
           if when[2].startswith('time:') else datetime.fromisoformat(value).date())
    return window[0] <= day <= window[1]


def describe(state, identity):
    """Why one calendar identity is or is not in the projection. No clock-dependent text."""
    claims = state['claims'].get(identity, [])
    keys = sorted(state['members'].get(identity, []))
    rows = [state['rows'][k] for k in keys if k in state['rows']]
    excluded = any(r['mode'] == 'exclude' for r in rows)
    approved = any(r['mode'] == 'include' for r in rows)
    selected = approved or state['policy'] is None or any(k in state['policy'] for k in keys)
    usable, reasons = [], set()
    for d in claims:
        if not selected or excluded or id(d) in state['kept']:
            when, reason = reports.deadline_claim(deepcopy(d), state['now'])
            if when:
                usable.append((when, d))
            elif reason != 'superseded':
                reasons.add(reason)
    usable.sort(key=lambda x: (RANK.get(x[1]['provenance'], 9), str(x[1].get('claim_id') or x[1].get('lead_key'))))
    dates = sorted({when[2] for when, _ in usable})
    active = {d['value'] for d in claims if d.get('active', True) and d['value']}
    best = usable[0] if usable else None
    if excluded:
        status = 'excluded'
    elif not selected:
        status = 'not_selected'
    elif not [d for d in claims if d['value']]:
        status = 'missing'
    elif len(dates) > 1:
        status = 'conflicting'
    elif not usable:
        status = 'insufficient'
    elif not in_window(state['window'], best[0]):
        status = 'outside_window'
    else:
        status = 'qualifies'
    event = calendar.uid('deadline', identity) in state['events'] and status == 'qualifies'
    entity = next((state['entities'][k] for k in keys if state['entities'][k]['type'] == 'posting'),
                  state['entities'][keys[0]] if keys else {})
    return {'identity_keys': keys, 'company': entity.get('company'), 'title': entity.get('title'),
            'deadline': best[1]['value'] if best and len(dates) == 1 else None,
            'all_day': bool(best[0][1]) if best and len(dates) == 1 else None,
            'evidence_url': best[1]['url'] if best and len(dates) == 1 else None,
            'status': status, 'reasons': sorted(reasons), 'in_projection': event,
            'other_recorded_dates': sorted(active-{best[1]['value']}) if best else sorted(active),
            'selection': [{'evidence_key': r['evidence_key'], 'mode': r['mode'], 'approved_deadline': r['approved_deadline']} for r in rows],
            'policy_selected': state['policy'] is None or any(k in state['policy'] for k in keys),
            'evidence': [evidence_view(d) for d in claims]}


def published(status, identity_keys, identity):
    """Whether the last uploaded feed held this record; 'unknown' without publisher status."""
    if status is None or 'published_deadlines' not in status:
        return {'in_published_feed': 'unknown'}
    aliases = ['deadline:'+identity]+[p+k for k in identity_keys for p in ('posting:', 'lead:')]
    hits = [status['published_deadlines'][h] for h in (calendar_feed.digest(['deadlines', a]) for a in aliases)
            if h in status['published_deadlines']]
    return {'in_published_feed': bool(hits), 'sequence': max(hits) if hits else None}


def publisher(status):
    if status is None:
        return {'state': 'unavailable', 'note': PROTON}
    return {'state': 'available', 'note': PROTON,
            **{k: status.get(k) for k in ('last_success_at', 'last_attempt_at', 'last_attempt_ok',
                                          'snapshot_generated_at', 'events', 'warnings')}}


def tokens(text):
    return set(re.findall(r'[a-z0-9]+', (text or '').lower()))


def squash(text):
    return re.sub(r'[^a-z0-9]', '', (text or '').lower())


def resolve(state, item):
    """Exact keys resolve; company/title only proposes candidates. Ties stay ambiguous."""
    key = item.get('key')
    if key:
        return {'status': 'resolved', 'key': key, 'candidates': []} if key in state['identity'] else {'status': 'not_found', 'candidates': []}
    company, wanted = squash(item.get('company')), tokens(item.get('title'))
    scored = defaultdict(float)
    for k, e in state['entities'].items():
        name = squash(e['company'])
        if name and company and (company in name or name in company) and wanted:
            score = len(wanted & tokens(e['title']))/len(wanted)
            if score >= .6:
                scored[state['identity'][k]] = max(scored[state['identity'][k]], score)
    if not scored:
        return {'status': 'not_found', 'candidates': []}
    top = max(scored.values())
    best = sorted(i for i, s in scored.items() if s == top)
    candidates = [{'identity_keys': sorted(state['members'][i]), 'score': round(s, 2)}
                  for i, s in sorted(scored.items(), key=lambda x: (-x[1], x[0]))][:5]
    if len(best) > 1:
        return {'status': 'ambiguous', 'candidates': candidates}
    return {'status': 'resolved', 'key': sorted(state['members'][best[0]])[0], 'candidates': candidates}


def similar_identities(state, identity):
    """Other projected identities that look like the same vacancy, so no second event is added."""
    keys = state['members'].get(identity, [])
    if not keys:
        return []
    own = state['entities'][keys[0]]
    title, company, found = tokens(own['title']), squash(own['company']), []
    for other, members in state['members'].items():
        if other == identity or calendar.uid('deadline', other) not in state['events']:
            continue
        e = state['entities'][members[0]]
        t = tokens(e['title'])
        if squash(e['company']) == company and t and (t <= title or title <= t):
            found.append(sorted(members))
    return found


def feed_effect(before, after, identities):
    """What a batch does to the whole feed. Selection is additive: only listed identities may change."""
    listed = {calendar.uid('deadline', i) for i in identities}
    return {'deadline_events_before': len(before['events']), 'deadline_events_after': len(after['events']),
            'added': len(after['events']-before['events']), 'removed': len(before['events']-after['events']),
            'unlisted_events_changed': len((before['events'] ^ after['events'])-listed)}


def report(conn, selection, policy_sql, status, *, shared=True):
    """Bounded read-only report for selected vacancies, or for all recorded approvals."""
    state = evaluate(conn, policy_sql, shared=shared)
    wanted = selection if selection is not None else [{'key': k} for k in sorted(state['rows'])][:MAX_ITEMS]
    items, seen = [], {}
    for raw in wanted:
        found = resolve(state, raw)
        row = {'input': raw, 'resolution': found['status'], 'candidates': found['candidates']}
        if found['status'] == 'resolved':
            identity = state['identity'][found['key']]
            row.update(key=found['key'], **describe(state, identity), **published(status, state['members'][identity], identity))
            row['possible_duplicates'] = similar_identities(state, identity)
            if raw.get('deadline') and raw['deadline'] != row['deadline']:
                row['candidate_deadline_mismatch'] = raw['deadline']
            if identity in seen:
                row['duplicate_of_input'] = seen[identity]
            seen.setdefault(identity, len(items))
        items.append(row)
    counts = defaultdict(int)
    for row in items:
        counts[row.get('status', row['resolution'])] += 1
    return {'kind': 'calendar_selection', 'policy': 'file' if policy_sql is not None else 'not_configured_all_evidence',
            'recorded_approvals': len(state['rows']), 'projected_deadline_events': len(state['events']),
            'summary': dict(counts), 'items': items, 'publisher': publisher(status),
            'not_the_legacy_export': 'run_report(kind="calendar") ignores file policy and approvals; this report follows the published projection.'}
