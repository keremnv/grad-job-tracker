"""Interrupted writes during the first schema-1 → schema-2 migration scan, and recovery."""
import contextlib
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from jobwatch import state as st
from jobwatch.cli import main, scan
from jobwatch.feeds import Posting

T1, T2, T3 = '2026-10-20T07:27:00Z', '2026-10-21T07:27:00Z', '2026-10-22T07:27:00Z'
ENTRY = {'company': 'Example', 'provider': 'lever', 'board': 'example', 'enabled': True,
         'careers_url': 'https://example.org/careers', 'validation_url': 'https://example.org/careers',
         'mapping_verified_at': '2026-10-17T00:00:00Z'}
KEPT = [('g1', 'Graduate Software Engineer'), ('g2', 'Junior Backend Engineer'), ('g3', 'Software Engineer'),
        ('g4', 'Graduate Platform Engineer')]
DROPPED = [(f'x{i}', title) for i, title in enumerate(['Senior Accountant', 'Sales Development Representative',
                                                      'Staff Software Engineer', 'Software Engineering Intern'] * 5)]


def post(i, title, description=None):
    return Posting('Example', 'lever', 'example', i, title, 'London, United Kingdom', f'https://jobs.lever.co/example/{i}',
                   description=description or f'<p>Role {i}: build things in Python.</p>', country='GB', countries=('GB',))


def schema_one():
    """The production shape before migration: only kept postings, board health inline."""
    rows = {}
    for i, title in KEPT:
        p = post(i, title)
        rows[p.key] = {'company': p.company, 'provider': 'lever', 'board': 'example', 'external_id': i, 'title': title,
                       'location': p.location, 'url': p.url, 'priority': 'review', 'reason': 'old wording',
                       'role_family': 'software', 'location_judgment': 'uk_signal', 'published_at': '', 'source_dates': {},
                       'salary': '', 'country': 'GB', 'countries': ['GB'], 'description': p.description,
                       'raw': {'categories': {'department': 'Engineering'}}, 'deadlines': [], 'feed_status': 'listed',
                       'first_seen': T1, 'last_seen': T1, 'application_verification': {'status': 'unknown'}, 'history': []}
    return {'schema': 1, 'postings': rows,
            'boards': {'lever:example': {'status': 'success', 'last_success': T1, 'last_attempt': T1, 'count': len(KEPT)}},
            'last_scan': {'at': T1, 'events': [], 'failures': []}}


FEED = [post(i, t) for i, t in KEPT] + [post(i, t) for i, t in DROPPED]


class Fault(OSError):
    pass


class Workspace:
    def __init__(self, root):
        self.root = Path(root)
        self.config, self.state, self.report = self.root / 'config.json', self.root / 'data' / 'jobs.json', self.root / 'latest.md'
        self.config.write_text(json.dumps({'employers': [ENTRY]}))
        self.state.parent.mkdir()
        self.state.write_text(json.dumps(schema_one()))

    def scan(self, at, fail_on=None, feed=FEED):
        """Scan at time `at`; with `fail_on=n` the n-th file replacement (0-based) raises."""
        real, calls = st.atomic_write, []

        def write(path, content):
            calls.append(Path(path))
            if fail_on is not None and len(calls) - 1 == fail_on:
                raise Fault('simulated interruption')
            return real(path, content)
        with patch('jobwatch.state.atomic_write', write), patch('jobwatch.cli.atomic_write', write), \
                patch('jobwatch.cli.utcnow', return_value=at), patch('jobwatch.cli.collect_board', return_value=feed), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            try:
                return scan(self.config, self.state, self.report), calls
            except Fault:
                return 'interrupted', calls

    def texts_on_disk(self):
        return {p.stem for p in st.text_dir(self.state).glob('*/*.txt')}


def summary(path):
    s = st.load_state(path)
    return {k: (r['screening']['verdict'], r['feed_status'], r['first_seen_basis']) for k, r in s['postings'].items()}


class FirstMigrationInterruptionTests(unittest.TestCase):
    def reference(self):
        with TemporaryDirectory() as d:
            ws = Workspace(d)
            code, calls = ws.scan(T2)
            self.assertEqual(code, 0)
            ws.scan(T3)
            return summary(ws.state), calls

    def test_every_interruption_point_recovers_without_false_new_events(self):
        expected, calls = self.reference()
        kinds = ['text' if p.suffix == '.txt' else p.name for p in calls]
        self.assertEqual(kinds[-3:], ['jobs.json', 'jobs-health.json', 'latest.md'])  # texts, index, health, report
        self.assertEqual(kinds.count('text'), len(FEED))
        for n in range(len(calls)):
            with self.subTest(interrupted_before=f'{n}:{kinds[n]}'), TemporaryDirectory() as d:
                ws = Workspace(d)
                self.assertEqual(ws.scan(T2, fail_on=n)[0], 'interrupted')
                st.load_state(ws.state)  # every intermediate state still loads
                code, _ = ws.scan(T3)
                self.assertEqual(code, 0)
                saved = st.load_state(ws.state)
                types = [e['type'] for e in saved['last_scan']['events']]
                self.assertNotIn('new', types)  # unrecorded postings are backfill, never "new"
                self.assertEqual(summary(ws.state), expected)
                self.assertEqual(ws.texts_on_disk(), st.referenced_texts(saved))  # nothing missing, no orphans
                self.assertIsNotNone(saved['boards']['lever:example']['index_complete_since'])
                self.assertEqual(json.loads(ws.state.read_text())['schema'], 2)

    def test_interruption_after_index_on_first_migration_loses_only_board_history(self):
        with TemporaryDirectory() as d:
            ws = Workspace(d)
            code, calls = ws.scan(T2, fail_on=len(FEED) + 1)  # index written, health not
            self.assertEqual(code, 'interrupted')
            s = st.load_state(ws.state)
            self.assertEqual(s['boards'], {})  # schema-1 health was inside the replaced index (still in Git history)
            self.assertEqual(len(s['postings']), len(FEED))
            self.assertTrue(all(r['first_seen_basis'] in ('legacy_retained', 'index_backfill') for r in s['postings'].values()))
            ws.scan(T3)
            s = st.load_state(ws.state)
            self.assertEqual(s['boards']['lever:example']['last_success'], T3)

    def test_interrupted_orphan_removal_is_finished_by_the_next_save(self):
        with TemporaryDirectory() as d:
            ws = Workspace(d)
            ws.scan(T2)
            changed = [post(i, t, f'v2 {i}') if i == 'g1' else p for p, (i, t) in zip(FEED, KEPT + DROPPED)]
            ws.scan(T3, feed=changed)
            again = [post(i, t, f'v3 {i}') if i == 'g1' else p for p, (i, t) in zip(FEED, KEPT + DROPPED)]
            real = Path.unlink

            def unlink(self, *a, **k):
                if self.suffix == '.txt':
                    raise Fault('interrupted during orphan removal')
                return real(self, *a, **k)
            with patch.object(Path, 'unlink', unlink):
                self.assertEqual(ws.scan('2026-10-23T07:27:00Z', feed=again)[0], 'interrupted')
            s = st.load_state(ws.state)
            self.assertGreater(len(ws.texts_on_disk() - st.referenced_texts(s)), 0)  # orphan left behind
            ws.scan('2026-10-24T07:27:00Z', feed=again)
            self.assertEqual(ws.texts_on_disk(), st.referenced_texts(st.load_state(ws.state)))

    def test_cli_exit_code_tells_the_workflow_not_to_commit(self):
        with TemporaryDirectory() as d:
            ws = Workspace(d)
            argv = ['scan', '--config', str(ws.config), '--state', str(ws.state), '--report', str(ws.report),
                    '--verifications', str(ws.root / 'data' / 'verifications.json')]
            with patch('jobwatch.state.atomic_write', side_effect=OSError(28, 'No space left on device')), \
                    patch('jobwatch.cli.collect_board', return_value=FEED), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main(argv), 2)
            self.assertIn('must not be committed', err.getvalue())
            with patch('jobwatch.cli.load_state', side_effect=[st.load_state(ws.state), RuntimeError('unexpected bug')]), \
                    patch('jobwatch.cli.collect_board', return_value=FEED), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(argv), 2)  # an unexpected exception is 2, never Python's default 1

    def test_post_save_verification_catches_a_missing_text_file(self):
        with TemporaryDirectory() as d:
            ws = Workspace(d)
            real = st.save_state

            def lossy(path, state, now=None):
                real(path, state, now)
                next(st.text_dir(path).glob('*/*.txt')).unlink()
            with patch('jobwatch.cli.save_state', lossy), patch('jobwatch.cli.collect_board', return_value=FEED), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaisesRegex(ValueError, 'missing after save'):
                scan(ws.config, ws.state, ws.report)


if __name__ == '__main__':
    unittest.main()
