"""Riverlane URL compatibility through actual persistence; synthetic responses only."""
from copy import deepcopy

from jobwatch.postgres.endpoints import check_endpoint
from jobwatch.postgres.publisher import Collector, CollectionError
from support import DatabaseTest, actor


def response(*ids, legacy=False):
    return {'jobs': [
        {'id': native_id, 'internal_job_id': 99, 'title': 'Graduate Software Engineer',
         'absolute_url': (f'https://job-boards.eu.greenhouse.io/riverlane/jobs/{native_id}'
                          if legacy else f'https://www.riverlane.com/jobs?gh_jid={native_id}#vacancies'),
         'location': {'name': 'Cambridge'}, 'content': 'Build synthetic systems'}
        for native_id in ids], 'meta': {'total': len(ids)}}


class RiverlaneTests(DatabaseTest):
    def setUp(self):
        super().setUp()
        with self.target.connect() as conn, conn.transaction():
            actor(conn)
            self.sid = conn.execute(
                "INSERT INTO jobwatch.sources(provider,board,region,employer_id) "
                "VALUES ('greenhouse','riverlane','eu',%s) RETURNING id",
                (self.employer,)).fetchone()['id']
        self.client = Collector(self.target.config('jobwatch_collector'), actor='collector:synthetic-riverlane-test')

    def monitored_fixture(self):
        # Synthetic test setup, never a live endpoint check or production activation.
        with self.target.connect('jobwatch_owner') as conn, conn.transaction():
            actor(conn)
            conn.execute("UPDATE jobwatch.sources SET status='monitored',validation_url='https://example.org',"
                         "endpoint_checked_at=now(),mapping_verified_at=now(),mapping_verified_by='test',"
                         "mapping_evidence='synthetic fixture only' WHERE id=%s", (self.sid,))

    def publish(self, data):
        return self.client.collect_source(self.sid, get_json=lambda _: data)

    def corpus(self):
        with self.target.connect() as conn:
            # Failed attempts may update run/health records, never this published corpus or owner data.
            return {name: conn.execute('SELECT * FROM jobwatch.' + name + ' ORDER BY 1').fetchall()
                    for name in ('postings', 'posting_versions', 'posting_events', 'applications', 'owner_audit')} | {
                'pointer': conn.execute('SELECT last_published_run_id FROM jobwatch.sources WHERE id=%s',
                                        (self.sid,)).fetchone()}

    def test_legacy_and_custom_urls_preserve_identity_evidence_and_application_link(self):
        self.monitored_fixture()
        old = response(17, legacy=True)
        self.publish(old)
        with self.target.connect('jobwatch_owner') as conn, conn.transaction():
            actor(conn)
            conn.execute("INSERT INTO jobwatch.applications(job_key,posting_key,company,role,url) "
                         "VALUES ('manual:synthetic-riverlane','greenhouse:riverlane:17','Synthetic','Engineer',%s)",
                         (old['jobs'][0]['absolute_url'],))
        current = response(17)
        self.publish(current)
        with self.target.connect() as conn:
            rows = conn.execute('SELECT p.posting_key,v.posting_url FROM jobwatch.postings p '
                                'JOIN jobwatch.posting_versions v ON v.id=p.current_version_id').fetchall()
            self.assertEqual(rows, [{'posting_key': 'greenhouse:riverlane:17',
                                     'posting_url': current['jobs'][0]['absolute_url']}])
            versions = conn.execute('SELECT posting_url,evidence FROM jobwatch.posting_versions').fetchall()
            self.assertEqual({v['posting_url'] for v in versions},
                             {old['jobs'][0]['absolute_url'], current['jobs'][0]['absolute_url']})
            from jobwatch.postgres.evidence import encode_evidence
            self.assertEqual({bytes(v['evidence']) for v in versions},
                             {encode_evidence(old['jobs'][0]), encode_evidence(current['jobs'][0])})
            application = conn.execute('SELECT job_key,posting_key,url FROM jobwatch.applications').fetchone()
            self.assertEqual(application, {'job_key': 'manual:synthetic-riverlane',
                                            'posting_key': 'greenhouse:riverlane:17',
                                            'url': old['jobs'][0]['absolute_url']})

    def test_malformed_custom_url_never_partially_publishes_or_marks_absence(self):
        self.monitored_fixture()
        self.publish(response(17, 18))
        before = self.corpus()
        for query in ('gh_jid=20', 'gh_jid=%ZZ', 'gh_jid=19&gh_jid=19'):
            bad = response(17, 19)
            bad['jobs'][0]['content'] = 'This earlier valid row must not be partially published'
            bad['jobs'][1]['absolute_url'] = 'https://www.riverlane.com/jobs?' + query
            with self.subTest(query=query), self.assertRaises(CollectionError) as failure:
                self.publish(bad)
            self.assertEqual(failure.exception.outcome, 'failed')
            self.assertEqual(self.corpus(), before)
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.posting_events WHERE kind='not_seen'"), 0)
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.collection_runs WHERE status='failed'"), 3)

    def test_endpoint_receipt_validates_completeness_without_publication_or_activation(self):
        with self.target.connect() as conn:
            before = conn.execute('SELECT * FROM jobwatch.sources WHERE id=%s', (self.sid,)).fetchone()
        good = response(17)
        valid = check_endpoint(self.client, self.sid, get_json=lambda _: good, fixture=True)
        self.assertEqual(valid['status'], 'validated')
        self.assertTrue(valid['absence_safe'])
        self.assertFalse(valid['published'])
        positive_only = deepcopy(good)
        positive_only.pop('meta')
        check = check_endpoint(self.client, self.sid, get_json=lambda _: positive_only, fixture=True)
        self.assertEqual(check['status'], 'validated')
        self.assertFalse(check['absence_safe'])
        malformed = response(17)
        malformed['jobs'][0]['absolute_url'] += '&gh_jid=17'
        failed = check_endpoint(self.client, self.sid, get_json=lambda _: malformed, fixture=True)
        self.assertEqual(failed['status'], 'failed')
        self.assertFalse(failed['absence_safe'])
        with self.target.connect() as conn:
            self.assertEqual(conn.execute('SELECT * FROM jobwatch.sources WHERE id=%s', (self.sid,)).fetchone(), before)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM jobwatch.postings').fetchone()['n'], 0)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM jobwatch.posting_events').fetchone()['n'], 0)
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM jobwatch.collection_runs WHERE mode<>'validate'").fetchone()['n'], 0)
