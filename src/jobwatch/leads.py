"""Manually verified vacancy leads and graduate-programme cycles; a 2026 cycle never implies 2027."""
import json
import re
from datetime import date
from pathlib import Path
from .config import timestamp
from .feeds import public_url
from .render import text, link
from .timing import deadline_window

VACANCY_STATUS = {'listed', 'employer_says_open', 'not_listed', 'unverified'}
PROGRAMME_STATE = {'open_2027', 'announced_window', 'register_interest_only', 'closed_reopening_unknown',
                   'previous_cycle', 'not_found'}
YEAR = re.compile(r'20\d{2}')


def _quoted_year(row, field, wording_field):
    """A stated year must appear verbatim in the quoted source wording."""
    value = row.get(field)
    if value in ('unknown', 'unspecified'):
        return None
    if not isinstance(value, str) or not YEAR.fullmatch(value) or value not in str(row.get(wording_field, '')):
        raise ValueError(f'{row.get("company")}: {field} must be unknown or a year quoted in {wording_field}')
    return int(value)


def _common(row):
    if not isinstance(row, dict) or not all(isinstance(row.get(k), str) and row[k].strip() for k in ('company', 'url', 'checked_at', 'source')):
        raise ValueError('lead needs company, url, checked_at and source')
    if not public_url(row['url']):
        raise ValueError(f'{row["company"]}: source URL must be public HTTPS')
    timestamp(row['checked_at'])
    if type(row.get('official')) is not bool:
        raise ValueError(f'{row["company"]}: official must be true or false')


def load_leads(path: Path):
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('schema') != 1 or not isinstance(data.get('vacancies'), list) or not isinstance(data.get('programmes'), list):
        raise ValueError('invalid lead registry')
    for row in data['vacancies']:
        _common(row)
        if row.get('status') not in VACANCY_STATUS or not row.get('title'):
            raise ValueError(f'{row["company"]}: vacancy needs title and known status')
        _quoted_year(row, 'intake', 'intake_wording')
        deadline = row.get('deadline')
        if deadline is not None:
            if not isinstance(deadline, dict) or not deadline.get('wording'):
                raise ValueError(f'{row["company"]}: deadline needs exact source wording')
            if 'value' in deadline:
                date.fromisoformat(deadline['value'])  # date only; no expiry time is inferred
            if 'url' in deadline and not public_url(deadline['url']):
                raise ValueError(f'{row["company"]}: deadline source URL must be public HTTPS')
    for row in data['programmes']:
        _common(row)
        if row.get('state') not in PROGRAMME_STATE or not row.get('programme') or not row.get('wording'):
            raise ValueError(f'{row["company"]}: programme needs name, known state and exact wording')
        cycle = _quoted_year(row, 'cycle', 'wording')
        if row['state'] == 'open_2027' and cycle != 2027:
            raise ValueError(f'{row["company"]}: open_2027 requires 2027 quoted in source wording')
        if row['state'] == 'previous_cycle' and (cycle is None or cycle >= 2027):
            raise ValueError(f'{row["company"]}: previous_cycle needs an earlier quoted year')
        if row['state'] == 'announced_window' and not row.get('window'):
            raise ValueError(f'{row["company"]}: announced_window needs the stated window')
    return data


def _source(row):
    kind = 'official' if row['official'] else "**not verified as the employer's own source**"
    return f"{link('source', row['url'])} ({kind}; {text(row['source'])}; checked {text(row['checked_at'])})"


def render_leads(data, as_of):
    vac, prog = data['vacancies'], data['programmes']
    lines = ['# Manual leads and programme cycles', '',
             f'Generated {text(as_of)} from `data/leads.json`. Every claim is quoted from the linked source at its check time.',
             'A listing or "applications open" statement does not verify an application form, eligibility or sponsorship.',
             'A previous-year programme is **never** evidence of a 2027 intake.', '',
             '## Source-stated deadlines', '']
    dated = sorted((r for r in vac if r.get('deadline')), key=lambda r: (r['deadline'].get('value') or '9999', r['company']))
    for r in dated:
        d = r['deadline']
        own = f" Deadline {link('source', d['url'])}." if d.get('url') else ''
        lines.append(f"- {text(r['company'])} — {text(r['title'])}: “{text(d['wording'])}”; {text(deadline_window(d, as_of))}.{own} {_source(r)}")
    if not dated:
        lines.append('None recorded.')
    lines += ['', '## Vacancies with a quoted 2027 intake', '']
    shown = [r for r in vac if r.get('intake') == '2027' and r['status'] in ('listed', 'employer_says_open')]
    for r in shown:
        lines.append(f"- **{text(r['company'])}** — {text(r['title'])} ({text(r['status'])}; employer source {'verified' if r['official'] else 'not verified'}): “{text(r['intake_wording'])}” Location: {text(r.get('location', 'not stated'))}. {_source(r)}")
    lines += ['', '## Listed vacancies without a quoted 2027 intake', '']
    lines += [f"- {text(r['company'])} — {text(r['title'])} ({text(r['status'])}): {text(r.get('notes', ''))} {_source(r)}"
              for r in vac if r.get('intake') != '2027' and r['status'] in ('listed', 'employer_says_open')] or ['None.']
    lines += ['', '## Leads not currently listed or not verifiable', '']
    lines += [f"- {text(r['company'])} — {text(r['title'])} ({text(r['status'])}): {text(r.get('notes', ''))} {_source(r)}"
              for r in vac if r['status'] in ('not_listed', 'unverified')] or ['None.']
    sections = [('open_2027', 'Programmes stating a 2027 intake'), ('announced_window', 'Announced future application windows'),
                ('register_interest_only', 'Register-interest-only pages'), ('closed_reopening_unknown', 'Closed; reopening date unknown'),
                ('not_found', 'No current official programme posting found'),
                ('previous_cycle', 'Previous-cycle programmes — not evidence of a 2027 intake')]
    for state, heading in sections:
        lines += ['', f'## {heading}', '']
        rows = [r for r in prog if r['state'] == state]
        for r in rows:
            window = f" Window: {text(r['window'])}." if r.get('window') else ''
            uncertainty = f" Uncertainty: {text(r['uncertainty'])}." if r.get('uncertainty') else ''
            said = f"Observation: {text(r['wording'])}" if state == 'not_found' else f"“{text(r['wording'])}”"
            lines.append(f"- **{text(r['company'])}** — {text(r['programme'])} (cycle: {text(r['cycle'])}): {said}{window}{uncertainty} {_source(r)}")
        if not rows:
            lines.append('None recorded.')
    lines += ['', 'This is a selected set of manually checked leads, not UK-wide coverage. Recheck each source before acting.', '']
    return '\n'.join(lines)
