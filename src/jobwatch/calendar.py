"""Local RFC 5545 date markers; no network, scheduling or calendar credentials."""
from collections import defaultdict
from datetime import date, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import parse_qsl, quote, urlsplit

from .applications import iso_date, read_applications
from .config import timestamp
from .feeds import public_url
from .identity import POSTING_KEY, exact_url
from .filtering import verdict_of
from .state import load_state, utcnow

POSTING_ID = POSTING_KEY
ZONED_TIME = re.compile(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})')
ROLLING = re.compile(r'\brolling\b|\bno (?:fixed |application )?deadline\b', re.I)
SECRET_KEYS = {'token', 'access_token', 'api_key', 'apikey', 'auth', 'authorization',
               'password', 'secret', 'signature', 'credential', 'session', 'code'}


def safe_url(value):
    """Exact URL identity: never discard query parameters or guess redirects."""
    if not public_url(value):
        raise ValueError('unsafe calendar source URL')
    u = urlsplit(value)
    for key, _ in parse_qsl(u.query + '&' + u.fragment, keep_blank_values=True):
        key = key.casefold().replace('-', '_')
        if key in SECRET_KEYS or key.startswith(('x_amz_', 'x_goog_')):
            raise ValueError('credential-bearing calendar URL omitted')
    # Fragments can identify distinct openings on client-rendered careers sites,
    # so reconciliation uses the exact identity (identity.exact_url).
    return exact_url(value)


def uid(kind, identity):
    digest = sha256(('jobwatch-calendar-v1\0' + kind + '\0' + identity).encode()).hexdigest()
    return digest + '@jobwatch.local'


def utc_value(dt):
    dt = dt.astimezone(timezone.utc)
    return f'{dt.year:04}{dt.month:02}{dt.day:02}T{dt.hour:02}{dt.minute:02}{dt.second:02}Z'


def event_date(value):
    """Return RFC start/end and an equality key. Timed markers have no duration."""
    if isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        start = date.fromisoformat(value)
        end = start + timedelta(days=1)
        return (f'DTSTART;VALUE=DATE:{start.isoformat().replace("-", "")}',
                f'DTEND;VALUE=DATE:{end.isoformat().replace("-", "")}', 'date:' + value)
    if not isinstance(value, str) or not ZONED_TIME.fullmatch(value) or value.endswith('-00:00'):
        raise ValueError('invalid date or ambiguous/unsupported deadline timezone or precision')
    start = utc_value(timestamp(value))
    return 'DTSTART:' + start, None, 'time:' + start


def escape_text(value):
    if not isinstance(value, str) or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', value):
        raise ValueError('invalid calendar text; no private values displayed')
    return (value.replace('\r\n', '\n').replace('\r', '\n').replace('\\', '\\\\')
            .replace('\n', '\\n').replace(',', '\\,').replace(';', '\\;'))


def fold(line):
    """Fold at UTF-8 character boundaries; continuation space counts as one octet."""
    parts, current, size = [], '', 0
    for char in line:
        width = len(char.encode('utf-8'))
        if size + width > 75:
            parts.append(current)
            current, size = ' ', 1
        current += char
        size += width
    return '\r\n'.join(parts + [current])


def render(events, now):
    stamp = utc_value(timestamp(now))
    seen = set()
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Jobwatch//Private Graduate Tracker//EN',
             'CALSCALE:GREGORIAN']
    for event in sorted(events, key=lambda e: (e['date'][2], e['uid'])):
        if event['uid'] in seen:
            raise ValueError('duplicate calendar UID; no export written')
        seen.add(event['uid'])
        start, end, _ = event['date']
        revised = utc_value(timestamp(event['modified_at'])) if event.get('modified_at') else stamp
        lines += ['BEGIN:VEVENT', 'UID:' + event['uid'], 'DTSTAMP:' + revised, start]
        if event.get('modified_at'):
            sequence = event.get('sequence', 0)
            if type(sequence) is not int or not 0 <= sequence <= 2147483647:
                raise ValueError('invalid calendar sequence')
            lines += ['LAST-MODIFIED:' + revised, 'SEQUENCE:' + str(sequence)]
        if end:
            lines.append(end)
        lines += ['SUMMARY:' + escape_text(event['summary']),
                  'DESCRIPTION:' + escape_text(event['description']), 'CLASS:PRIVATE', 'TRANSP:TRANSPARENT']
        if event.get('url'):
            # URL has URI semantics, not TEXT escaping; TEXT fields above escape punctuation.
            lines.append('URL:' + quote(event['url'], safe=":/?=&%+#@;,-._~[]!$'()*+"))
        lines.append('END:VEVENT')
    lines.append('END:VCALENDAR')
    try:
        return ('\r\n'.join(fold(line) for line in lines) + '\r\n').encode('utf-8')
    except UnicodeError:
        raise ValueError('invalid Unicode in calendar data') from None


def deadline_events(state, verifications, leads):
    """Reconcile vacancy URLs with IDs, never shared deadline pages or titles."""
    warnings, aliases, metadata = [], defaultdict(set), {}
    postings = state['postings']
    lead_rows = leads.get('vacancies', [])
    if any(not isinstance(r, dict) for r in [*postings.values(), *verifications.values(), *lead_rows]):
        raise ValueError('malformed calendar source record')
    # IDs also exist independently of current feed observations (e.g. Riverlane).
    for key in sorted(set(postings) | set(verifications)):
        if not POSTING_ID.fullmatch(key):
            warnings.append('Unsupported posting identity; deadline evidence omitted.')
            continue
        rows = [r for r in (postings.get(key), verifications.get(key)) if r is not None]
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError('malformed calendar posting record')
            try:
                url = safe_url(row.get('url', ''))
            except ValueError:
                continue
            aliases[url].add(key)
            metadata.setdefault(key, {}).update({k: row[k] for k in ('company', 'title') if row.get(k)})
            metadata[key].setdefault('url', url)
    # Lead text supplies a label for verified IDs absent from jobs.json.
    for row in lead_rows:
        if not isinstance(row, dict):
            raise ValueError('malformed calendar lead record')
        try:
            url = safe_url(row.get('url', ''))
        except ValueError:
            continue
        keys = aliases.get(url, set())
        if len(keys) == 1:
            meta = metadata[next(iter(keys))]
            for field in ('company', 'title'):
                if not meta.get(field) and row.get(field):
                    meta[field] = row[field]
    evidence = []
    def checked_deadlines(row):
        values = row.get('deadlines', [])
        if not isinstance(values, list) or any(not isinstance(d, dict) for d in values):
            raise ValueError('malformed calendar deadline evidence')
        return values

    for key, row in sorted(postings.items()):
        # The index records excluded postings too; their feed dates are not owner deadlines
        # unless the posting was independently checked.
        if verdict_of(row) == 'excluded' and key not in verifications:
            continue
        expected = f"{row.get('provider')}:{str(row.get('board', '')).lower()}:{row.get('external_id')}"
        for d in checked_deadlines(row):
            # Only a structured official API date is sufficient feed provenance.
            evidence.append((key if key == expected else None, row, d,
                             d.get('observed_at'), d.get('source_field') == 'application_deadline', 'Feed observed'))
    for key, row in sorted(verifications.items()):
        for d in checked_deadlines(row):
            evidence.append((key, row, d, d.get('verified_at'), True, 'Source verified'))
    for row in lead_rows:
        if row.get('deadline') is not None:
            d = row['deadline']
            if not isinstance(d, dict):
                raise ValueError('malformed calendar lead deadline')
            evidence.append((None, row, d, d.get('verified_at', row.get('checked_at')),
                             row.get('official') is True and row.get('status') in {'listed', 'employer_says_open', 'not_listed'},
                             'Official source checked'))
    grouped = defaultdict(list)
    for key, row, d, checked, trusted, origin in evidence:
        if not isinstance(d, dict):
            raise ValueError('malformed calendar deadline evidence')
        if not d.get('value'):
            continue  # no prose parsing, inferred dates or rolling dates
        if ROLLING.search(d.get('wording', '')):
            warnings.append('Rolling/no-deadline wording with an explicit date; evidence omitted.')
            continue
        try:
            if not trusted:
                raise ValueError('unacceptable calendar deadline provenance')
            timestamp(checked)
            vacancy_url = safe_url(row.get('url') or metadata.get(key, {}).get('url', ''))
            source = safe_url(d.get('url', vacancy_url))
            if origin == 'Feed observed' and source != vacancy_url:
                raise ValueError('structured feed deadline URL does not identify the posting')
            keys = aliases.get(vacancy_url, set())
            if len(keys) > 1:
                raise ValueError('vacancy URL maps to multiple posting IDs')
            if key is not None:
                if not POSTING_ID.fullmatch(key) or key not in metadata:
                    raise ValueError('unacceptable calendar posting identity')
                identity = key
            else:
                if origin == 'Feed observed':
                    raise ValueError('feed posting ID mismatch')
                identity = next(iter(keys)) if keys else vacancy_url
            when = event_date(d['value'])
        except (ValueError, TypeError, OverflowError):
            warnings.append('Dated deadline omitted: invalid date/timezone, source provenance, URL or posting identity.')
            continue
        label = metadata.get(identity, row)
        label = {**label, 'url': safe_url(label.get('url', vacancy_url))}
        grouped[identity].append((when, label, source, checked, origin, d))
    events = []
    for identity, records in sorted(grouped.items()):
        event_uid = uid('deadline', identity)
        if len({r[0][2] for r in records}) != 1:
            warnings.append(f'Deadline conflict {event_uid[:12]}: explicit dates disagree; vacancy omitted.')
            continue
        when, label, _, _, _, _ = records[0]
        explanation = ('All-day date marker; closing time and timezone are unknown. This does not imply midnight expiry.'
                       if when[1] else 'Exact source timestamp, normalised to UTC; instantaneous marker with no assumed duration.')
        description = [explanation, 'A recorded deadline is not independent proof the application form is still open. Recheck the source before acting.']
        details = set()
        for _, _, source, checked, origin, d in records:
            details.add(f"{origin}: {checked}\nSource URL: {source}\nRecorded value: {d['value']}\nOriginal wording: {d.get('wording', '(not recorded)')}")
        description += sorted(details)
        title = ' — '.join(label.get(k, '') for k in ('company', 'title') if label.get(k)) or 'Recorded vacancy'
        events.append({'uid': event_uid, 'date': when, 'summary': 'Application deadline: ' + title,
                       'description': '\n\n'.join(description), 'url': label.get('url', records[0][2])})
    return events, sorted(set(warnings))


def followup_events(applications, *, minimal=False):
    events, warnings = [], []
    for row in applications:
        if row['stage'] in {'rejected', 'withdrawn'} or not row['next_action'].strip() or not row['next_action_date']:
            continue
        iso_date(row['next_action_date'])  # existing ledger schema permits dates only
        try:
            when = event_date(row['next_action_date'])
        except (ValueError, OverflowError):
            warnings.append('Follow-up date cannot be represented safely; event omitted.')
            continue
        url = None
        try:
            url = safe_url(row['url'])
        except ValueError:
            warnings.append('Unsafe follow-up URL omitted; action date marker retained.')
        summary = (f"Follow up: {row['company']} — {row['role']}" if minimal else
                   f"{row['company']} — {row['role']}: {row['next_action']}")
        events.append({'uid': uid('followup', row['job_key']), 'date': when,
                       'summary': summary,
                       'description': 'Owner-entered next action date. All-day date marker; no appointment time is implied.',
                       'url': url})
    return events, sorted(set(warnings))


def output_paths(directory, inputs):
    """Allow only two fixed filenames inside the repository's ignored local folder."""
    root = Path.cwd().resolve()
    directory = Path(os.path.abspath(directory))
    local = root / '.local-reports'
    if not directory.is_relative_to(local):
        raise ValueError('calendar output must be inside repository .local-reports')
    paths = [directory / name for name in ('deadlines.ics', 'followups.ics')]
    protected = {Path(p).resolve() for p in inputs}
    for path in paths:
        for component in (path, *path.parents):
            if component == root:
                break
            if component.is_symlink():
                raise ValueError('calendar output symlinks are not allowed')
        if path.resolve() in protected or (path.exists() and (not path.is_file() or path.stat().st_nlink != 1)):
            raise ValueError('calendar output collides with source data or unsafe file')
        result = subprocess.run(['git', 'check-ignore', '--quiet', '--', str(path)],
                                cwd=root, capture_output=True)
        if result.returncode != 0:
            raise ValueError('calendar output must be untracked and ignored by Git')
    return paths


def write_exports(paths, contents, inputs):
    """Stage both complete files before replacing either; each replacement is atomic."""
    directory = paths[0].parent
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = directory / '.calendar-export.lock'
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError('calendar export lock exists; no exports written') from None
    staged = []
    try:
        os.close(fd)
        for content in contents:
            with tempfile.NamedTemporaryFile(mode='wb', dir=directory, prefix='.jobwatch-', delete=False) as f:
                staged.append(Path(f.name))
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
        output_paths(directory, inputs)  # recheck immediately before replacement
        for tmp, path in zip(staged, paths):
            os.replace(tmp, path)
    finally:
        for tmp in staged:
            tmp.unlink(missing_ok=True)
        lock.unlink()


def _registry(path, field, kind):
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('schema') != 1 or not isinstance(data.get(field), kind):
        raise ValueError('invalid calendar source registry')
    return data[field]


def configure_parser(commands):
    parser = commands.add_parser('calendar', help='Export private local iCalendar files')
    sub = parser.add_subparsers(dest='calendar_command', required=True)
    export = sub.add_parser('export', help='Write separate deadline and follow-up ICS files; no upload')
    for flag, default in (('state', 'data/jobs.json'), ('verifications', 'data/verifications.json'),
                          ('leads', 'data/leads.json'), ('applications', 'data/applications.csv'),
                          ('output-dir', '.local-reports')):
        export.add_argument('--' + flag, type=Path, default=Path(default))


def run_cli(ns):
    inputs = [ns.state, ns.verifications, ns.leads, ns.applications]
    paths = output_paths(ns.output_dir, inputs)
    # Parse all inputs and render both files before any output is changed.
    state = load_state(ns.state)
    verifications = _registry(ns.verifications, 'postings', dict)
    leads = {'vacancies': _registry(ns.leads, 'vacancies', list)}
    applications = read_applications(ns.applications)
    deadlines, dw = deadline_events(state, verifications, leads)
    followups, fw = followup_events(applications)
    now = utcnow()
    write_exports(paths, [render(deadlines, now), render(followups, now)], inputs)
    from .render import text
    for path, events in zip(paths, (deadlines, followups)):
        print(f'{len(events)} events: {text(path.relative_to(Path.cwd().resolve()))}')
    for warning in sorted(set(dw + fw)):
        print('WARNING: ' + warning)
    return 1 if dw or fw else 0
