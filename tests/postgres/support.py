"""Small SQL fixtures adapted from the approved review; no collector implementation.

The ONLY test destination is a labelled, running Docker container created by
scripts/test_postgres.sh. No runtime/admin URL from the caller is ever consumed.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
import os
import subprocess
import unittest
from uuid import uuid4

from jobwatch.postgres.connection import DatabaseConfig, connect
from jobwatch.postgres.evidence import encode_evidence
from jobwatch.postgres.migrate import migrate

ENABLED = bool(os.environ.get('JOBWATCH_PG_TEST_CONTAINER'))
DOMAIN_TABLES = ('employers', 'sources', 'collection_runs', 'postings', 'posting_versions',
                 'posting_events', 'verifications', 'leads', 'triage', 'applications', 'owner_audit')


@dataclass(frozen=True)
class Target:
    port: int

    @classmethod
    def verified(cls):
        name = os.environ.get('JOBWATCH_PG_TEST_CONTAINER', '')
        token = os.environ.get('JOBWATCH_PG_TEST_TOKEN', '')
        if not name.startswith('jobwatch-foundation-test-') or not token:
            raise RuntimeError('Use scripts/test_postgres.sh; no arbitrary database target is allowed')
        info = json.loads(subprocess.check_output(['docker', 'inspect', name]))[0]
        labels = info['Config']['Labels']
        if not info['State']['Running'] or labels.get('jobwatch.test') != 'postgres-foundation' or labels.get('jobwatch.test-token') != token:
            raise RuntimeError('Disposable container identity verification failed')
        bindings = info['NetworkSettings']['Ports']['5432/tcp']
        if len(bindings) != 1 or bindings[0]['HostIp'] != '127.0.0.1':
            raise RuntimeError('Tests require an exclusively loopback-bound disposable database')
        return cls(int(bindings[0]['HostPort']))

    def config(self, user='jobwatch_migrator'):
        from psycopg.conninfo import make_conninfo
        # hostaddr also overrides an inherited PGHOSTADDR. All addressing is explicit.
        dsn = make_conninfo(host='127.0.0.1', hostaddr='127.0.0.1', port=self.port,
                            dbname='jobwatch_foundation_test', user=user, sslmode='disable')
        return DatabaseConfig(dsn, purpose='migration' if user == 'jobwatch_migrator' else 'runtime',
                              connect_timeout=5, statement_timeout_ms=10_000, lock_timeout_ms=5_000,
                              idle_transaction_timeout_ms=10_000)

    def connect(self, user='jobwatch_migrator'):
        return connect(self.config(user))

    def reset_schema(self):
        # Target.verified() has already established ownership of this disposable target.
        with self.connect() as conn:
            conn.execute('DROP SCHEMA IF EXISTS jobwatch CASCADE')


def actor(conn, name='agent:postgres-tests'):
    conn.execute("SELECT set_config('jobwatch.actor', %s, true)", (name,))


def source(conn, board='test', provider='greenhouse', employer_id=None):
    return conn.execute('INSERT INTO jobwatch.sources(provider,board,employer_id) VALUES (%s,%s,%s) RETURNING id',
                        (provider, board, employer_id)).fetchone()['id']


def run(conn, source_id):
    return conn.execute("INSERT INTO jobwatch.collection_runs(id,source_id,mode,request_version,processing_revision) "
                        "VALUES (%s,%s,'publish','fixture-v1','pipeline-v1') RETURNING *", (uuid4(), source_id)).fetchone()


def version(conn, key, source_id, run_id, *, evidence=None, vid=None, normalizer='v1', description=None, title=None, url=None):
    from psycopg.types.json import Jsonb
    raw = {'id': key.rsplit(':', 1)[1], 'description': 'Build Python services'} if evidence is None else evidence
    text = raw.get('content', raw.get('descriptionPlain', raw.get('description', ''))) if description is None else description
    snapshot = {'company': 'Test', 'title': title or raw.get('title', 'Graduate Software Engineer'),
        'posting_url': url or f'https://example.org/jobs/{key.rsplit(":",1)[1]}',
        'location_text': 'London', 'country': '', 'countries': [], 'department': '', 'team': '',
        'employment_type': '', 'salary': '', 'published_at': '', 'source_dates': {}, 'deadlines': [],
        'description_text': text.replace('\0', '\ufffd'), 'extra_fields': {}}
    vid = vid or uuid4()
    columns = ['id', 'posting_key', 'source_id', 'origin', 'evidence', 'first_run_id', 'observed_at',
               'normalizer_version', 'normalized_sha256', 'nul_replaced'] + list(snapshot)
    values = [vid, key, source_id, 'collector', encode_evidence(raw), run_id, '2026-10-23T12:00:00Z',
              normalizer, sha256(encode_evidence(snapshot)).hexdigest(), '\0' in text]
    values.extend(Jsonb(v) if isinstance(v, dict) or k == 'deadlines' else v for k, v in snapshot.items())
    conn.execute('INSERT INTO jobwatch.posting_versions (' + ','.join(columns) + ') VALUES (' +
                 ','.join(['%s'] * len(values)) + ')', values)
    return vid


def posting(conn, source_id, run_id, native_id='1', provider='greenhouse', board='test', **kwargs):
    key = f'{provider}:{board.lower()}:{native_id}'
    vid = uuid4()
    conn.execute("INSERT INTO jobwatch.postings(posting_key,source_id,provider,board,native_id,status,first_seen_at,"
        "first_seen_basis,last_seen_at,current_version_id,verdict,family,codes,rules_version,screening_basis,screened_at) "
        "VALUES (%s,%s,%s,%s,%s,'listed',now(),'observed',now(),%s,'review','software','{}','rules-v1','full',now())",
        (key, source_id, provider, board, native_id, vid))
    version(conn, key, source_id, run_id, vid=vid, **kwargs)
    return key, vid


@unittest.skipUnless(ENABLED, 'Run scripts/test_postgres.sh for disposable PostgreSQL integration tests')
class DatabaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.target = Target.verified()
        cls.target.reset_schema()
        with cls.target.connect() as conn:
            migrate(conn)

    def setUp(self):
        with self.target.connect() as conn, conn.transaction():
            actor(conn)
            conn.execute('TRUNCATE ' + ','.join('jobwatch.' + t for t in DOMAIN_TABLES) + ' RESTART IDENTITY CASCADE')
            self.employer = conn.execute("INSERT INTO jobwatch.employers(slug,name) VALUES ('test','Test') RETURNING id").fetchone()['id']
            self.source = source(conn, employer_id=self.employer)

    def seed(self, **kwargs):
        with self.target.connect('jobwatch_collector') as conn, conn.transaction():
            actor(conn)
            attempt = run(conn, self.source)
            key, vid = posting(conn, self.source, attempt['id'], **kwargs)
        return key, vid, attempt

    def scalar(self, statement, params=None):
        with self.target.connect() as conn:
            return next(iter(conn.execute(statement, params).fetchone().values()))

    def rejected(self, error, statement, user='jobwatch_migrator', params=None):
        with self.target.connect(user) as conn:
            with self.assertRaises(error):
                with conn.transaction():
                    actor(conn)
                    conn.execute(statement, params)
