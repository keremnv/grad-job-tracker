"""Actual production publisher, mocked provider responses, disposable PostgreSQL only."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import subprocess
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

try:
    import psycopg
except ImportError:
    psycopg = None

from jobwatch import filtering
from jobwatch.postgres.collection import Processing, fetch, normalize_v1
from jobwatch.postgres.publisher import Collector, CollectionError, _version
from support import DatabaseTest, actor, source


def raw(native_id='1', title='Graduate Software Engineer', description='Build Python services', **extras):
    return {'id': native_id, 'internal_job_id': 12, 'title': title,
            'absolute_url': f'https://job-boards.greenhouse.io/test/jobs/{native_id}',
            'location': {'name': 'London, UK'}, 'content': description, **extras}


class PublisherTests(DatabaseTest):
    def setUp(self):
        super().setUp()
        with self.target.connect('jobwatch_owner') as conn, conn.transaction():
            actor(conn)
            conn.execute("UPDATE jobwatch.sources SET status='monitored',validation_url='https://example.org',endpoint_checked_at=now(),"
                "mapping_verified_at=now(),mapping_verified_by='test',mapping_evidence='official page' WHERE id=%s", (self.source,))
        self.client = Collector(self.target.config('jobwatch_collector'), actor='collector:tests')

    def batch(self, rows=None, *, client=None, request=None, receipt=None, advance_from=None):
        client = client or self.client
        registration = client.register(self.source, request_version=request, advance_from=advance_from)
        response = {'jobs': [raw()] if rows is None else rows}
        with self.target.connect() as conn:
            provider = conn.execute('SELECT provider FROM jobwatch.sources WHERE id=%s', (self.source,)).fetchone()['provider']
        if provider == 'greenhouse':
            response['meta'] = {'total': len(response['jobs'])}
        else:
            response['apiVersion'] = '1'
        return fetch(registration, client.processing, get_json=lambda _: response,
                     clock=(lambda: receipt) if receipt else (lambda: datetime.now(timezone.utc)))

    def publish(self, rows=None, **kwargs):
        batch = self.batch(rows, **kwargs)
        self.assertEqual((kwargs.get('client') or self.client).publish(batch), 'published')
        return batch

    def events(self, category):
        with self.target.connect() as conn:
            return conn.execute('SELECT * FROM jobwatch.posting_events WHERE category=%s ORDER BY id', (category,)).fetchall()

    def test_initial_excluded_unchanged_and_new_identity(self):
        first = self.publish([raw(), raw('2', title='Senior Accountant')])
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.postings'), 2)
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.postings WHERE verdict='excluded'"), 1)
        self.assertEqual({r['kind'] for r in self.events('observation')}, {'backfill'})
        self.assertEqual({r['actor'] for r in self.events('observation')}, {'collector:tests'})
        self.assertEqual(self.scalar('SELECT actor FROM jobwatch.collection_runs'), 'collector:tests')
        initial_time = self.scalar("SELECT first_seen_at FROM jobwatch.postings WHERE native_id='1'")
        again = self.publish([raw(), raw('2', title='Senior Accountant')])
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.posting_versions'), 2)
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.posting_events'), 2)
        self.assertEqual(self.scalar("SELECT first_seen_at FROM jobwatch.postings WHERE native_id='1'"), initial_time)
        self.assertEqual(self.scalar("SELECT last_seen_run_id FROM jobwatch.postings WHERE native_id='1'"), again.run.id)
        self.publish([raw(), raw('2', title='Senior Accountant'), raw('3')])
        self.assertEqual(self.events('observation')[-1]['kind'], 'first_seen')

    def test_last_positive_run_fk_and_registration_metadata_privileges(self):
        self.publish()
        with self.target.connect() as conn, conn.transaction():
            actor(conn)
            other_source = source(conn, 'other', employer_id=self.employer)
        other_run = self.client.register(other_source)
        with self.target.connect('jobwatch_collector') as conn:
            with self.assertRaises(psycopg.errors.ForeignKeyViolation), conn.transaction():
                conn.execute('UPDATE jobwatch.postings SET last_seen_run_id=%s', (other_run.id,))
            with self.assertRaises(psycopg.errors.InsufficientPrivilege), conn.transaction():
                conn.execute("UPDATE jobwatch.collection_runs SET details='{}' WHERE id=%s", (other_run.id,))
        self.assertEqual(self.scalar("SELECT status FROM jobwatch.postings"), 'listed')

    def test_three_consecutive_material_changes_and_native_retention(self):
        self.publish([raw(description='First wording')])
        for text in ('Second wording', 'Third wording', 'Fourth wording'):
            self.publish([raw(description=text)])
        events = self.events('provider')
        self.assertEqual(len(events), 3)
        with self.target.connect('jobwatch_owner') as conn:
            for e in events:
                self.assertEqual(e['kind'], 'changed')
                self.assertIn('description', e['changes']['changed'])
                evidence = conn.execute('SELECT evidence FROM jobwatch.posting_versions WHERE id=ANY(%s::uuid[])',
                    ([e['before_version_id'], e['after_version_id']],)).fetchall()
                self.assertEqual(len(evidence), 2)
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.posting_versions'), 4)

    def test_formatting_timestamp_only_and_deadline_observation_stability(self):
        first = self.publish([raw(description='<p>Build Python</p>', application_deadline='2026-11-01', updated_at='a')])
        old = self.scalar('SELECT deadlines FROM jobwatch.posting_versions')
        self.publish([raw(description='<p style="margin-top:0px">Build Python</p><p>&nbsp;</p>', application_deadline='2026-11-01', updated_at='b')])
        self.assertEqual(self.events('provider'), [])
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.posting_versions'), 2)
        self.assertEqual(self.scalar('SELECT deadlines FROM jobwatch.research_postings'), old)

    def test_actual_incident_and_cambridge_publication(self):
        pairs = json.loads(Path(__file__).with_name('fixtures').joinpath('source_pairs.json').read_text())
        for pair in pairs['ashby'] + [pairs['greenhouse']]:
            provider, board, native_id = pair['key'].split(':')
            with self.target.connect() as conn, conn.transaction():
                actor(conn)
                existing = conn.execute('SELECT id FROM jobwatch.sources WHERE provider=%s AND board=%s', (provider, board)).fetchone()
                sid = existing['id'] if existing else source(conn, board, provider, self.employer)
                conn.execute("UPDATE jobwatch.sources SET region=%s,status='monitored',validation_url='https://example.org',endpoint_checked_at=now(),"
                    "mapping_verified_at=now(),mapping_verified_by='test',mapping_evidence='official page' WHERE id=%s", ('eu' if provider == 'greenhouse' else 'global', sid))
            # The second incident pair has the same source: use the first source rather than insert duplicates.
            responses = []
            for record in (pair['before'], pair['after']):
                envelope = {'jobs': [record], **({'apiVersion': '1'} if provider == 'ashby' else {'meta': {'total': 1}})}
                rid, status = self.client.collect_source(sid, get_json=lambda _: envelope)
                self.assertEqual(status, 'published')
            with self.target.connect() as conn:
                events = conn.execute("SELECT * FROM jobwatch.posting_events WHERE posting_key=%s AND category='provider'", (pair['key'],)).fetchall()
            if provider == 'ashby':
                self.assertEqual(events, [])
            else:
                self.assertEqual(len(events), 1)
                self.assertEqual(set(events[0]['changes']['changed']), {'metadata'})

    def test_nul_replacement_and_screening_reproducibility(self):
        native = raw(description='Build\0Python\r\nservices')
        self.publish([native])
        with self.target.connect() as conn:
            row = conn.execute('SELECT * FROM jobwatch.research_postings').fetchone()
            evidence = conn.execute('SELECT evidence,nul_replaced FROM jobwatch.posting_versions').fetchone()
        self.assertEqual(json.loads(bytes(evidence['evidence'])), native)
        self.assertEqual(row['description_text'], 'Build\ufffdPython\r\nservices')
        self.assertTrue(evidence['nul_replaced'])
        self.assertEqual(row['screening_input_sha256'], filtering.input_fingerprint({'title': row['title'], 'location': row['location_text'],
            'country': row['country'], 'countries': row['countries'], 'department': row['department'], 'team': row['team'],
            'employment_type': row['employment_type'], 'description': row['description_text']}))

    def test_new_field_baseline_and_latest_request_not_first_capture(self):
        self.publish([raw()], request='contract-a')
        self.publish([raw(metadata=None)], request='contract-b')
        self.assertEqual(self.events('provider')[-1]['kind'], 'baseline')
        self.publish([raw()], request='contract-a')
        count = len(self.events('provider'))
        self.publish([raw()], request='contract-b')  # reuses original version, latest positive request is now B
        self.publish([raw(metadata=None)], request='contract-b')
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.posting_versions'), 2)
        self.assertGreater(len(self.events('provider')), count)

    def test_empty_absence_reappearance_and_frozen_last_seen(self):
        first = self.publish()
        last_seen = self.scalar('SELECT last_seen_at FROM jobwatch.postings')
        self.publish([])
        self.assertEqual(self.scalar('SELECT status FROM jobwatch.postings'), 'not_seen')
        self.assertEqual(self.scalar('SELECT last_seen_at FROM jobwatch.postings'), last_seen)
        self.assertEqual(self.scalar('SELECT last_seen_run_id FROM jobwatch.postings'), first.run.id)
        self.publish()
        self.assertEqual(self.scalar('SELECT status FROM jobwatch.postings'), 'listed')
        self.assertIsNone(self.scalar('SELECT missing_since FROM jobwatch.postings'))
        self.assertEqual([r['kind'] for r in self.events('observation')], ['backfill', 'not_seen', 'reappeared'])

    def test_failed_malformed_partial_and_no_transaction_during_network(self):
        first = self.publish()
        cases = [TimeoutError('untrusted password=secret'), {'jobs': [raw(), {'id': 'malformed'}], 'meta': {'total': 2}},
                 {'jobs': [], 'meta': {'total': 9}}, {'jobs': [raw()], 'meta': {'total': 1}, 'hasMore': True}]
        for response in cases:
            def get(_):
                with self.target.connect('postgres') as observer:
                    self.assertEqual(observer.execute("SELECT count(*) AS n FROM pg_stat_activity WHERE application_name='jobwatch-runtime' "
                        "AND state='idle in transaction'").fetchone()['n'], 0)
                if isinstance(response, Exception):
                    raise response
                return response
            with self.assertRaises(CollectionError) as failure:
                self.client.collect_source(self.source, get_json=get)
            self.assertEqual(failure.exception.outcome, 'failed')
            self.assertNotIn('secret', str(failure.exception))
        self.assertEqual(self.scalar('SELECT last_published_run_id FROM jobwatch.sources'), first.run.id)
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.postings WHERE status='listed'"), 1)
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.posting_events WHERE kind='not_seen'"), 0)

    def test_greenhouse_without_total_is_positive_only(self):
        self.publish([raw(), raw('2')])
        self.client.collect_source(self.source, get_json=lambda _: {'jobs': [raw()]})
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.postings WHERE status='listed'"), 2)
        self.assertFalse(self.scalar('SELECT absence_safe FROM jobwatch.source_health'))

    def test_unchanged_verdict_updates_input_and_evaluation_time(self):
        self.publish()
        before = self.scalar('SELECT screened_at FROM jobwatch.postings')
        old_input = self.scalar('SELECT screening_input_sha256 FROM jobwatch.postings')
        self.publish([raw(description='Build Python services differently')])
        self.assertGreater(self.scalar('SELECT screened_at FROM jobwatch.postings'), before)
        self.assertNotEqual(self.scalar('SELECT screening_input_sha256 FROM jobwatch.postings'), old_input)
        self.assertEqual(self.events('screening'), [])

    def test_rules_rescreen_and_processing_downgrade_prevention(self):
        self.publish()
        pending = self.batch()
        def screen(inputs):
            return {**filtering.screen(inputs), 'rules_version': 'test-rules-v2', 'verdict': 'excluded'}
        new = Collector(self.client.config, actor='operator:tests', processing=Processing(revision='pipeline-v2', rules_version='test-rules-v2', screen=screen))
        self.assertEqual(new.rescreen_source(self.source, advance_from='pipeline-v1'), 'rescreened')
        self.assertEqual(self.scalar('SELECT verdict FROM jobwatch.postings'), 'excluded')
        self.assertEqual(len(self.events('screening')), 1)
        self.assertTrue(self.events('screening')[0]['changes']['cause']['rules_changed'])
        self.assertEqual(self.client.publish(pending), 'superseded')
        self.assertEqual(self.client.rescreen_source(self.source, advance_from='pipeline-v1'), 'superseded')

    def test_normalizer_revision_is_immutable_and_not_provider_change(self):
        first = self.publish()
        def normalize(p, at):
            return {**normalize_v1(p, at), 'description_text': p.description + ' [test representation]'}
        new = Collector(self.client.config, actor='operator:tests', processing=Processing(revision='pipeline-v2', normalizer_version='test-normalizer-v2', normalize=normalize))
        new.rescreen_source(self.source, advance_from='pipeline-v1')
        self.assertEqual(self.events('provider'), [])
        self.assertEqual(len(self.events('normalizer')), 1)
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.posting_versions'), 2)
        self.assertEqual(self.scalar("SELECT description_text FROM jobwatch.posting_versions WHERE normalizer_version='normalizer-v1'"), 'Build Python services')
        self.assertEqual(new.rescreen_source(self.source, advance_from='pipeline-v1'), 'rescreened')
        self.assertEqual(len(self.events('normalizer')), 1)

    def test_out_of_order_replay_and_changed_payload_refusal(self):
        older = self.batch([raw(description='Older')])
        newer = self.batch([raw(description='Newer')])
        self.assertEqual(self.client.publish(newer), 'published')
        self.assertEqual(self.client.publish(older), 'superseded')
        self.assertEqual(self.client.publish(newer), 'published')
        self.assertEqual(self.scalar('SELECT description_text FROM jobwatch.research_postings'), 'Newer')
        with self.assertRaises(CollectionError):
            self.client.publish(replace(newer, fetched_at=datetime(2030, 1, 1, tzinfo=timezone.utc)))
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.posting_events'), 1)

    def test_halfway_failure_rolls_back_every_corpus_change_and_records_failure(self):
        first = self.publish()
        batch = self.batch([raw(description='changed'), raw('2')])
        count = 0
        def fail(*args, **kwargs):
            nonlocal count
            count += 1
            result = _version(*args, **kwargs)
            if count == 2:
                args[0].execute('SELECT 1/0')
            return result
        with patch('jobwatch.postgres.publisher._version', side_effect=fail), self.assertRaises(CollectionError) as failure:
            self.client.publish(batch)
        self.assertEqual(failure.exception.outcome, 'failed')
        self.assertEqual(failure.exception.sqlstate, '22012')
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.postings'), 1)
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.posting_versions'), 1)
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.posting_events'), 1)
        self.assertEqual(self.scalar('SELECT last_published_run_id FROM jobwatch.sources'), first.run.id)
        self.assertEqual(self.scalar('SELECT status FROM jobwatch.collection_runs WHERE id=%s', (batch.run.id,)), 'failed')

    def test_lost_registration_ack_and_lost_publication_ack(self):
        original = self.client._register_once
        calls = 0
        def register(*args):
            nonlocal calls
            result = original(*args); calls += 1
            if calls == 1:
                raise psycopg.OperationalError('simulated lost acknowledgement')
            return result
        rid = uuid4()
        with patch.object(self.client, '_register_once', side_effect=register):
            registration = self.client.register(self.source, run_id=rid)
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.collection_runs'), 1)
        observation = fetch(registration, get_json=lambda _: {'jobs': [raw()], 'meta': {'total': 1}})
        original_publish = self.client._publish_once
        calls = 0
        def publish(batch):
            nonlocal calls
            result = original_publish(batch); calls += 1
            if calls == 1:
                raise psycopg.OperationalError('simulated lost COMMIT acknowledgement')
            return result
        with patch.object(self.client, '_publish_once', side_effect=publish):
            self.assertEqual(self.client.publish(observation), 'published')
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.posting_events'), 1)
        self.assertEqual(self.scalar('SELECT fetched_at FROM jobwatch.collection_runs'), observation.fetched_at)

    def test_deadlock_and_serialization_retries_reuse_payload(self):
        observation = self.batch()
        original = self.client._publish_once
        errors = [psycopg.errors.DeadlockDetected(), psycopg.errors.SerializationFailure()]
        def publish(batch):
            self.assertIs(batch, observation)
            if errors:
                raise errors.pop()
            return original(batch)
        with patch.object(self.client, '_publish_once', side_effect=publish):
            self.assertEqual(self.client.publish(observation), 'published')
        self.assertEqual(self.scalar('SELECT fetched_at FROM jobwatch.collection_runs'), observation.fetched_at)

    def test_owner_records_unchanged_and_collector_cannot_read_them(self):
        self.publish()
        with self.target.connect('jobwatch_owner') as conn, conn.transaction():
            actor(conn)
            conn.execute("INSERT INTO jobwatch.triage(posting_key,decision,note,recorded_by) VALUES ('greenhouse:test:1','later','private fixture','owner')")
            conn.execute("INSERT INTO jobwatch.applications(job_key,posting_key,company,role,url,notes) VALUES ('manual:stable','greenhouse:test:1','Test','Grad','https://example.org/app','private fixture')")
            conn.execute("INSERT INTO jobwatch.leads(lead_key,kind,company,title,state,url,official,source_note,checked_at,checked_by) "
                "VALUES ('vacancy:private-test','vacancy','Test','Grad','unverified','https://example.org/lead',true,'fixture',now(),'agent')")
            conn.execute("INSERT INTO jobwatch.verifications(posting_key,kind,checked_at,checked_by,url,wording) "
                "VALUES ('greenhouse:test:1','deadline',now(),'agent','https://example.org/deadline','Applications close in November')")
            before = conn.execute('SELECT * FROM jobwatch.owner_audit ORDER BY id').fetchall()
            verifications = conn.execute('SELECT * FROM jobwatch.verifications').fetchall()
            leads = conn.execute('SELECT * FROM jobwatch.leads').fetchall()
        self.publish([raw(description='Updated source')])
        self.client.rescreen_all()
        self.assertEqual(self.scalar('SELECT note FROM jobwatch.triage'), 'private fixture')
        self.assertEqual(self.scalar('SELECT notes FROM jobwatch.applications'), 'private fixture')
        with self.target.connect() as conn:
            self.assertEqual(conn.execute('SELECT * FROM jobwatch.owner_audit ORDER BY id').fetchall(), before)
            self.assertEqual(conn.execute('SELECT * FROM jobwatch.verifications').fetchall(), verifications)
            self.assertEqual(conn.execute('SELECT * FROM jobwatch.leads').fetchall(), leads)

    def test_multi_page_lever_positive_only_and_valid_empty_first_page(self):
        with self.target.connect() as conn, conn.transaction():
            actor(conn)
            sid = source(conn, 'lever-test', 'lever', self.employer)
            conn.execute("UPDATE jobwatch.sources SET status='monitored',validation_url='https://example.org',endpoint_checked_at=now(),"
                "mapping_verified_at=now(),mapping_verified_by='test',mapping_evidence='official page' WHERE id=%s", (sid,))
        rows = [{'id': str(i), 'text': 'Graduate Software Engineer', 'hostedUrl': f'https://jobs.lever.co/lever-test/{i}',
                 'descriptionPlain': 'Build Python', 'country': 'GB', 'categories': {'location': 'London'}} for i in range(101)]
        def get(data):
            from urllib.parse import urlsplit, parse_qs
            def response(url):
                skip = int(parse_qs(urlsplit(url).query)['skip'][0])
                return data[skip:skip+100]
            return response
        first, _ = self.client.collect_source(sid, get_json=get(rows), pause=lambda _: None)
        second, _ = self.client.collect_source(sid, get_json=get(rows[:100]), pause=lambda _: None)
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.postings WHERE status='listed'"), 101)
        self.assertEqual(self.scalar("SELECT last_seen_run_id FROM jobwatch.postings WHERE native_id='100'"), first)
        self.assertFalse(self.scalar('SELECT absence_safe FROM jobwatch.collection_runs WHERE id=%s', (second,)))
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.posting_events WHERE kind='not_seen'"), 0)
        self.client.collect_source(sid, get_json=lambda _: [], pause=lambda _: None)
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.postings WHERE status='not_seen'"), 101)

    def wait_for_publishers(self, n):
        deadline = time.monotonic() + 3
        with self.target.connect('postgres') as conn:
            while time.monotonic() < deadline:
                count = conn.execute("SELECT count(*) AS n FROM pg_stat_activity WHERE datname=current_database() AND wait_event_type='Lock' "
                    "AND query LIKE 'SELECT * FROM jobwatch.sources%'").fetchone()['n']
                if count >= n:
                    return
                time.sleep(.01)
        self.fail('Expected source-lock wait did not occur')

    def test_two_concurrent_actual_publishers(self):
        older = self.batch([raw(description='Older observation')])
        newer = self.batch([raw(description='Newer observation')])
        with ThreadPoolExecutor(2) as pool, self.target.connect() as conn:
            with conn.transaction():
                conn.execute('SELECT id FROM jobwatch.sources WHERE id=%s FOR NO KEY UPDATE', (self.source,))
                a = pool.submit(self.client.publish, older)
                self.wait_for_publishers(1)
                b = pool.submit(self.client.publish, newer)
                self.wait_for_publishers(2)
            self.assertIn(a.result(5), ('published', 'superseded'))
            self.assertEqual(b.result(5), 'published')
        self.assertEqual(self.scalar('SELECT last_published_run_id FROM jobwatch.sources'), newer.run.id)
        self.assertEqual(self.scalar('SELECT description_text FROM jobwatch.research_postings'), 'Newer observation')

    def test_concurrent_rescreen_and_publication_cannot_downgrade(self):
        self.publish()
        pending = self.batch([raw(description='Pending old worker')])
        def judge(i):
            return {**filtering.screen(i), 'rules_version': 'test-rules-v2', 'verdict': 'excluded'}
        new = Collector(self.client.config, actor='operator:test', processing=Processing(revision='pipeline-v2', rules_version='test-rules-v2', screen=judge))
        with ThreadPoolExecutor(2) as pool, self.target.connect() as conn:
            with conn.transaction():
                conn.execute('SELECT id FROM jobwatch.sources WHERE id=%s FOR NO KEY UPDATE', (self.source,))
                rescreen = pool.submit(new.rescreen_source, self.source, advance_from='pipeline-v1')
                self.wait_for_publishers(1)
                publication = pool.submit(self.client.publish, pending)
                self.wait_for_publishers(2)
            self.assertEqual(rescreen.result(5), 'rescreened')
            self.assertIn(publication.result(5), ('published', 'superseded'))
        self.assertEqual(self.scalar('SELECT rules_version FROM jobwatch.postings'), 'test-rules-v2')
        self.assertEqual(self.scalar('SELECT processing_revision FROM jobwatch.sources'), 'pipeline-v2')

    def test_atomic_combined_normalizer_source_transition(self):
        self.publish([raw(description='Original wording')])
        stale = self.batch()
        def normalize(p, at):
            return {**normalize_v1(p, at), 'description_text': p.description + ' [test representation]'}
        new = Collector(self.client.config, actor='operator:test', processing=Processing(revision='pipeline-v2', normalizer_version='test-normalizer-v2', normalize=normalize))
        self.publish([raw(description='Changed wording')], client=new, advance_from='pipeline-v1')
        events = self.events('normalizer') + self.events('provider')
        events.sort(key=lambda e: e['id'])
        self.assertEqual([e['category'] for e in events], ['normalizer', 'provider'])
        self.assertEqual(events[0]['after_version_id'], events[1]['before_version_id'])
        self.assertEqual(self.scalar('SELECT processing_revision FROM jobwatch.sources'), 'pipeline-v2')
        self.assertEqual(self.client.publish(stale), 'superseded')
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.posting_versions'), 3)

    def test_rules_metadata_refresh_without_judgment_event(self):
        self.publish()
        before = self.scalar('SELECT screened_at FROM jobwatch.postings')
        def judge(i):
            return {**filtering.screen(i), 'rules_version': 'test-rules-v2'}
        new = Collector(self.client.config, actor='operator:test', processing=Processing(revision='pipeline-v2', rules_version='test-rules-v2', screen=judge))
        new.rescreen_source(self.source, advance_from='pipeline-v1')
        self.assertEqual(self.events('screening'), [])
        self.assertEqual(self.scalar('SELECT rules_version FROM jobwatch.postings'), 'test-rules-v2')
        self.assertGreater(self.scalar('SELECT screened_at FROM jobwatch.postings'), before)

    def test_rescreen_failure_rolls_back_versions_events_and_revision(self):
        self.publish([raw(), raw('2')])
        def normalize(p, at):
            return {**normalize_v1(p, at), 'description_text': p.description + ' [test representation]'}
        new = Collector(self.client.config, actor='operator:test', processing=Processing(revision='pipeline-v2', normalizer_version='test-normalizer-v2', normalize=normalize))
        real = new._screen
        calls = 0
        def screen(conn, *args, **kwargs):
            nonlocal calls
            real(conn, *args, **kwargs); calls += 1
            if calls == 2:
                conn.execute('SELECT 1/0')
        with patch.object(new, '_screen', side_effect=screen), self.assertRaises(CollectionError):
            new.rescreen_source(self.source, advance_from='pipeline-v1')
        self.assertEqual(self.scalar('SELECT processing_revision FROM jobwatch.sources'), 'pipeline-v1')
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.posting_versions'), 2)
        self.assertEqual(self.events('normalizer'), [])

    def test_connection_failures_are_bounded_and_unknown_attempt_is_resumable(self):
        from jobwatch.postgres.connection import DatabaseError
        observation = self.batch()
        with patch.object(self.client, '_connection', side_effect=DatabaseError('credentials must never escape')) as opener:
            with self.assertRaises(CollectionError) as failure:
                self.client.publish(observation)
            self.assertEqual(opener.call_count, 6)  # three publication attempts, three health/outcome checks
        self.assertEqual(failure.exception.outcome, 'unknown')
        self.assertNotIn('credentials', str(failure.exception))
        self.assertEqual(self.scalar('SELECT status FROM jobwatch.collection_runs'), 'running')
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.postings'), 0)
        self.assertEqual(self.client.publish(observation), 'published')

    def test_registration_mismatch_pause_and_no_refetch(self):
        observation = self.batch()
        with self.assertRaises(CollectionError):
            self.client.register(self.source, run_id=observation.run.id, request_version='wrong-contract')
        with self.target.connect('jobwatch_owner') as conn, conn.transaction():
            actor(conn)
            conn.execute("UPDATE jobwatch.sources SET status='paused' WHERE id=%s", (self.source,))
        self.assertEqual(self.client.publish(observation), 'skipped')
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.postings'), 0)
        def unexpected(_):
            self.fail('Paused source must not be fetched')
        self.assertEqual(self.client.collect_source(self.source, get_json=unexpected)[1], 'skipped')

    def test_all_publication_acknowledgements_lost_but_health_confirms_commit(self):
        observation = self.batch()
        original = self.client._publish_once
        calls = 0
        def lost(batch):
            nonlocal calls
            original(batch)
            calls += 1
            raise psycopg.OperationalError('lost acknowledgement')
        with patch.object(self.client, '_publish_once', side_effect=lost):
            self.assertEqual(self.client.publish(observation), 'published')
        self.assertEqual(calls, 3)
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.posting_events'), 1)

    def test_ashby_version_contract_and_partial_response(self):
        with self.target.connect() as conn, conn.transaction():
            actor(conn)
            sid = source(conn, 'ashby-test', 'ashby', self.employer)
            conn.execute("UPDATE jobwatch.sources SET status='monitored',validation_url='https://example.org',endpoint_checked_at=now(),"
                "mapping_verified_at=now(),mapping_verified_by='test',mapping_evidence='official page' WHERE id=%s", (sid,))
        job = {'id': '1', 'title': 'Graduate Software Engineer', 'jobUrl': 'https://jobs.ashbyhq.com/ashby-test/1', 'descriptionPlain': 'Python'}
        self.client.collect_source(sid, get_json=lambda _: {'apiVersion': '1', 'jobs': [job]})
        self.client.collect_source(sid, get_json=lambda _: {'jobs': []})  # valid legacy shape; insufficient completeness proof
        self.assertEqual(self.scalar('SELECT status FROM jobwatch.postings'), 'listed')
        with self.assertRaises(CollectionError):
            self.client.collect_source(sid, get_json=lambda _: {'apiVersion': '1', 'jobs': [], 'nextCursor': 'more'})
        self.assertEqual(self.scalar('SELECT status FROM jobwatch.postings'), 'listed')
        self.client.collect_source(sid, get_json=lambda _: {'apiVersion': '1', 'jobs': []})
        self.assertEqual(self.scalar('SELECT status FROM jobwatch.postings'), 'not_seen')

    def test_cli_fixture_uses_actual_publisher_and_rescreens(self):
        with TemporaryDirectory() as temp:
            fixture = Path(temp) / 'feed.json'
            fixture.write_text(json.dumps({'jobs': [raw()], 'meta': {'total': 1}}))
            env = {**os.environ, 'JOBWATCH_DATABASE_URL': self.client.config.dsn}
            command = [sys.executable, '-m', 'jobwatch.postgres.scan', '--actor', 'collector:cli-test']
            result = subprocess.run(command + ['collect', str(self.source), '--fixture', str(fixture)], env=env,
                                    text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], 'published')
            result = subprocess.run(command + ['rescreen', '--all'], env=env, text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'], {str(self.source): 'rescreened'})

    def test_v1_database_parity_for_all_providers(self):
        from jobwatch import feeds, state
        from jobwatch.postgres.collection import iso
        fixtures = Path(__file__).parent.parent / 'fixtures'
        for provider, board in (('greenhouse', 'monzo'), ('lever', 'palantir'), ('ashby', 'incident')):
            response = json.loads((fixtures / (provider + '.json')).read_text())
            with self.target.connect() as conn, conn.transaction():
                actor(conn)
                sid = source(conn, board, provider, self.employer)
                conn.execute("UPDATE jobwatch.sources SET status='monitored',validation_url='https://example.org',endpoint_checked_at=now(),"
                    "mapping_verified_at=now(),mapping_verified_by='test',mapping_evidence='official page' WHERE id=%s", (sid,))
            rid, outcome = self.client.collect_source(sid, get_json=lambda _: response, pause=lambda _: None)
            self.assertEqual(outcome, 'published')
            expected = feeds.collect_board({'provider': provider, 'board': board, 'company': 'Test'}, get_json=lambda _: response)
            with self.target.connect() as conn:
                receipt = conn.execute('SELECT fetched_at FROM jobwatch.collection_runs WHERE id=%s', (rid,)).fetchone()['fetched_at']
                for p in expected:
                    actual = conn.execute('SELECT r.*,v.published_at FROM jobwatch.research_postings r JOIN jobwatch.posting_versions v '
                        'ON v.id=r.current_version_id WHERE r.posting_key=%s', (p.key,)).fetchone()
                    legacy = state._source(p, state.text_hash(p.description), iso(receipt))
                    for field, oldfield in (('company','company'),('title','title'),('posting_url','url'),('location_text','location'),
                            ('country','country'),('countries','countries'),('department','department'),('team','team'),
                            ('employment_type','employment_type'),('salary','salary'),('published_at','published_at'),('source_dates','source_dates'),('deadlines','deadlines')):
                        self.assertEqual(actual[field], legacy[oldfield], (provider, field))
                    self.assertEqual(actual['description_text'], p.description)
                    judgment = filtering.screen(filtering.inputs_from_posting(p))
                    for field, oldfield in (('verdict','verdict'),('family','family'),('codes','codes'),('rules_version','rules_version'),
                            ('screening_input_sha256','input_sha256'),('screening_basis','basis')):
                        self.assertEqual(actual[field], judgment[oldfield], (provider, field))
