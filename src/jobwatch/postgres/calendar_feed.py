"""Private derived ICS snapshots. No network, scheduler, credentials or database writes."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import stat
from uuid import uuid4
from zoneinfo import ZoneInfo

from jobwatch import calendar
from jobwatch.config import timestamp
from .reports import calendar_events

SCHEMA = 'jobwatch.calendar-snapshot/1'
FEEDS = ('deadlines', 'actions')
FILES = ('deadlines.ics', 'actions.ics', 'combined.ics')
UID = re.compile(r'[0-9a-f]{64}@jobwatch\.local')
HEX = re.compile(r'[0-9a-f]{64}')
GENERATION = re.compile(r'snapshot-[0-9a-f]{32}')
MAX_BYTES = 900_000  # Below Proton's documented 1 MB subscription bound.
MAX_STATE = 20_000
MAX_MANIFEST = 32*1024*1024


class FeedError(ValueError):
    """Fixed diagnostics only: no owner text, SQL, credentials or feed URLs."""


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def digest(value):
    return sha256(encoded(value)).hexdigest()


def check_manifest(value):
    try:
        if value['schema'] != SCHEMA or len(value['events']) > MAX_STATE:
            raise ValueError()
        now = timestamp(value['generated_at'])
        for uid, row in value['events'].items():
            if (not UID.fullmatch(uid) or type(row['active']) is not bool
                    or row['feed'] not in FEEDS or not HEX.fullmatch(row['fingerprint'])
                    or type(row['sequence']) is not int or not 0 <= row['sequence'] <= 2147483647
                    or not row['aliases'] or len(row['aliases']) > 10000
                    or not all(HEX.fullmatch(a) for a in row['aliases'])
                    or row['anchor'] not in row['aliases']
                    or not timestamp(row['created_at']) <= timestamp(row['modified_at']) <= now
                    or (row.get('superseded_by') and row['superseded_by'] not in value['events'])):
                raise ValueError()
        if set(value['files']) != set(FILES):
            raise ValueError()
        for row in value['files'].values():
            if not HEX.fullmatch(row['sha256']) or not 0 < row['bytes'] <= MAX_BYTES:
                raise ValueError()
            if timestamp(row['modified_at']) > now:
                raise ValueError()
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
        raise FeedError('Invalid calendar snapshot metadata; preserve and inspect it') from None
    return value


def build(data, previous=None, *, feeds=FEEDS, past_days=30, future_days=366, max_bytes=MAX_BYTES):
    """Pure projection + transport revisioning. Metadata contains hashes, never event text."""
    if (not feeds or len(feeds) != len(set(feeds)) or any(f not in FEEDS for f in feeds)
            or type(past_days) is not int or not 0 <= past_days <= 3660
            or type(future_days) is not int or not 1 <= future_days <= 3660
            or type(max_bytes) is not int or not 128 <= max_bytes <= MAX_BYTES):
        raise FeedError('Invalid calendar publication bounds')
    now = data['generated_at']
    at = timestamp(now)
    old = check_manifest(previous) if previous is not None else None
    if old and at < timestamp(old['generated_at']):
        raise FeedError('Calendar publication clock moved backwards')
    today = at.astimezone(ZoneInfo('Europe/London')).date()
    first, last = today-timedelta(days=past_days), today+timedelta(days=future_days)
    projected, warnings = calendar_events(data, minimal=True)
    events = []
    for feed in FEEDS:
        for event in projected[feed] if feed in feeds else []:
            when = event['date'][2]
            day = (datetime.strptime(when[5:], '%Y%m%dT%H%M%SZ').replace(tzinfo=timezone.utc).astimezone(ZoneInfo('Europe/London')).date() if when.startswith('time:')
                   else datetime.fromisoformat(when[5:]).date())
            if not first <= day <= last:
                continue
            if not UID.fullmatch(event['uid']):
                raise FeedError('Invalid projected calendar identity')
            aliases = sorted({digest([feed, a]) for a in event['aliases']})
            if not aliases:
                raise FeedError('Missing projected calendar identity')
            events.append({**event, 'aliases': aliases, 'feed': feed})
    if len(events) > 5000:
        raise FeedError('Calendar has too many events; narrow the explicit date window')
    seen_aliases = set()
    for event in events:
        if seen_aliases.intersection(event['aliases']):
            raise FeedError('Projected calendar identities overlap; inspect source links')
        seen_aliases.update(event['aliases'])
    state = {k: dict(v) for k,v in old['events'].items()} if old else {}
    index = {}
    for uid, row in state.items():
        if not row.get('superseded_by'):
            for alias in row['aliases']:
                index.setdefault(alias, set()).add(uid)

    def candidates(event):
        return {uid for a in event['aliases'] for uid in index.get(a, ())}

    # On unlink/split, retain an old UID for the component containing its original anchor.
    events.sort(key=lambda e: (not any(state[u]['anchor'] in e['aliases'] for u in candidates(e)), e['uid']))
    selected, rendered = set(), {f: [] for f in FEEDS}
    for event in events:
        possible = candidates(event)-selected
        uid = (min(possible, key=lambda u: (timestamp(state[u]['created_at']), u)) if possible else event['uid'])
        if uid in selected:
            raise FeedError('Calendar identity split is ambiguous; inspect source links')
        prior = state.get(uid)
        fingerprint = digest({k: event.get(k) for k in ('date','summary','description','url')})
        changed = prior is None or prior['fingerprint'] != fingerprint or not prior['active'] or prior.get('superseded_by')
        sequence = (prior['sequence']+1 if changed else prior['sequence']) if prior else 0
        modified = now if changed else prior['modified_at']
        if sequence > 2147483647:
            raise FeedError('Calendar sequence exhausted; preserve the publication state')
        state[uid] = {'feed': event['feed'], 'aliases': event['aliases'],
            'anchor': prior['anchor'] if prior and prior['anchor'] in event['aliases'] else event['aliases'][0],
            'fingerprint': fingerprint, 'sequence': sequence, 'modified_at': modified,
            'created_at': prior['created_at'] if prior else now, 'active': True}
        selected.add(uid)
        rendered[event['feed']].append({**event, 'uid': uid, 'sequence': sequence, 'modified_at': modified})
        for retired in possible-{uid}:
            state[retired] = {**state[retired], 'superseded_by': uid}
    for uid, row in state.items():
        if uid not in selected and row['active']:
            row.update(active=False, sequence=row['sequence']+1, modified_at=now)
    if len(state) > MAX_STATE:
        raise FeedError('Calendar metadata limit exceeded; preserve state and review retention')
    contents, file_info = {}, {}
    for name in FILES:
        items = rendered['deadlines']+rendered['actions'] if name=='combined.ics' else rendered[name[:-4]]
        body = calendar.render(items, now)
        if len(body) > max_bytes:
            raise FeedError('Calendar feed exceeds size bound; narrow the explicit date window')
        checksum = sha256(body).hexdigest()
        before = old['files'][name] if old else None
        contents[name] = body
        file_info[name] = {'sha256': checksum, 'bytes': len(body), 'events': len(items),
            'modified_at': before['modified_at'] if before and before['sha256']==checksum else now}
    manifest = {'schema': SCHEMA, 'generated_at': now, 'enabled_feeds': sorted(feeds),
        'window': {'start': first.isoformat(), 'end': last.isoformat(), 'timezone': 'Europe/London'},
        'files': file_info, 'events': state, 'warnings': warnings}
    check_manifest(manifest)
    if len(encoded(manifest)) > MAX_MANIFEST:
        raise FeedError('Calendar manifest size bound exceeded')
    return contents, manifest


def private_directory(path):
    for item in (path, *path.parents):
        if item.is_symlink():
            raise FeedError('Calendar output cannot traverse symlinks')
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise FeedError('Calendar output must be a private owned directory (0700)')


def read_file(path, limit):
    fd = os.open(path, os.O_RDONLY|os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1
                or info.st_mode & 0o077 or info.st_size > limit):
            raise FeedError('Unsafe or oversized calendar artifact')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            return stream.read(limit+1)
    finally:
        os.close(fd)


def read_generation(directory):
    private_directory(directory)
    raw = read_file(directory/'manifest.json', MAX_MANIFEST)
    if sha256(raw).hexdigest().encode()+b'\n' != read_file(directory/'manifest.sha256', 65):
        raise FeedError('Calendar manifest checksum mismatch')
    try:
        manifest = check_manifest(json.loads(raw))
    except (ValueError, TypeError):
        raise FeedError('Invalid calendar manifest') from None
    for filename, meta in manifest['files'].items():
        body = read_file(directory/filename, MAX_BYTES)
        if len(body) != meta['bytes'] or sha256(body).hexdigest() != meta['sha256']:
            raise FeedError('Calendar file checksum mismatch')
    return directory, manifest


def current_snapshot(root):
    private_directory(root)
    link = root/'current'
    if not link.is_symlink():
        raise FeedError('Missing calendar state; restore it or explicitly initialise a new subscription')
    name = os.readlink(link)
    if not GENERATION.fullmatch(name):
        raise FeedError('Invalid calendar generation pointer')
    return read_generation(root/name)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def publication_lock(root):
    fd = os.open(root/'.publish.lock', os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or info.st_nlink != 1 or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise FeedError('Unsafe calendar lock')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            raise FeedError('Another calendar publication is active') from None
        yield
    finally:
        os.close(fd)


def publish(data, output, *, initialize=False, **options):
    root = Path(os.path.abspath(output))
    if initialize:
        if root.exists() and set(p.name for p in root.iterdir())- {'.publish.lock'}:
            raise FeedError('Calendar initialisation requires a new empty directory')
        # Validate ancestors before mkdir so an untrusted symlink is never followed.
        for parent in (root,*root.parents):
            if parent.is_symlink():
                raise FeedError('Calendar output cannot traverse symlinks')
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_directory(root)
    with publication_lock(root):
        previous_dir, previous = (None,None) if initialize else current_snapshot(root)
        if initialize and (root/'current').exists():
            raise FeedError('Calendar publication was already initialised')
        contents, manifest = build(data, previous, **options)
        directory = root/('snapshot-'+uuid4().hex)
        directory.mkdir(mode=0o700)
        temporary_link = root/('.current-'+uuid4().hex)
        committed = False
        try:
            raw = encoded(manifest)
            for name, body in {**contents, 'manifest.json':raw,
                               'manifest.sha256':sha256(raw).hexdigest().encode()+b'\n'}.items():
                fd = os.open(directory/name, os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd,'wb') as stream:
                    stream.write(body);stream.flush();os.fsync(stream.fileno())
                if name in contents:
                    at = timestamp(manifest['files'][name]['modified_at']).timestamp()
                    os.utime(directory/name,(at,at),follow_symlinks=False)
            sync_directory(directory)
            os.symlink(directory.name, temporary_link)
            os.replace(temporary_link,root/'current')
            committed = True
            sync_directory(root)
        finally:
            if temporary_link.is_symlink():
                temporary_link.unlink()
            if not committed:
                shutil.rmtree(directory)
        # Keep current and its predecessor. Never remove unrelated directories/files.
        keep = {directory,previous_dir}
        for older in root.iterdir():
            if older not in keep and GENERATION.fullmatch(older.name) and not older.is_symlink():
                try:
                    private_directory(older)
                    if set(p.name for p in older.iterdir()) == set(FILES)|{'manifest.json','manifest.sha256'}:
                        read_generation(older)
                        shutil.rmtree(older)
                except (OSError, FeedError):
                    pass  # Cache cleanup cannot undo a completed pointer publication.
        return summary(manifest)


def summary(manifest, *, now=None, max_age_hours=2):
    age = (timestamp(now or datetime.now(timezone.utc).isoformat())-timestamp(manifest['generated_at'])).total_seconds()
    return {'schema':SCHEMA,'generated_at':manifest['generated_at'],'age_seconds':age,
            'fresh':0 <= age <= max_age_hours*3600,'files':manifest['files'],
            'window':manifest['window'],'warnings':manifest['warnings'],
            'external_subscription_verified':False,'guaranteed_alerting':False}
