"""Source-locked PostgreSQL publication/rescreening. Network collection is outside transactions."""
from hashlib import sha256
import json
import time
from uuid import uuid4

from jobwatch import feeds
from .collection import Processing, Run, fetch, prepare, replay_native, content, iso
from .connection import DatabaseConfig, DatabaseError, connect
from .evidence import encode_evidence
from .material import COMPARISON_VERSION, compare, describe

FIELDS = ('company', 'title', 'posting_url', 'location_text', 'country', 'countries', 'department', 'team',
          'employment_type', 'salary', 'published_at', 'source_dates', 'deadlines', 'description_text', 'extra_fields')
SCREEN = ('verdict', 'family', 'codes', 'rules_version', 'input_sha256', 'basis')


class CollectionError(RuntimeError):
    def __init__(self, message, run_id=None, outcome='unknown', sqlstate=None):
        super().__init__(message)
        self.run_id, self.outcome = run_id, outcome
        self.sqlstate = sqlstate


def _state(exc):
    value = getattr(exc, 'sqlstate', None)
    return value if isinstance(value, str) and len(value) == 5 and value.isascii() and value.isalnum() else None


def _snapshot(row):
    return {name: row[name] for name in FIELDS}


def _carry_deadlines(new, old):
    if old is not None and content(new)['deadlines'] == content(old)['deadlines']:
        return {**new, 'deadlines': old['deadlines']}
    return new


def _diff(before, after):
    a, b = content(before), content(after)
    return {key: {'before': describe(a[key]), 'after': describe(b[key])} for key in a if a[key] != b[key]}


def _version(conn, key, source_id, evidence, snapshot, normalizer, nul, *, origin, first_run_id, observed_at, provenance=None):
    from psycopg.types.json import Jsonb
    digest = sha256(encode_evidence(content(snapshot))).hexdigest()
    existing = conn.execute('SELECT id FROM jobwatch.posting_versions WHERE posting_key=%s AND evidence_sha256=%s '
        'AND normalizer_version=%s AND normalized_sha256=%s',
        (key, sha256(evidence).hexdigest(), normalizer, digest)).fetchone()
    if existing:
        return existing['id']
    vid = uuid4()
    columns = ('id', 'posting_key', 'source_id', 'evidence', 'normalizer_version', 'normalized_sha256',
               'nul_replaced', 'origin', 'first_run_id', 'observed_at', 'provenance') + FIELDS
    values = [vid, key, source_id, evidence, normalizer, digest, nul, origin, first_run_id, observed_at, Jsonb(provenance or {})]
    values += [Jsonb(snapshot[k]) if k in ('source_dates', 'deadlines', 'extra_fields') else snapshot[k] for k in FIELDS]
    conn.execute('INSERT INTO jobwatch.posting_versions (' + ','.join(columns) + ') VALUES (' + ','.join(['%s'] * len(values)) + ')', values)
    return vid


class Collector:
    def __init__(self, config=None, *, actor, processing=Processing(), attempts=3):
        if not actor or not actor.strip() or type(attempts) is not int or not 1 <= attempts <= 5:
            raise ValueError('Provide an actor and 1..5 transaction attempts')
        self.config = config or DatabaseConfig.from_env()
        self.actor, self.processing, self.attempts = actor, processing, attempts

    def _connection(self):
        return connect(self.config)

    def _actor(self, conn):
        conn.execute("SELECT set_config('jobwatch.actor',%s,true)", (self.actor,))

    def _retry(self, operation):
        import psycopg
        for attempt in range(self.attempts):
            try:
                return operation()
            except (psycopg.Error, DatabaseError) as exc:
                code = getattr(exc, 'sqlstate', None)
                transient = isinstance(exc, (DatabaseError, psycopg.OperationalError, psycopg.InterfaceError)) or code in ('40001', '40P01', '55P03') or (code or '').startswith('08')
                if not transient or attempt + 1 == self.attempts:
                    raise
                time.sleep(.05 * (attempt + 1))

    def register(self, source_id, *, run_id=None, request_version=None, advance_from=None):
        if any(value is not None and (not isinstance(value, str) or not value.strip()) for value in (request_version, advance_from)):
            raise ValueError('Request and expected processing revisions must be nonempty identifiers')
        rid = run_id or uuid4()
        try:
            return self._retry(lambda: self._register_once(source_id, rid, request_version, advance_from))
        except CollectionError:
            raise
        except Exception as exc:
            raise CollectionError('Registration failed; retry registration with the same UUID', rid, sqlstate=_state(exc)) from None

    def _register_once(self, source_id, rid, request_version, advance_from):
        from psycopg.types.json import Jsonb
        from .collection import REQUESTS
        with self._connection() as conn, conn.transaction():
            self._actor(conn)
            row = conn.execute('SELECT * FROM jobwatch.collection_runs WHERE id=%s', (rid,)).fetchone()
            if not row:
                src = conn.execute('SELECT s.*,e.name AS company FROM jobwatch.sources s LEFT JOIN jobwatch.employers e ON e.id=s.employer_id WHERE s.id=%s', (source_id,)).fetchone()
                if not src or not src['company'] or src['processing_revision'] not in (advance_from or self.processing.revision, self.processing.revision):
                    raise CollectionError('Unknown/unassociated source or stale processing release', rid)
                metadata = {'source': {k: src[k] for k in ('provider', 'board', 'region', 'company', 'employer_id', 'status')},
                            'normalizer_version': self.processing.normalizer_version, 'rules_version': self.processing.rules_version,
                            'comparison_version': COMPARISON_VERSION, 'advance_from': advance_from}
                conn.execute("INSERT INTO jobwatch.collection_runs(id,source_id,mode,request_version,processing_revision,details) "
                    "VALUES (%s,%s,'publish',%s,%s,%s) ON CONFLICT(id) DO NOTHING",
                    (rid, source_id, request_version or REQUESTS[src['provider']], self.processing.revision, Jsonb(metadata)))
                row = conn.execute('SELECT * FROM jobwatch.collection_runs WHERE id=%s', (rid,)).fetchone()
            expected_request = request_version or REQUESTS[row['details']['source']['provider']]
            if (row['source_id'], row['mode'], row['request_version'], row['processing_revision'],
                row['details'].get('normalizer_version'), row['details'].get('rules_version'), row['details'].get('advance_from'), row['details'].get('comparison_version')) != (
                    source_id, 'publish', expected_request, self.processing.revision,
                    self.processing.normalizer_version, self.processing.rules_version, advance_from, COMPARISON_VERSION):
                raise CollectionError('Run UUID is already bound to different registration metadata', rid)
            return Run(row['id'], source_id, row['run_order'], row['started_at'], row['request_version'],
                       row['processing_revision'], row['status'], encode_evidence(row['details']))

    def _lock(self, conn, source_id, rid=None):
        src = conn.execute('SELECT * FROM jobwatch.sources WHERE id=%s FOR NO KEY UPDATE', (source_id,)).fetchone()
        if not src:
            raise CollectionError('Source no longer exists', rid)
        run = conn.execute('SELECT * FROM jobwatch.collection_runs WHERE id=%s FOR UPDATE', (rid,)).fetchone() if rid else None
        if rid and (not run or run['source_id'] != source_id):
            raise CollectionError('Run does not belong to source', rid)
        return src, run

    def _finish(self, conn, rid, status, *, error=None, observation=None):
        if observation:
            conn.execute('UPDATE jobwatch.collection_runs SET status=%s,finished_at=clock_timestamp(),error=%s,fetched_at=%s,source_records=%s,vacancies=%s,skipped_non_vacancies=%s,'
                'absence_safe=%s,payload_sha256=%s WHERE id=%s',
                (status, error, observation.fetched_at, observation.source_records, len(observation.records), observation.skipped,
                 observation.absence_safe, observation.digest, rid))
        else:
            conn.execute('UPDATE jobwatch.collection_runs SET status=%s,finished_at=clock_timestamp(),error=%s WHERE id=%s', (status, error, rid))
        return status

    def fail(self, registration, code='collection_failed', observation=None, *, sqlstate=None):
        # Code is an internal classification, not a driver/provider message or payload snippet.
        allowed = {'collection_failed', 'publication_failed', 'source_not_monitored'}
        if code not in allowed or (sqlstate is not None and (len(sqlstate) != 5 or not sqlstate.isascii() or not sqlstate.isalnum())):
            raise ValueError('Unknown failure code')
        def finish():
            with self._connection() as conn, conn.transaction():
                self._actor(conn)
                _, row = self._lock(conn, registration.source_id, registration.id)
                if observation and row['payload_sha256'] and row['payload_sha256'] != observation.digest:
                    raise CollectionError('Run already bound to a different validated payload', registration.id, row['status'])
                if row['status'] != 'running':
                    return row['status']
                return self._finish(conn, registration.id, 'failed', error=code + (':' + sqlstate if sqlstate else ''), observation=observation)
        return self._retry(finish)

    def collect_source(self, source_id, *, get_json=feeds.request_json, pause=time.sleep, advance_from=None):
        registration = self.register(source_id, advance_from=advance_from)  # every new network collection gets a new UUID
        if registration.metadata['source']['status'] != 'monitored':
            def skip():
                with self._connection() as conn, conn.transaction():
                    self._actor(conn)
                    _, row = self._lock(conn, source_id, registration.id)
                    return row['status'] if row['status'] != 'running' else self._finish(conn, registration.id, 'skipped')
            try:
                return registration.id, self._retry(skip)
            except Exception:
                raise CollectionError('Source skip outcome unconfirmed', registration.id) from None
        try:
            observation = fetch(registration, self.processing, get_json=get_json, pause=pause)
        except Exception:
            try:
                outcome = self.fail(registration)
            except Exception:
                outcome = 'unknown'
            raise CollectionError('Collection failed; no observations were published', registration.id, outcome) from None
        return registration.id, self.publish(observation)

    def publish(self, observation):
        r = observation.run
        if (r.processing_revision != self.processing.revision or r.metadata.get('normalizer_version') != self.processing.normalizer_version
                or r.metadata.get('rules_version') != self.processing.rules_version
                or r.metadata.get('comparison_version') != COMPARISON_VERSION
                or type(observation.source_records) is not int or observation.source_records < 0
                or type(observation.skipped) is not int or observation.skipped < 0
                or type(observation.request_count) is not int or observation.request_count < 1
                or type(observation.absence_safe) is not bool
                or observation.source_records != len(observation.records) + observation.skipped
                or len({x.key for x in observation.records}) != len(observation.records)
                or list(observation.records) != sorted(observation.records, key=lambda x: x.key)):
            raise CollectionError('Invalid observation or incompatible processing release', r.id)
        iso(observation.fetched_at)
        try:
            return self._retry(lambda: self._publish_once(observation))
        except CollectionError:
            raise
        except Exception as exc:
            try:
                outcome = self.fail(r, 'publication_failed', observation, sqlstate=_state(exc))
            except Exception:
                outcome = 'unknown'
            if outcome == 'published':  # confirmed under the source/run locks, including the payload digest
                return 'published'
            raise CollectionError('Publication failed; retain the payload when outcome is unknown', r.id, outcome, _state(exc)) from None

    def _event(self, conn, row, run_id, at, category, kind, before, after, changes=None):
        from psycopg.types.json import Jsonb
        conn.execute('INSERT INTO jobwatch.posting_events(posting_key,source_id,run_id,observed_at,category,kind,before_version_id,'
            'after_version_id,comparison_version,changes) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',
            (row['posting_key'], row['source_id'], run_id, at, category, kind, before, after, COMPARISON_VERSION, Jsonb(changes or {})))

    def _current(self, conn, source_id):
        return conn.execute('SELECT p.*,v.*,r.request_version AS last_request FROM jobwatch.postings p '
            'JOIN jobwatch.posting_versions v ON v.id=p.current_version_id '
            'LEFT JOIN jobwatch.collection_runs r ON r.id=coalesce(p.last_seen_run_id,v.first_run_id) '
            'WHERE p.source_id=%s ORDER BY p.posting_key', (source_id,)).fetchall()

    def _renormalize(self, conn, row, src, run_id, at):
        old = _snapshot(row)
        if row['normalizer_version'] == self.processing.normalizer_version or row['origin'] == 'legacy_index':
            return old, row['current_version_id'], False
        entry = {**src, 'company': row['company']}
        prepared = prepare(replay_native(entry, json.loads(bytes(row['evidence']))), row['observed_at'], self.processing)
        new = _carry_deadlines(json.loads(prepared.normalized), old)
        vid = _version(conn, row['posting_key'], row['source_id'], bytes(row['evidence']), new,
            self.processing.normalizer_version, prepared.nul_replaced, origin=row['origin'], first_run_id=row['first_run_id'],
            observed_at=row['observed_at'], provenance=row['provenance'])
        changes = _diff(old, new)
        if changes:
            self._event(conn, row, run_id, at, 'normalizer', 'renormalized', row['current_version_id'], vid,
                        {'changed': changes, 'before_normalizer': row['normalizer_version'], 'after_normalizer': self.processing.normalizer_version})
        return new, vid, bool(changes)

    def _screen(self, conn, row, snapshot, before_vid, after_vid, run_id, at, *, source_changed=False, normalizer_changed=False):
        result = self.processing.judge(snapshot)
        before = {k: row[{'input_sha256': 'screening_input_sha256', 'basis': 'screening_basis'}.get(k, k)] for k in SCREEN}
        if any(before[k] != result[k] for k in ('verdict', 'family', 'codes')):
            self._event(conn, row, run_id, at, 'screening', 'rescreened', before_vid, after_vid,
                {'before': before, 'after': result, 'cause': {'rules_changed': before['rules_version'] != result['rules_version'],
                 'inputs_changed': before['input_sha256'] != result['input_sha256'], 'source_changed': source_changed,
                 'normalizer_changed': normalizer_changed}})
        conn.execute('UPDATE jobwatch.postings SET verdict=%s,family=%s,codes=%s,rules_version=%s,screening_input_sha256=%s,'
            'screening_basis=%s,screened_at=clock_timestamp() WHERE posting_key=%s',
            tuple(result[k] for k in SCREEN) + (row['posting_key'],))

    def _publish_once(self, observation):
        r = observation.run
        with self._connection() as conn, conn.transaction():
            self._actor(conn)
            src, attempt = self._lock(conn, r.source_id, r.id)
            if attempt['details'] != r.metadata or attempt['request_version'] != r.request_version or attempt['processing_revision'] != r.processing_revision:
                raise CollectionError('Observation registration metadata does not match database', r.id)
            if attempt['payload_sha256'] and attempt['payload_sha256'] != observation.digest:
                raise CollectionError('Run already bound to a different validated payload', r.id)
            if attempt['status'] != 'running':
                return attempt['status']
            if src['status'] != 'monitored' or src['employer_id'] != r.metadata['source']['employer_id']:
                return self._finish(conn, r.id, 'skipped', observation=observation)
            last = conn.execute('SELECT run_order FROM jobwatch.collection_runs WHERE id=%s', (src['last_published_run_id'],)).fetchone()
            expected = r.metadata.get('advance_from') or r.processing_revision
            if src['processing_revision'] not in (expected, r.processing_revision) or (last and last['run_order'] >= attempt['run_order']):
                return self._finish(conn, r.id, 'superseded', observation=observation)
            current = {row['posting_key']: row for row in self._current(conn, r.source_id)}
            seen = set()
            at = observation.fetched_at
            records = {record.key: record for record in observation.records}
            for key in sorted(current.keys() | records.keys()):
                if key not in records:
                    row = current[key]
                    snapshot, vid, normalized = self._renormalize(conn, row, src, r.id, at)
                    self._screen(conn, row, snapshot, row['current_version_id'], vid, r.id, at, normalizer_changed=normalized)
                    conn.execute('UPDATE jobwatch.postings SET current_version_id=%s WHERE posting_key=%s', (vid, key))
                    if observation.absence_safe and row['status'] == 'listed':
                        conn.execute("UPDATE jobwatch.postings SET status='not_seen',missing_since=%s WHERE posting_key=%s", (at, key))
                        self._event(conn, row, r.id, at, 'observation', 'not_seen', row['current_version_id'], vid)
                    continue
                rec = records[key]
                if key != f"{src['provider']}:{src['board'].lower()}:{rec.native_id}":
                    raise CollectionError('Posting identity does not match source', r.id)
                seen.add(key)
                old = current.get(key)
                new = json.loads(rec.normalized)
                intermediate, before_vid, normalized = self._renormalize(conn, old, src, r.id, at) if old else (None, None, False)
                new = _carry_deadlines(new, intermediate)
                if old is None:
                    result = self.processing.judge(new)
                    basis = 'index_backfill' if src['last_published_run_id'] is None else 'observed'
                    conn.execute("INSERT INTO jobwatch.postings(posting_key,source_id,provider,board,native_id,status,first_seen_at,first_seen_basis,"
                        "current_version_id,verdict,family,codes,rules_version,screening_input_sha256,screening_basis,screened_at) "
                        "VALUES (%s,%s,%s,%s,%s,'listed',%s,%s,%s,%s,%s,%s,%s,%s,%s,clock_timestamp())",
                        (key, r.source_id, src['provider'], src['board'], rec.native_id, at, basis, uuid4()) + tuple(result[k] for k in SCREEN))
                vid = _version(conn, key, r.source_id, rec.evidence, new, self.processing.normalizer_version, rec.nul_replaced,
                               origin='collector', first_run_id=r.id, observed_at=at)
                row = {'posting_key': key, 'source_id': r.source_id}
                changed = {}
                if old is None:
                    self._event(conn, row, r.id, at, 'observation', 'backfill' if basis == 'index_backfill' else 'first_seen', None, vid)
                else:
                    if old['origin'] == 'legacy_index':
                        baseline = {'native_evidence': True, 'legacy_comparable_output_changes': _diff(intermediate, new)}
                    else:
                        changed, baseline = compare(src['provider'], json.loads(bytes(old['evidence'])), json.loads(rec.evidence), old['last_request'], r.request_version)
                    if changed or baseline:
                        self._event(conn, row, r.id, at, 'provider', 'changed' if changed else 'baseline', before_vid, vid,
                            {'changed': changed, 'baseline': baseline, 'observed_interval': {'after': iso(old['last_seen_at']) if old['last_seen_at'] else None, 'through': iso(at)}})
                    if old['status'] == 'not_seen':
                        self._event(conn, row, r.id, at, 'observation', 'reappeared', old['current_version_id'], vid)
                    self._screen(conn, old, new, old['current_version_id'], vid, r.id, at, source_changed=bool(changed), normalizer_changed=normalized)
                conn.execute("UPDATE jobwatch.postings SET current_version_id=%s,status='listed',missing_since=NULL,last_seen_at=%s,last_seen_run_id=%s WHERE posting_key=%s", (vid, at, r.id, key))
            if observation.absence_safe:
                count = conn.execute("SELECT count(*) AS n FROM jobwatch.postings WHERE source_id=%s AND status='listed'", (r.source_id,)).fetchone()['n']
                if count != len(seen):
                    raise CollectionError('Published identity accounting mismatch', r.id)
            self._finish(conn, r.id, 'published', observation=observation)
            conn.execute('UPDATE jobwatch.sources SET last_published_run_id=%s WHERE id=%s', (r.id, r.source_id))
            if r.metadata.get('advance_from') is not None:
                conn.execute('UPDATE jobwatch.sources SET processing_revision=%s WHERE id=%s', (self.processing.revision, r.source_id))
        return 'published'

    def rescreen_source(self, source_id, *, advance_from=None):
        if advance_from is not None and (not isinstance(advance_from, str) or not advance_from.strip()):
            raise ValueError('Expected processing revision must be a nonempty identifier')
        def apply():
            with self._connection() as conn, conn.transaction():
                self._actor(conn)
                src, _ = self._lock(conn, source_id)
                expected = advance_from or self.processing.revision
                if src['status'] != 'monitored':
                    return 'skipped'
                if src['processing_revision'] not in (expected, self.processing.revision):
                    return 'superseded'
                at = conn.execute('SELECT clock_timestamp() AS at').fetchone()['at']
                for row in self._current(conn, source_id):
                    snapshot, vid, normalized = self._renormalize(conn, row, src, None, at)
                    self._screen(conn, row, snapshot, row['current_version_id'], vid, None, at, normalizer_changed=normalized)
                    conn.execute('UPDATE jobwatch.postings SET current_version_id=%s WHERE posting_key=%s', (vid, row['posting_key']))
                conn.execute('UPDATE jobwatch.sources SET processing_revision=%s WHERE id=%s', (self.processing.revision, source_id))
            return 'rescreened'
        try:
            return self._retry(apply)
        except CollectionError:
            raise
        except Exception as exc:
            raise CollectionError('Rescreen outcome unconfirmed; rerun with the same expected revision', sqlstate=_state(exc)) from None

    def rescreen_all(self, *, advance_from=None):
        with self._connection() as conn:
            ids = [row['id'] for row in conn.execute("SELECT id FROM jobwatch.sources WHERE status='monitored' ORDER BY id")]
        return {sid: self.rescreen_source(sid, advance_from=advance_from) for sid in ids}
