"""Discovery-index invariants: completeness, failure safety, provenance separation, migration."""
import contextlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from jobwatch import state as st
from jobwatch.cli import scan
from jobwatch.feeds import Posting
from jobwatch.filtering import screen as real_screen

T1, T2, T3 = '2026-10-17T07:00:00Z', '2026-10-18T07:00:00Z', '2026-10-19T07:00:00Z'
ENTRY = {'company': 'Example', 'provider': 'lever', 'board': 'example', 'enabled': True,
         'careers_url': 'https://example.org/careers', 'validation_url': 'https://example.org/careers',
         'mapping_verified_at': '2026-10-17T00:00:00Z'}


def post(i, title='Graduate Software Engineer', description='Build software in Python.', **extra):
    return Posting('Example', 'lever', 'example', str(i), title, 'London, United Kingdom',
                   f'https://jobs.lever.co/example/{i}', description=description, country='GB',
                   countries=('GB',), **extra)


def board(n=100, relevant_every=5):
    return [post(i, 'Graduate Software Engineer' if i % relevant_every == 0 else 'Senior Accountant') for i in range(n)]


class Workspace:
    def __init__(self, root):
        self.root = Path(root)
        self.config = self.root / 'config.json'; self.state = self.root / 'jobs.json'; self.report = self.root / 'latest.md'
        self.config.write_text(json.dumps({'employers': [ENTRY]}))

    def scan(self, result):
        kwargs = {'side_effect': result} if isinstance(result, Exception) else {'return_value': result}
        with patch('jobwatch.cli.collect_board', **kwargs), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return scan(self.config, self.state, self.report)


class CompletenessTests(unittest.TestCase):
    def test_successful_board_accounts_for_every_identity_including_excluded(self):
        with TemporaryDirectory() as d:
            ws = Workspace(d)
            self.assertEqual(ws.scan(board()), 0)
            loaded = st.load_state(ws.state)
            self.assertEqual(len(loaded['postings']), 100)
            verdicts = [r['screening']['verdict'] for r in loaded['postings'].values()]
            self.assertEqual(verdicts.count('excluded'), 80)
            health = loaded['boards']['lever:example']
            self.assertEqual((health['count'], sum(health['screening'].values())), (100, 100))
            excluded = loaded['postings']['lever:example:1']
            self.assertEqual(excluded['screening']['codes'][0], 'exclude.senior_title')
            self.assertEqual(excluded['url'], 'https://jobs.lever.co/example/1')
            self.assertEqual(excluded['first_seen'], loaded['last_scan']['at'])
            self.assertIn('80 listed posting(s) were excluded by screening but are recorded', ws.report.read_text())

    def test_failed_board_after_success_produces_no_absence_and_no_rewrite(self):
        with TemporaryDirectory() as d:
            ws = Workspace(d)
            ws.scan(board(10))
            before = ws.state.read_bytes()
            self.assertEqual(ws.scan(TimeoutError('timeout')), 1)
            self.assertEqual(ws.state.read_bytes(), before)
            loaded = st.load_state(ws.state)
            self.assertTrue(all(r['feed_status'] == 'listed' for r in loaded['postings'].values()))
            self.assertEqual(loaded['boards']['lever:example']['status'], 'failed')

    def test_unchanged_scan_leaves_index_byte_identical(self):
        with TemporaryDirectory() as d:
            ws = Workspace(d)
            ws.scan(board(20))
            index, texts = ws.state.read_bytes(), sorted(p.name for p in st.text_dir(ws.state).glob('*/*.txt'))
            health = st.health_path(ws.state).read_bytes()
            ws.scan(board(20))
            self.assertEqual(ws.state.read_bytes(), index)
            self.assertEqual(sorted(p.name for p in st.text_dir(ws.state).glob('*/*.txt')), texts)
            self.assertNotEqual(st.health_path(ws.state).read_bytes(), health)  # only health moves

    def test_not_seen_freezes_last_seen_and_reappearance_clears_it(self):
        s, _ = st.merge({'schema': 2, 'postings': {}}, {'lever:example': [post(1)]}, T1)
        s, _ = st.merge(s, {'lever:example': [post(1)]}, T2)
        s, events = st.merge(s, {'lever:example': []}, T3)
        row = s['postings']['lever:example:1']
        self.assertEqual((row['feed_status'], row['missing_since'], st.last_seen(row, s['boards'])), ('not_seen', T3, T2))
        s, events = st.merge(s, {'lever:example': [post(1)]}, '2026-10-20T07:00:00Z')
        self.assertEqual(events[0]['type'], 'reappeared')
        self.assertNotIn('last_seen', s['postings']['lever:example:1'])


class ProvenanceTests(unittest.TestCase):
    def test_rule_change_with_identical_source_is_a_screening_event_only(self):
        s, _ = st.merge({'schema': 2, 'postings': {}}, {'lever:example': [post(1), post(2, 'Senior Accountant')]}, T1)
        def stricter(inputs):
            result = real_screen(inputs)
            if result['verdict'] == 'priority':
                result = {**result, 'verdict': 'review', 'codes': result['codes'] + ['level.unknown'], 'rules_version': 'test-2'}
            return {**result, 'rules_version': 'test-2'}
        with patch('jobwatch.state.screen', stricter), patch('jobwatch.state.RULES_VERSION', 'test-2'):
            s, events = st.merge(s, {'lever:example': [post(1), post(2, 'Senior Accountant')]}, T2)
        self.assertEqual([e['type'] for e in events], ['rescreened'])
        self.assertEqual(events[0]['cause'], 'rules_changed')
        row = s['postings']['lever:example:1']
        self.assertEqual(row['source_events'], [])
        self.assertEqual(row['screening_events'][0]['before']['verdict'], 'priority')
        self.assertEqual(row['screening']['rules_version'], 'test-2')
        # The excluded posting's codes and verdict were unchanged: version updated, no event.
        self.assertEqual(s['postings']['lever:example:2']['screening_events'], [])
        self.assertEqual(s['postings']['lever:example:2']['screening']['rules_version'], 'test-2')

    def test_employer_edit_is_a_source_event_and_cause_is_recorded(self):
        s, _ = st.merge({'schema': 2, 'postings': {}}, {'lever:example': [post(1)]}, T1)
        s, events = st.merge(s, {'lever:example': [post(1, 'Senior Software Engineer', 'Lead a team.')]}, T2)
        self.assertEqual([e['type'] for e in events], ['changed', 'rescreened'])
        self.assertEqual(set(events[0]['changes']), {'title', 'description_sha256'})
        self.assertEqual(events[1]['cause'], 'source_changed')
        row = s['postings']['lever:example:1']
        # The previous description text stays available for a diff.
        before = row['source_events'][-1]['changes']['description_sha256']['before']
        self.assertEqual(s['texts'][before], 'Build software in Python.')

    def test_failed_board_records_are_rescreened_from_stored_fields_without_source_change(self):
        s, _ = st.merge({'schema': 2, 'postings': {}}, {'lever:example': [post(1)]}, T1)
        snapshot = {k: v for k, v in s['postings']['lever:example:1'].items() if k not in ('screening', 'screening_events')}
        with patch('jobwatch.state.RULES_VERSION', 'test-2'), \
                patch('jobwatch.state.screen', lambda i: {**real_screen(i), 'rules_version': 'test-2'}):
            s, events = st.merge(s, {}, T2)  # board failed: not in `successful`
        row = s['postings']['lever:example:1']
        self.assertEqual({k: v for k, v in row.items() if k not in ('screening', 'screening_events')}, snapshot)
        self.assertEqual(row['screening']['rules_version'], 'test-2')
        self.assertEqual(events, [])

    def test_rescreen_preview_writes_nothing(self):
        s, _ = st.merge({'schema': 2, 'postings': {}}, {'lever:example': [post(1)]}, T1)
        s['postings']['lever:example:1']['screening']['verdict'] = 'excluded'
        before = json.dumps(s, sort_keys=True)
        preview = st.rescreen_preview(s, T2)
        self.assertEqual(preview[0]['after']['verdict'], 'priority')
        self.assertEqual(json.dumps(s, sort_keys=True), before)


LEGACY_ROW = {
    'company': 'Example', 'provider': 'greenhouse', 'board': 'example', 'external_id': '9', 'title': 'Graduate Engineer',
    'location': 'London', 'url': 'https://job-boards.greenhouse.io/example/jobs/9', 'priority': 'review',
    'reason': 'old wording', 'role_family': 'engineering_programme', 'location_judgment': 'unknown',
    'published_at': '', 'source_dates': {}, 'salary': '', 'country': '', 'countries': [],
    'description': '&lt;p&gt;Graduate scheme&lt;/p&gt;', 'raw': {'departments': [{'name': 'Graduate Opportunities'}], 'content': 'x'},
    'deadlines': [], 'feed_status': 'listed', 'first_seen': T1, 'last_seen': T2,
    'application_verification': {'status': 'unknown'},
    'history': [{'type': 'changed', 'at': T2, 'changes': {'reason': {'before': 'a', 'after': 'b'}}}]}


def legacy_state():
    return {'schema': 1, 'postings': {'greenhouse:example:9': json.loads(json.dumps(LEGACY_ROW))},
            'boards': {'greenhouse:example': {'status': 'success', 'last_success': T2, 'last_attempt': T2, 'count': 3}},
            'last_scan': {'at': T2, 'events': [], 'failures': []}}


class MigrationTests(unittest.TestCase):
    def test_schema_one_upgrade_is_deterministic_idempotent_and_preserves_evidence(self):
        outputs = []
        for _ in range(2):
            with TemporaryDirectory() as d:
                path = Path(d) / 'jobs.json'
                path.write_text(json.dumps(legacy_state()))
                loaded = st.load_state(path)
                st.save_state(path, loaded)
                first = path.read_bytes()
                st.save_state(path, st.load_state(path))  # retry after migration is a no-op
                self.assertEqual(path.read_bytes(), first)
                outputs.append(first)
                row = st.load_state(path)['postings']['greenhouse:example:9']
                self.assertEqual(row['legacy_history'], LEGACY_ROW['history'])
                self.assertEqual(row['department'], 'Graduate Opportunities')
                self.assertNotIn('raw', row); self.assertNotIn('description', row)
                self.assertEqual(row['screening']['rules_version'], st.LEGACY_RULES)
                self.assertEqual(row['first_seen_basis'], 'legacy_retained')
                self.assertEqual(st.description_of(st.load_state(path), row), LEGACY_ROW['description'])
        self.assertEqual(outputs[0], outputs[1])

    def test_first_complete_scan_of_legacy_board_marks_backfill_not_new(self):
        s = legacy_state()
        newcomer = Posting('Example', 'greenhouse', 'example', '10', 'Senior Accountant', 'London', 'https://job-boards.greenhouse.io/example/jobs/10')
        legacy = Posting('Example', 'greenhouse', 'example', '9', 'Graduate Engineer', 'London', LEGACY_ROW['url'],
                         description=LEGACY_ROW['description'], department='Graduate Opportunities')
        s, events = st.merge(s, {'greenhouse:example': [legacy, newcomer]}, T3)
        self.assertEqual(s['postings']['greenhouse:example:10']['first_seen_basis'], 'index_backfill')
        self.assertIn('backfilled', [e['type'] for e in events])
        self.assertIn('legacy_migration', [e.get('cause') for e in events])
        self.assertNotIn('changed', [e['type'] for e in events])  # department first collected now is not an edit
        later = Posting('Example', 'greenhouse', 'example', '11', 'Graduate Software Engineer', 'London', 'https://job-boards.greenhouse.io/example/jobs/11')
        s, events = st.merge(s, {'greenhouse:example': [legacy, newcomer, later]}, '2026-10-20T07:00:00Z')
        self.assertEqual(s['postings']['greenhouse:example:11']['first_seen_basis'], 'observed')
        self.assertEqual([e['type'] for e in events], ['new'])

    def test_unknown_schema_and_corrupt_text_refuse(self):
        with TemporaryDirectory() as d:
            path = Path(d) / 'jobs.json'
            path.write_text('{"schema": 3, "postings": {}}')
            with self.assertRaises(ValueError): st.load_state(path)
            s, _ = st.merge({'schema': 2, 'postings': {}}, {'lever:example': [post(1)]}, T1)
            st.save_state(path, s)
            file = next(st.text_dir(path).glob('*/*.txt'))
            file.write_text('tampered')
            with self.assertRaises(ValueError): st.load_state(path)


def screen_basis(state, row):
    return real_screen(st.stored_inputs(state, row))['basis']


class TextStoreTests(unittest.TestCase):
    def test_texts_are_immutable_garbage_collected_and_kept_for_unseen_records(self):
        with TemporaryDirectory() as d:
            path = Path(d) / 'jobs.json'
            s, _ = st.merge({'schema': 2, 'postings': {}}, {'lever:example': [post(1, description='v1')]}, T1)
            st.save_state(path, s, T1)
            s, _ = st.merge(st.load_state(path), {'lever:example': [post(1, description='v2')]}, T2)
            st.save_state(path, s, T2)
            s, _ = st.merge(st.load_state(path), {'lever:example': [post(1, description='v3')]}, T3)
            st.save_state(path, s, T3)
            stored = {p.read_text() for p in st.text_dir(path).glob('*/*.txt')}
            self.assertEqual(stored, {'v2', 'v3'})  # current + the 'before' of the latest change
            s, _ = st.merge(st.load_state(path), {'lever:example': []}, '2026-10-20T07:00:00Z')
            st.save_state(path, s, '2028-06-01T00:00:00Z')  # long unseen: no age-based expiry
            loaded = st.load_state(path)
            row = loaded['postings']['lever:example:1']
            self.assertEqual(st.description_of(loaded, row), 'v3')
            self.assertEqual(screen_basis(loaded, row), 'full')  # rescreening keeps full evidence
            del loaded['postings']['lever:example:1']['description_sha256']
            self.assertEqual(screen_basis(loaded, row), 'title_metadata_only')


if __name__ == '__main__':
    unittest.main()
