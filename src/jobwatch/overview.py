"""Local-only weekly overview and narrowly attributed requirement mentions."""
from collections import Counter, defaultdict
from datetime import timedelta
from zoneinfo import ZoneInfo
from hashlib import sha256
import re
from .applications import STAGES, read_applications, url_identity
from .feeds import public_url
from .config import timestamp
from .filtering import plain, rank, role_family, verdict_of
from .state import description_of, description_sha, last_seen
from .render import text, link
from .timing import deadline_window, stale

SKILLS = {k:re.compile(v,re.I) for k,v in {'Python':r'\bpython\b','Java':r'\bjava\b',
          'JavaScript':r'\bjavascript\b','TypeScript':r'\btypescript\b','C++':r'(?<![\w+])c\+\+','Rust':r'\brust\b',
          'SQL':r'\bsql\b','Linux':r'\blinux\b','Git':r'\bgit\b',
          'distributed systems':r'\bdistributed systems\b', 'Kubernetes':r'\bkubernetes\b',
          'Docker':r'\bdocker\b','AWS':r'\baws\b','Terraform':r'\bterraform\b',
          'machine learning':r'\bmachine learning\b','PyTorch':r'\bpytorch\b','LLMs':r'\bllms?\b'}.items()}
# Case-sensitive: the English verb ("go the extra mile") is not the language.
SKILLS['Go']=re.compile(r'\bGo(?:lang)?\b(?! (?:the|to|beyond|ahead)\b)')


def description_hash(value):
    return sha256(value.encode('utf-8')).hexdigest()


def _verified_deadlines(state, verifications, leads, now, applications):
    """Only independently checked evidence, including manual-only leads."""
    evidence = []
    done_keys = {r.get('job_key') for r in applications if r['stage'] not in {'bookmarked', 'preparing'}}
    done_urls = {url_identity(r['url']) for r in applications
                 if r.get('url') and r['stage'] not in {'bookmarked', 'preparing'}}
    for key, row in state['postings'].items():
        verification = verifications.get(key, {})
        if verdict_of(row) == 'excluded' or verification.get('status') == 'closed_verified':
            continue
        # Raw feed dates are observations, not independent verification.
        for deadline in verification.get('deadlines', []):
            evidence.append((key, row, deadline))
    for row in (leads or {}).get('vacancies', []):
        if row.get('official') and row.get('deadline') and row.get('status') != 'unverified':
            deadline = {**row['deadline']}
            deadline.setdefault('url', row['url'])
            deadline.setdefault('verified_at', row['checked_at'])
            evidence.append((None, row, deadline))
    selected = []
    for key, row, deadline in evidence:
        if key in done_keys or url_identity(row['url']) in done_urls:
            continue
        try:
            checked = timestamp(deadline.get('verified_at'))
        except (ValueError, TypeError):
            continue
        if checked > timestamp(now) or not public_url(deadline.get('url', '')) or not deadline.get('wording'):
            continue
        urgency = deadline_window(deadline, now)
        if urgency.startswith('later than'):
            continue
        selected.append((checked, row, deadline, urgency))
    # Keep the newest check when the lead and verification registries overlap.
    seen_vacancies, seen_sources, result = set(), set(), []
    for checked, row, deadline, urgency in sorted(selected, key=lambda item: item[0], reverse=True):
        value = deadline.get('value') or ' '.join(deadline['wording'].split())
        vacancy = (url_identity(row['url']), value)
        source = (row['company'].casefold(), url_identity(deadline['url']), value)
        if vacancy in seen_vacancies or source in seen_sources:
            continue
        seen_vacancies.add(vacancy); seen_sources.add(source)
        result.append((row, deadline, urgency))
    return sorted(result, key=lambda item: (item[1].get('value') or '9999', item[0]['company'], item[0]['title']))


def weekly(state, applications, verifications, now, leads=None):
    current = timestamp(now); since = current - timedelta(days=7)
    today = current.astimezone(ZoneInfo('Europe/London')).date()
    horizon = today + timedelta(days=7)
    candidates = [r for r in state['postings'].values() if verdict_of(r) != 'excluded']
    # Backfilled records were listed before complete recording began; their first listing is unknown.
    recent = [r for r in candidates if r.get('first_seen_basis') != 'index_backfill' and since <= timestamp(r['first_seen']) <= current]
    lines = ['# Weekly job review (private local output)', '',
             f'As of {text(now)}; previous seven days. Last feed scan: {text(state.get("last_scan", {}).get("at", "unknown"))}.',
             'Owner action dates use Europe/London calendar days. Source deadlines retain their stated dates/timezones.',
             'Feed observations do not verify an open application or eligibility. Not seen in a feed never proves closure.', '']
    active = sorted((r for r in applications if r['stage'] not in {'rejected', 'withdrawn'}
                     and (r.get('next_action') or r.get('next_action_date'))),
                    key=lambda r: (r.get('next_action_date') or '9999', r['company'], r['role'], r.get('job_key', '')))

    def action(row):
        return (f"- {text(row['company'])} — {link(row['role'], row.get('url', ''))}: "
                f"{text(row.get('next_action') or 'Review next action')}; owner-stated date "
                f"{text(row.get('next_action_date') or 'unknown')}; stage {text(row['stage'])}.")

    for heading, rows in (
        ('Overdue owner-entered actions', [r for r in active if r.get('next_action_date') and r['next_action_date'] < today.isoformat()]),
        ('Actions due within seven days', [r for r in active if r.get('next_action_date') and today.isoformat() <= r['next_action_date'] <= horizon.isoformat()]),
    ):
        lines += [f'## {heading}', ''] + ([action(r) for r in rows] or ['None recorded.']) + ['']
    lines += ['## Source-verified application deadlines requiring attention', '',
              'Checked source evidence within 14 days, passed dates to recheck, and undated wording for manual review. Recheck before acting; a passed date does not establish closure.', '']
    for row, deadline, urgency in _verified_deadlines(state, verifications, leads, now, applications):
        lines.append(f"- {text(row['company'])} — {link(row['title'], row['url'])}: "
                     f"{text(plain(deadline['wording']))}; source value {text(deadline.get('value', 'unknown'))}; "
                     f"{text(urgency)}; {link('deadline source', deadline['url'])}; checked {text(deadline['verified_at'])}.")
    if not lines[-1].startswith('- '):
        lines.append('No verified source deadlines requiring attention; missing dates remain unknown.')
    lines += ['', '## Applications by stage', '']
    counts = Counter(r['stage'] for r in applications)
    for stage in STAGES:
        rows = [r for r in applications if r['stage'] == stage]
        if not rows:
            continue
        lines.append(f'- {text(stage)}: {counts[stage]}')
        for row in sorted(rows, key=lambda r: (r['company'], r['role'])):
            lines.append(f"  - {text(row['company'])} — {link(row['role'], row.get('url', ''))}; applied {text(row.get('date_applied') or 'unknown')}.")
    if not applications:
        lines.append('Ledger has no application rows.')
    other = [r for r in active if not r.get('next_action_date') or r['next_action_date'] > horizon.isoformat()]
    if other:
        lines += ['', 'Other owner actions (later or date unknown):', ''] + [action(r) for r in other]
    lines += ['', f'## New relevant vacancies — first observed within seven days ({len(recent)})', '']
    lines += [f"- {text(verdict_of(r))} · {text(r['company'])} — {link(r['title'], r['url'])}; feed: {text(r['feed_status'])}; first observed {text(r['first_seen'])}." for r in sorted(recent, key=rank)]
    if not recent:
        lines.append('None recorded on configured feeds in this window.')
    lines += ['', '## Stale feeds and collection failures', '']
    problems = []
    for key, health in sorted(state.get('boards', {}).items()):
        warning = '; STALE/unknown (>3 days)' if stale(health.get('last_success'), now) else ''
        if warning or health.get('status') == 'failed':
            problems.append(f"- {text(key)}: {text(health.get('status', 'unknown'))}; last success {text(health.get('last_success', 'unknown'))}{warning}; {text(health.get('error', ''))}.")
    problems += [f"- Last scan failure: {text(failure)}" for failure in state.get('last_scan', {}).get('failures', [])]
    lines += problems or ['No stale/failed boards recorded.' if state.get('boards') else 'No board-health history recorded; collection health unknown.']
    lines += ['', 'Private owner notes and CV versions are not reproduced. This report stays local and is never included in the scheduled Actions summary.', '']
    return '\n'.join(lines)


def requirements(state, verifications, now):
    groups=defaultdict(list); omitted=0
    for key,row in state['postings'].items():
        if verdict_of(row)=='excluded' or row['feed_status']!='listed':continue
        evidence=verifications.get(key,{})
        description=description_of(state,row)
        if description is None or not evidence.get('description_verified_at') or evidence.get('description_sha256')!=description_sha(row):
            omitted+=1;continue
        timestamp(evidence['description_verified_at'])
        groups[(row.get('screening') or {}).get('family') or row.get('role_family') or role_family(row['title'])].append((key,row,description))
    total=sum(len(rows) for rows in groups.values())
    lines=['# Attributed requirement mentions', '', f'Generated {text(now)}. Independently checked description samples: {total}; candidates omitted without matching verification: {omitted}.',
           '', 'Counts are per posting, grouped by title-derived role family. A mention can describe the stack, a preference or a requirement; it is not proof that every applicant needs it. Reposts are retained separately and may bias counts. This sample does not establish how many CV variants are useful.', '']
    for family,rows in sorted(groups.items()):
        lines += [f'## {text(family)} — {len(rows)} checked descriptions','']
        for skill,pattern in SKILLS.items():
            matches=[(key,row) for key,row,description in rows if pattern.search(plain(description))]
            if not matches:continue
            lines.append(f'- **{text(skill)}**: {len(matches)}/{len(rows)} postings.')
            for key,row in matches:
                evidence=verifications[key]
                lines.append(f"  {text(key)}: {link(row['title'],evidence['description_url'])}; checked {text(evidence['description_verified_at'])}; feed observation {text(last_seen(row,state.get('boards')) or 'unknown')}.")
    if not groups:lines.append('No independently verified matching descriptions yet; no skill-demand inference made.')
    lines += ['', 'This is a small convenience sample from configured boards, not UK hiring-market evidence. Expand checked descriptions across SWE, platform and ML/AI before deciding on CV variants. No applicant project claims or CV wording are generated.','']
    return '\n'.join(lines)
