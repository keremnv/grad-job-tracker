import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from jobwatch.cli import scan
from jobwatch.feeds import Posting


class CLITests(unittest.TestCase):
    def test_one_good_one_failed_board(self):
        with TemporaryDirectory() as d:
            d = Path(d)
            config = d / 'employers.json'
            state = d / 'jobs.json'
            report = d / 'report.md'
            config.write_text(json.dumps({'employers': [
                {'company': 'Good', 'provider': 'lever', 'board': 'good', 'enabled': True, 'careers_url': 'https://example.org/careers', 'validation_url': 'https://example.org/careers', 'mapping_verified_at': '2026-10-17T00:00:00Z'},
                {'company': 'Broken', 'provider': 'lever', 'board': 'broken', 'enabled': True, 'careers_url': 'https://example.org/careers', 'validation_url': 'https://example.org/careers', 'mapping_verified_at': '2026-10-17T00:00:00Z'}]}))
            def fake(entry):
                if entry['board'] == 'broken':
                    raise ValueError('bad response')
                return [Posting('Good', 'lever', 'good', 'a', 'Software Engineer New Grad',
                                'London', 'https://jobs.lever.co/good/a')]
            with patch('jobwatch.cli.collect_board', side_effect=fake):
                code = scan(config, state, report)
            self.assertEqual(code, 1)
            self.assertIn('bad response', report.read_text())
            self.assertIn('lever:good:a', json.loads(state.read_text())['postings'])

    def test_total_failure_updates_health_and_report_preserves_postings(self):
        with TemporaryDirectory() as d:
            d = Path(d)
            config = d / 'employers.json'
            state = d / 'jobs.json'
            report = d / 'report.md'
            config.write_text(json.dumps({'employers': [
                {'company': 'Broken', 'provider': 'lever', 'board': 'broken', 'enabled': True, 'careers_url': 'https://example.org/careers', 'validation_url': 'https://example.org/careers', 'mapping_verified_at': '2026-10-17T00:00:00Z'}]}))
            state.write_text('{"schema":1,"postings":{}}')
            report.write_text('OLD REPORT')
            with patch('jobwatch.cli.collect_board', side_effect=ValueError('bad response')):
                code = scan(config, state, report)
            self.assertEqual(code, 2)
            self.assertIn('bad response', report.read_text())
            saved = json.loads(state.read_text())
            self.assertEqual(saved['postings'], {})
            self.assertEqual(saved['boards']['lever:broken']['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
