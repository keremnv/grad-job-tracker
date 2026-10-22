"""Offline owner-ledger behaviour, transaction failures and CLI privacy."""
import contextlib
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from jobwatch import applications as app
from jobwatch.cli import main, report, scan
from jobwatch.feeds import Posting


def row(key='manual:one', url='https://example.org/jobs/one', **changes):
    return app.make_application(dict(job_key=key, company='Example', role='Graduate Engineer',
                                     url=url, **changes))


def csv_bytes(rows, fields=app.COLUMNS):
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=fields, lineterminator='\r\n')
    writer.writeheader(); writer.writerows(rows)
    return out.getvalue().encode('utf-8')


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'applications.csv'

    def test_manual_add_update_preserves_every_unspecified_field(self):
        original = row(stage='applied', date_applied='', cv_version='swe-v1',
                       next_action='Prepare, review\nthen practise', next_action_date='2026-10-22',
                       notes='PRIVATE “note”, commas\nand newlines')
        app.add(self.path, original)
        app.update(self.path, original['job_key'], {'stage': 'interview'})
        self.assertEqual(app.read_applications(self.path), [{**original, 'stage': 'interview'}])
        app.update(self.path, original['job_key'], {'date_applied': '2026-10-17', 'cv_version': 'swe-v2',
                   'next_action': 'Read', 'next_action_date': '', 'notes': ''})
        updated = app.read_applications(self.path)[0]
        self.assertEqual(updated['date_applied'], '2026-10-17')
        self.assertEqual(updated['next_action_date'], '')
        self.assertEqual(updated['notes'], '')
        self.assertEqual(updated['cv_version'], 'swe-v2')
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertIn(b'\r\n', self.path.read_bytes())

    def test_verified_url_deterministic_identity_or_explicit_key_required(self):
        values = dict(company='Example', role='Role', url='https://EXAMPLE.org:443/jobs/one#apply')
        with self.assertRaises(ValueError): app.make_application(values)
        first = app.make_application(values, verified_url=True)
        second = app.make_application({**values, 'url': 'https://example.org/jobs/one'}, verified_url=True)
        self.assertEqual(first['job_key'], second['job_key'])
        self.assertTrue(first['job_key'].startswith('manual:'))
        self.assertEqual(first['date_applied'], '')
        self.assertEqual(first['stage'], 'bookmarked')
        self.assertNotEqual(first['job_key'], app.make_application({**values, 'url': 'https://example.org/jobs/two'}, verified_url=True)['job_key'])

    def test_link_posting_uses_stable_id_and_rejects_mismatched_url(self):
        postings = {'lever:example:one': {'company': 'Tracked', 'title': 'Role', 'url': 'https://example.org/jobs/one'}}
        linked = app.make_application({}, postings, posting_id='lever:example:one')
        self.assertEqual(linked['job_key'], 'lever:example:one')
        self.assertEqual(linked['company'], 'Tracked')
        for values, key in (({}, 'missing'), ({'job_key': 'other'}, 'lever:example:one'),
                            ({'url': 'https://example.org/jobs/two'}, 'lever:example:one')):
            with self.assertRaises(ValueError): app.make_application(values, postings, posting_id=key)

    def test_duplicate_id_and_url_collisions_never_merge(self):
        app.add(self.path, row(notes='PRIVATE'))
        original = self.path.read_bytes()
        for duplicate in (row(url='https://example.org/jobs/two'), row(key='manual:two'),
                          row(key='manual:three', url='https://EXAMPLE.org:443/jobs/one#apply')):
            with self.assertRaises(ValueError): app.add(self.path, duplicate)
            self.assertEqual(self.path.read_bytes(), original)
        for changes in ({}, {'company': 'Changed'}, {'stage': 'invented'}):
            with self.assertRaises(ValueError): app.update(self.path, 'manual:one', changes)
        with self.assertRaises(ValueError): app.update(self.path, 'missing', {'stage': 'applied'})
        self.assertEqual(self.path.read_bytes(), original)

    def test_stage_date_and_url_validation(self):
        for stage in app.STAGES:
            self.assertEqual(row(stage=stage)['stage'], stage)
        for field in ('date_applied', 'next_action_date'):
            for value in ('', '2024-02-29', '2026-12-31'):
                self.assertEqual(row(**{field: value})[field], value)
            for value in ('2026-02-29', '20261022', '2026-W01-1', '22/10/2026', '2026-10-22T12:00:00Z'):
                with self.subTest(field=field, value=value), self.assertRaises(ValueError): row(**{field: value})
        for values in ({'stage': ''}, {'stage': 'closed'}, {'company': ''}, {'job_key': 'spaces bad'},
                       {'url': 'javascript:alert(1)'}, {'url': 'https://user:pass@example.org/x'}):
            raw = row(); raw.update(values)
            with self.assertRaises(ValueError): app.validate([raw])

    def test_malformed_csv_and_duplicate_headers_rows_fail_without_writes(self):
        valid = csv_bytes([row()])
        invalid = [b'', b'job_key,company\nx,y\n', valid + b'x,y\n',
                   valid + b'"unterminated', valid + b'\n', valid + b'\xff',
                   csv_bytes([row(), row()]), valid.replace(b'company,role', b'company,company')]
        for content in invalid:
            with self.subTest(content=content[:35]), self.assertRaises(ValueError): app.parse_csv(content)
        self.assertEqual(app.parse_csv(b'\xef\xbb\xbf' + valid), [row()])
        # A corrupt existing ledger is never "repaired" by dropping unknown fields.
        self.path.write_bytes(valid + b'x,y\n')
        original = self.path.read_bytes()
        with self.assertRaises(ValueError): app.add(self.path, row('manual:two', 'https://example.org/two'))
        self.assertEqual(original, self.path.read_bytes())
        reversed_columns = tuple(reversed(app.COLUMNS))
        self.assertEqual(app.parse_csv(csv_bytes([row()], reversed_columns)), [row()])

    def test_spreadsheet_formulas_rejected_without_mutating_notes(self):
        for field in app.COLUMNS:
            for prefix in ('=HYPERLINK(', ' +SUM(', '\t@command', '\n-danger'):
                raw = row(); raw[field] = prefix + 'PRIVATE'
                with self.assertRaises(ValueError) as error: app.validate([raw])
                self.assertNotIn('PRIVATE', str(error.exception))

    def test_import_default_and_explicit_dry_run_no_writes_then_confirm(self):
        source = self.root / 'history.csv'
        incoming = [row(f'history:{i}', f'https://example.org/jobs/{i}', stage='applied', notes='PRIVATE') for i in range(20)]
        source.write_bytes(csv_bytes(incoming))
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['applications', 'import-csv', str(source), '--applications', str(self.path), '--dry-run']), 0)
            self.assertFalse(self.path.exists())
            self.assertEqual(main(['applications', 'import-csv', str(source), '--applications', str(self.path)]), 0)
            self.assertFalse(self.path.exists())
            self.assertEqual(main(['applications', 'import-csv', str(source), '--applications', str(self.path), '--confirm']), 0)
        self.assertEqual(app.read_applications(self.path), incoming)
        self.assertIn('20 new rows', output.getvalue())
        self.assertNotIn('PRIVATE', output.getvalue())

    def test_import_conflicts_are_all_or_nothing(self):
        app.add(self.path, row())
        before = self.path.read_bytes()
        source = self.root / 'history.csv'
        unique = row('manual:two', 'https://example.org/two')
        for incoming in ([unique, row()], [unique, unique], [{**unique, 'stage': 'invalid'}],
                         [row('different:key')]):
            source.write_bytes(csv_bytes(incoming))
            for confirm in (False, True):
                with self.assertRaises(ValueError): app.import_csv(self.path, source, confirm, preview=lambda _: None)
                self.assertEqual(before, self.path.read_bytes())

    def test_import_detects_owner_edit_after_preview(self):
        app.add(self.path, row())
        source = self.root / 'history.csv'
        source.write_bytes(csv_bytes([row('manual:two', 'https://example.org/two')]))
        owner_edit = csv_bytes([row(notes='OWNER EDIT')])
        def preview(_): self.path.write_bytes(owner_edit)
        with self.assertRaisesRegex(ValueError, 'changed after preview'):
            app.import_csv(self.path, source, True, preview)
        self.assertEqual(self.path.read_bytes(), owner_edit)

    def test_atomic_replace_failure_keeps_old_bytes_and_cleans_temp_and_lock(self):
        app.add(self.path, row())
        before = self.path.read_bytes()
        with patch('jobwatch.applications.os.replace', side_effect=OSError('simulated failure')):
            with self.assertRaises(OSError): app.update(self.path, 'manual:one', {'stage': 'interview'})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.root.glob('.jobwatch-*')), [])
        self.assertFalse(self.path.with_suffix('.csv.lock').exists())

    def test_import_detects_same_bytes_file_replacement_after_preview(self):
        app.add(self.path, row())
        source = self.root / 'history.csv'
        source.write_bytes(csv_bytes([row('manual:two', 'https://example.org/two')]))
        original = self.path.read_bytes()
        def preview(_):
            replacement = self.root / 'replacement.csv'
            replacement.write_bytes(original)
            os.replace(replacement, self.path)
        with self.assertRaisesRegex(ValueError, 'changed after preview'):
            app.import_csv(self.path, source, True, preview)
        self.assertEqual(self.path.read_bytes(), original)

    def test_external_edit_during_temp_write_is_preserved(self):
        app.add(self.path, row())
        owner_edit = csv_bytes([row(notes='OWNER EDIT')])
        fsync = os.fsync
        def changed(fd):
            self.path.write_bytes(owner_edit)
            fsync(fd)
        with patch('jobwatch.applications.os.fsync', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'changed during edit'):
                app.update(self.path, 'manual:one', {'stage': 'interview'})
        self.assertEqual(self.path.read_bytes(), owner_edit)
        self.assertEqual(list(self.root.glob('.jobwatch-*')), [])

    def test_concurrent_cli_process_refuses_locked_ledger(self):
        app.add(self.path, row())
        before = self.path.read_bytes()
        with app.locked(self.path):
            result = subprocess.run([sys.executable, '-m', 'jobwatch.cli', 'applications', 'update',
                                     '--applications', str(self.path), '--job-key', 'manual:one',
                                     '--stage', 'interview'], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('lock exists', result.stderr)
        self.assertEqual(self.path.read_bytes(), before)
        app.update(self.path, 'manual:one', {'stage': 'interview'})
        self.assertEqual(app.read_applications(self.path)[0]['stage'], 'interview')

    def test_symlink_ledger_refused(self):
        target = self.root / 'other.csv'; target.write_bytes(csv_bytes([row()]))
        self.path.symlink_to(target)
        with self.assertRaises(ValueError): app.update(self.path, 'manual:one', {'stage': 'interview'})
        self.assertEqual(app.parse_csv(target.read_bytes()), [row()])

    def test_cli_manual_add_update_list_filters_and_no_notes_on_errors(self):
        base = ['--applications', str(self.path)]
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(['applications', 'add', *base, '--job-key', 'manual:one', '--company', '<script>Example</script>',
                                   '--role', '[role]', '--url', 'https://example.org/jobs/one', '--notes', 'PRIVATE NOTE',
                                   '--next-action', 'PRIVATE ACTION', '--next-action-date', '2026-10-22']), 0)
            self.assertEqual(main(['applications', 'update', *base, '--job-key', 'manual:one', '--stage', 'applied']), 0)
            self.assertEqual(main(['applications', 'list', *base, '--stage', 'applied', '--due-on-or-before', '2026-10-22', '--due-on-or-after', '2026-10-22']), 0)
            self.assertIn('1 application(s)', out.getvalue())
            self.assertEqual(main(['applications', 'update', *base, '--job-key', 'manual:one', '--next-action-date', 'PRIVATE INVALID DATE']), 2)
        self.assertNotIn('PRIVATE', out.getvalue() + err.getvalue())
        self.assertNotIn('<script>', out.getvalue())
        self.assertIn('&lt;script&gt;', out.getvalue())
        with contextlib.redirect_stdout(io.StringIO()) as filtered:
            self.assertEqual(main(['applications', 'list', *base, '--stage', 'interview']), 0)
        self.assertIn('0 application(s)', filtered.getvalue())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(['applications', 'list', *base, '--due-on-or-before', 'invalid']), 2)
            self.assertEqual(main(['applications', 'list', *base, '--due-on-or-before', '2026-10-01', '--due-on-or-after', '2026-10-22']), 2)

    def test_scan_preserves_every_owner_file_even_with_concurrent_application_update(self):
        app.add(self.path, row(notes='PRIVATE NOTE'))
        config = self.root / 'config.json'
        config.write_text(json.dumps({'employers': [{'company': 'Example', 'provider': 'lever', 'board': 'example',
            'enabled': True, 'careers_url': 'https://example.org', 'validation_url': 'https://example.org',
            'mapping_verified_at': '2026-10-17T00:00:00Z'}]}))
        verification = self.root / 'verifications.json'; verification.write_text('{"schema":1,"postings":{}}')
        leads = self.root / 'leads.json'; leads.write_text('OWNER LEADS')
        state_path = self.root / 'jobs.json'; output = self.root / 'latest.md'
        def collect(_):
            app.update(self.path, 'manual:one', {'stage': 'interview'})
            return [Posting('Example', 'lever', 'example', 'one', 'Graduate Software Engineer', 'UK', 'https://example.org/jobs/one')]
        with patch('jobwatch.cli.collect_board', side_effect=collect), contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(scan(config, state_path, output, verification), 0)
        self.assertEqual(app.read_applications(self.path)[0]['stage'], 'interview')
        self.assertEqual(app.read_applications(self.path)[0]['notes'], 'PRIVATE NOTE')
        self.assertEqual(leads.read_text(), 'OWNER LEADS')
        self.assertEqual(verification.read_text(), '{"schema":1,"postings":{}}')
        self.assertNotIn('PRIVATE', output.read_text() + out.getvalue() + err.getvalue())
        # Explicit output paths must also respect owner files outside state dir.
        for dest in (self.path, leads):
            with self.assertRaises(ValueError): scan(config, self.root/'other'/'jobs.json', dest, verification, self.path, leads)
        # Selecting alternate owner files must not unprotect the default ledger.
        with contextlib.chdir(self.root):
            Path('data').mkdir()
            default = Path('data/applications.csv')
            default.write_bytes(self.path.read_bytes())
            before = default.read_bytes()
            with self.assertRaises(ValueError):
                scan(config, self.root/'other'/'jobs.json', default, verification,
                     self.root/'alternate.csv', self.root/'alternate-leads.json')
            self.assertEqual(default.read_bytes(), before)
        self.assertNotIn('PRIVATE', report(json.loads(state_path.read_text())))
