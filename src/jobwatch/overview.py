"""Local-only weekly overview and narrowly attributed requirement mentions."""
from collections import Counter, defaultdict
import csv
from datetime import timedelta
from hashlib import sha256
from pathlib import Path
import re
from .config import timestamp
from .filtering import plain, rank, role_family
from .render import text, link
from .timing import deadline_window, stale

STAGES = {'bookmarked','preparing','applied','assessment','interview','offer','rejected','withdrawn'}
SKILLS = {'Python':r'\bpython\b','Java':r'\bjava\b','Go':r'\bgo\b',
          'JavaScript':r'\bjavascript\b','SQL':r'\bsql\b','Linux':r'\blinux\b',
          'distributed systems':r'\bdistributed systems\b', 'Kubernetes':r'\bkubernetes\b',
          'Docker':r'\bdocker\b','AWS':r'\baws\b','machine learning':r'\bmachine learning\b'}


def description_hash(value):
    return sha256(value.encode('utf-8')).hexdigest()


def read_applications(path: Path):
    """Read owner rows without normalising or writing the ledger."""
    with path.open(encoding='utf-8-sig', newline='') as f:
        reader=csv.DictReader(f)
        required={'job_key','company','role','url','stage','next_action','next_action_date'}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError('application ledger is missing required columns')
        rows=list(reader)
    if any(None in row for row in rows):
        raise ValueError('malformed application CSV row')
    return rows


def weekly(state, applications, verifications, now):
    current=timestamp(now); since=current-timedelta(days=7)
    candidates=[r for r in state['postings'].values() if r['priority']!='excluded']
    recent=[]
    for r in candidates:
        seen=timestamp(r['first_seen'])
        if since <= seen <= current:
            recent.append(r)
    lines=['# Weekly job review (private local output)', '', f'As of {text(now)}; previous seven days. Last feed scan: {text(state.get("last_scan",{}).get("at","unknown"))}.',
           '', 'Feed observations do not verify an open application or eligibility. The owner ledger is read only; no stages are inferred.', '', '## Collection health', '']
    for key,health in sorted(state.get('boards',{}).items()):
        warning='; STALE/unknown (>3 days)' if stale(health.get('last_success'),now) else ''
        lines.append(f"- {text(key)}: {text(health['status'])}; last success {text(health.get('last_success','unknown'))}{warning}.")
    lines += ['',f'## First observed within seven days ({len(recent)})','']
    lines += [f"- {text(r['priority'])} · {text(r['company'])} — {link(r['title'],r['url'])}; feed: {text(r['feed_status'])}; first observed {text(r['first_seen'])}." for r in sorted(recent,key=rank)]
    if not recent:lines.append('None recorded on configured feeds in this window.')
    lines += ['','## Source deadlines awaiting review','']
    deadlines=[]
    for key,r in state['postings'].items():
        if r['priority']=='excluded':continue
        for d in r.get('deadlines',[])+verifications.get(key,{}).get('deadlines',[]):
            deadlines.append(f"- {text(r['company'])} — {link(r['title'],d.get('url',r['url']))}: {text(plain(d['wording']))}; {text(deadline_window(d,now))}; evidence dated {text(d.get('verified_at',d.get('observed_at','unknown')))}.")
    lines += deadlines or ['No source deadline evidence; missing dates remain unknown.']
    lines += ['','## Owner application stages','']
    counts=Counter(row['stage'] or 'unspecified' for row in applications)
    lines += [f'- {text(stage)}: {count}' for stage,count in sorted(counts.items())] or ['Ledger has no application rows.']
    lines += ['','## Actions awaiting the owner','']
    actions=0
    for row in applications:
        if row['stage'] in {'rejected','withdrawn'}:continue
        if row.get('next_action'):
            actions+=1
            due=row.get('next_action_date') or 'unknown'
            lines.append(f"- {text(row['company'])} — {text(row['role'])}: {text(row['next_action'])}; owner-stated date {text(due)}; stage {text(row['stage'] or 'unspecified')}.")
        if row['stage'] and row['stage'] not in STAGES:
            lines.append(f"- Unknown owner stage {text(row['stage'])}; review the ledger manually.")
    if not actions:lines.append('No owner next-actions recorded. Review the ranked vacancy queue and manual watchlist as needed.')
    lines += ['','Private owner notes, CV versions and correspondence are not reproduced. This report is local and is not included in the scheduled Actions summary.','']
    return '\n'.join(lines)


def requirements(state, verifications, now):
    groups=defaultdict(list); omitted=0
    for key,row in state['postings'].items():
        if row['priority']=='excluded' or row['feed_status']!='listed':continue
        evidence=verifications.get(key,{})
        description=row.get('description','')
        if not evidence.get('description_verified_at') or evidence.get('description_sha256')!=description_hash(description):
            omitted+=1;continue
        timestamp(evidence['description_verified_at'])
        groups[row.get('role_family') or role_family(row['title'])].append((key,row,evidence))
    total=sum(len(rows) for rows in groups.values())
    lines=['# Attributed requirement mentions', '', f'Generated {text(now)}. Independently checked description samples: {total}; candidates omitted without matching verification: {omitted}.',
           '', 'Counts are per posting, grouped by title-derived role family. A mention can describe the stack, a preference or a requirement; it is not proof that every applicant needs it. Reposts are retained separately and may bias counts. This sample does not establish how many CV variants are useful.', '']
    for family,rows in sorted(groups.items()):
        lines += [f'## {text(family)} — {len(rows)} checked descriptions','']
        for skill,pattern in SKILLS.items():
            matches=[(key,row,evidence) for key,row,evidence in rows if re.search(pattern,plain(row['description']),re.I)]
            if not matches:continue
            lines.append(f'- **{text(skill)}**: {len(matches)}/{len(rows)} postings.')
            for key,row,evidence in matches:
                lines.append(f"  {text(key)}: {link(row['title'],evidence['description_url'])}; checked {text(evidence['description_verified_at'])}; feed observation {text(row['last_seen'])}.")
    if not groups:lines.append('No independently verified matching descriptions yet; no skill-demand inference made.')
    lines += ['', 'This is a small convenience sample from configured boards, not UK hiring-market evidence. Expand checked descriptions across SWE, platform and ML/AI before deciding on CV variants. No applicant project claims or CV wording are generated.','']
    return '\n'.join(lines)
