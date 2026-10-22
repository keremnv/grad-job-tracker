"""Owner-controlled CSV ledger. No network access or inferred application state."""
from __future__ import annotations

from contextlib import contextmanager
import csv
from datetime import date
from hashlib import sha256
import io
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit, urlunsplit

from .feeds import public_url
from .render import text, link

COLUMNS = ('job_key', 'company', 'role', 'url', 'stage', 'date_applied',
           'cv_version', 'next_action', 'next_action_date', 'notes')
STAGES = ('bookmarked', 'preparing', 'applied', 'assessment', 'interview',
          'offer', 'rejected', 'withdrawn')
EDITABLE = COLUMNS[4:]


def iso_date(value):
    """Blank dates are unknown; accept only YYYY-MM-DD, never compact/week dates."""
    if value and (not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value)):
        raise ValueError('date must be blank or YYYY-MM-DD')
    return date.fromisoformat(value) if value else None


def url_identity(url):
    if not public_url(url):
        raise ValueError('application URL must be HTTPS without credentials or whitespace')
    u = urlsplit(url)
    # Ignore fragments/default port only. Query parameters and paths can identify
    # distinct vacancies, so never guess which parts are tracking parameters.
    return urlunsplit(('https', u.hostname.lower(), u.path or '/', u.query, ''))


def validate(rows):
    keys, urls = set(), set()
    for number, row in enumerate(rows, 2):
        if set(row) != set(COLUMNS) or any(not isinstance(v, str) for v in row.values()):
            raise ValueError(f'application CSV row {number}: malformed fields')
        for field, value in row.items():
            if re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', value):
                raise ValueError(f'application CSV row {number}: control character in {field}')
            # CSV quoting alone cannot stop spreadsheet formula execution.
            # Reject, never silently prefix/alter owner notes or other values.
            if value.lstrip().startswith(('=', '+', '-', '@')):
                raise ValueError(f'application CSV row {number}: spreadsheet formula prefix in {field}')
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9:._-]{0,199}', row['job_key']):
            raise ValueError(f'application CSV row {number}: invalid job_key')
        if not row['company'].strip() or not row['role'].strip():
            raise ValueError(f'application CSV row {number}: company and role required')
        if row['stage'] not in STAGES:
            raise ValueError(f'application CSV row {number}: invalid stage')
        for field in ('date_applied', 'next_action_date'):
            try:
                iso_date(row[field])
            except ValueError:
                raise ValueError(f'application CSV row {number}: invalid {field}; use YYYY-MM-DD or blank') from None
        identity = url_identity(row['url'])
        if row['job_key'] in keys:
            raise ValueError(f'application CSV row {number}: duplicate job_key; no rows written')
        if identity in urls:
            raise ValueError(f'application CSV row {number}: duplicate vacancy URL; no rows written')
        keys.add(row['job_key']); urls.add(identity)
    return rows


def parse_csv(content):
    try:
        reader = csv.reader(io.StringIO(content.decode('utf-8-sig'), newline=''), strict=True)
        header = next(reader, [])
        if len(header) != len(COLUMNS) or set(header) != set(COLUMNS):
            raise ValueError('application CSV must contain exactly the ten supported columns')
        rows = []
        for number, values in enumerate(reader, 2):
            if len(values) != len(header):
                raise ValueError(f'application CSV row {number}: incorrect number of fields')
            rows.append(dict(zip(header, values)))
    except (csv.Error, UnicodeError):
        raise ValueError('malformed application CSV (expected UTF-8 and valid CSV quoting)') from None
    return validate(rows)


def snapshot(path):
    """Include file identity to catch replacement even when bytes are unchanged."""
    if path.is_symlink():
        raise ValueError('application ledger must not be a symbolic link')
    try:
        before = path.stat()
        content = path.read_bytes()
        after = path.stat()
    except FileNotFoundError:
        if path.exists():
            raise ValueError('application ledger changed while reading; retry') from None
        return None
    signature = lambda s: (s.st_dev, s.st_ino, s.st_mtime_ns, s.st_ctime_ns)
    if signature(before) != signature(after):
        raise ValueError('application ledger changed while reading; retry')
    return content, signature(after)


def read_applications(path: Path):
    version = snapshot(path)
    return parse_csv(version[0]) if version else []


@contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + '.lock')
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise ValueError('application lock exists; another edit may be running; no rows written') from None
    try:
        os.close(fd)
        yield
    finally:
        lock.unlink()


def _write(path, rows, before):
    """Replace a complete validated CSV, detecting edits up to replacement."""
    validate(rows)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', newline='',
                                         dir=path.parent, prefix='.jobwatch-', delete=False) as f:
            tmp = Path(f.name)
            writer = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator='\r\n')
            writer.writeheader(); writer.writerows(rows)
            f.flush(); os.fsync(f.fileno())
        if snapshot(path) != before:
            raise ValueError('application ledger changed during edit; no rows written; review and retry')
        os.replace(tmp, path)
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def add(path, row):
    with locked(path):
        before = snapshot(path)
        rows = parse_csv(before[0]) if before else []
        _write(path, rows + [row], before)


def update(path, key, changes):
    if not changes or set(changes) - set(EDITABLE):
        raise ValueError('update requires at least one supported application field')
    with locked(path):
        before = snapshot(path)
        rows = parse_csv(before[0]) if before else []
        row = next((r for r in rows if r['job_key'] == key), None)
        if row is None:
            raise ValueError('application job_key not found')
        row.update(changes)
        _write(path, rows, before)


def make_application(values, postings=None, verified_url=False, posting_id=None):
    values = {k: v for k, v in values.items() if v is not None}
    if posting_id:
        posting = (postings or {}).get(posting_id)
        if posting is None:
            raise ValueError('posting ID not found in tracked observations')
        if values.get('job_key') and values['job_key'] != posting_id:
            raise ValueError('job_key conflicts with posting ID')
        if values.get('url') and url_identity(values['url']) != url_identity(posting['url']):
            raise ValueError('URL conflicts with linked posting ID')
        values = {'company': posting['company'], 'role': posting['title'], 'url': posting['url'],
                  **values, 'job_key': posting_id}
    if not values.get('job_key'):
        if not verified_url:
            raise ValueError('supply --job-key or confirm the manually checked vacancy URL with --verified-url')
        values['job_key'] = 'manual:' + sha256(url_identity(values.get('url', '')).encode()).hexdigest()[:24]
    row = {field: values.get(field, '') for field in COLUMNS}
    row['stage'] = values.get('stage', 'bookmarked')
    return validate([row])[0]


def import_csv(path, source, confirm=False, preview=print):
    incoming = parse_csv(source.read_bytes())
    before = snapshot(path)
    rows = parse_csv(before[0]) if before else []
    combined = validate(rows + incoming)  # any duplicate aborts the entire import
    preview(f'Validated import: {len(incoming)} new rows; {len(rows)} existing rows. No notes displayed.')
    for row in incoming:
        preview(f"- {text(row['job_key'])}: {text(row['company'])} — {text(row['role'])}; {text(row['stage'])}; applied {text(row['date_applied'] or 'unknown')}")
    if confirm:
        with locked(path):
            if snapshot(path) != before:
                raise ValueError('application ledger changed after preview; review and retry')
            _write(path, combined, before)
        preview(f'Imported {len(incoming)} applications.')
    else:
        preview('Dry run only. Review the CSV, then repeat with --confirm to write.')


def configure_parser(subparsers):
    parser = subparsers.add_parser('applications', help='Manage the private owner CSV ledger')
    commands = parser.add_subparsers(dest='application_command', required=True)
    for command in ('add', 'update', 'list', 'import-csv'):
        p = commands.add_parser(command)
        p.add_argument('--applications', default='data/applications.csv')
        if command in ('add', 'update'):
            p.add_argument('--job-key', required=command == 'update')
            if command == 'add':
                p.add_argument('--company'); p.add_argument('--role'); p.add_argument('--url')
                p.add_argument('--posting-id', help='Existing ID from data/jobs.json')
                p.add_argument('--state', default='data/jobs.json')
                p.add_argument('--verified-url', action='store_true', help='Owner confirms URL identifies a manually checked vacancy; no fetch is performed')
            for field in EDITABLE:
                p.add_argument('--' + field.replace('_', '-'), choices=STAGES if field == 'stage' else None)
        elif command == 'list':
            p.add_argument('--stage', choices=STAGES)
            p.add_argument('--due-on-or-before', help='Owner next-action date YYYY-MM-DD; blanks excluded')
            p.add_argument('--due-on-or-after', help='Owner next-action date YYYY-MM-DD; blanks excluded')
        else:
            p.add_argument('source', type=Path)
            group = p.add_mutually_exclusive_group()
            group.add_argument('--dry-run', action='store_true', help='Validate and preview (default)')
            group.add_argument('--confirm', action='store_true', help='Explicitly approve writing the reviewed import')


def run_cli(ns):
    path = Path(ns.applications)
    if ns.application_command == 'add':
        from .state import load_state
        postings = load_state(Path(ns.state))['postings'] if ns.posting_id else {}
        row = make_application({k: getattr(ns, k) for k in COLUMNS}, postings, ns.verified_url, ns.posting_id)
        add(path, row)
        print(f"Added {text(row['job_key'])}.")
    elif ns.application_command == 'update':
        update(path, ns.job_key, {k: getattr(ns, k) for k in EDITABLE if getattr(ns, k) is not None})
        print(f'Updated {text(ns.job_key)}.')
    elif ns.application_command == 'import-csv':
        import_csv(path, ns.source, ns.confirm)
    else:
        lower, upper = iso_date(ns.due_on_or_after), iso_date(ns.due_on_or_before)
        if lower and upper and lower > upper:
            raise ValueError('due-date range is reversed')
        rows = read_applications(path)
        shown = 0
        for row in sorted(rows, key=lambda r: (r['next_action_date'] or '9999', r['job_key'])):
            due = iso_date(row['next_action_date'])
            if ns.stage and ns.stage != row['stage']:
                continue
            if (lower or upper) and (due is None or (lower and due < lower) or (upper and due > upper)):
                continue
            print(f"- {text(row['job_key'])}: {text(row['company'])} — {link(row['role'], row['url'])}; {text(row['stage'])}; owner due {text(row['next_action_date'] or 'unknown')}")
            shown += 1
        print(f'{shown} application(s). Notes and next-action text are omitted from console output.')
    return 0
