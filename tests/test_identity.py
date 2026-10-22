import json
from pathlib import Path
import unittest

from jobwatch import calendar as cal
from jobwatch.applications import url_identity
from jobwatch.feeds import collect_board
from jobwatch.identity import exact_url, link_application, loose_url, posting_key

FIXTURES = Path(__file__).parent / 'fixtures'


class IdentityTests(unittest.TestCase):
    def test_levels_preserve_query_and_only_exact_keeps_fragment(self):
        url = 'https://JOBS.example.org:443/careers?job=7#/opening/2'
        self.assertEqual(exact_url(url), 'https://jobs.example.org/careers?job=7#/opening/2')
        self.assertEqual(loose_url(url), 'https://jobs.example.org/careers?job=7')
        # The ledger and calendar now delegate to the same definitions.
        self.assertEqual(url_identity(url), loose_url(url))
        self.assertEqual(cal.safe_url(url), exact_url(url))
        self.assertEqual(posting_key('lever', 'Palantir', 'x1'), 'lever:palantir:x1')

    def test_link_prefers_key_then_exact_url_and_only_warns_on_fragment_variant(self):
        rows = [{'job_key': 'manual:a', 'url': 'https://example.org/jobs#one'},
                {'job_key': 'lever:x:1', 'url': 'https://example.org/other'}]
        self.assertEqual(link_application('lever:x:1', 'https://jobs.lever.co/x/1', rows)[1], 'job_key')
        row, basis, possible = link_application('lever:x:2', 'https://example.org/jobs#one', rows)
        self.assertEqual((row['job_key'], basis, possible), ('manual:a', 'exact_url', []))
        row, basis, possible = link_application('lever:x:3', 'https://example.org/jobs#two', rows)
        self.assertIsNone(row); self.assertEqual(basis, 'none'); self.assertEqual(possible[0]['job_key'], 'manual:a')
        # Two exact URL matches are ambiguous and are never linked.
        twins = [{'job_key': 'manual:b', 'url': 'https://example.org/z'}, {'job_key': 'manual:c', 'url': 'https://example.org/z'}]
        self.assertEqual(link_application('lever:x:4', 'https://example.org/z', twins)[:2], (None, 'none'))


class FeedMetadataTests(unittest.TestCase):
    def test_department_team_and_non_vacancy_accounting(self):
        cases = {'greenhouse': ({'company': 'Monzo', 'provider': 'greenhouse', 'board': 'monzo'}, 'Finance', ''),
                 'lever': ({'company': 'Palantir', 'provider': 'lever', 'board': 'palantir'}, '', 'Delta'),
                 'ashby': ({'company': 'incident.io', 'provider': 'ashby', 'board': 'incident'}, 'Product Development', 'Engineering')}
        for kind, (entry, department, team) in cases.items():
            with self.subTest(kind=kind):
                data = json.loads((FIXTURES / (kind + '.json')).read_text())
                stats = {}
                rows = collect_board(entry, lambda _: data, stats=stats)
                self.assertEqual((rows[0].department, rows[0].team), (department, team))
                self.assertEqual(stats['source_records'], len(rows) + stats['skipped_non_vacancies'])

    def test_odd_metadata_shape_never_fails_a_board(self):
        data = json.loads((FIXTURES / 'ashby.json').read_text())
        data['jobs'][0]['department'] = {'unexpected': 'object'}
        rows = collect_board({'company': 'incident.io', 'provider': 'ashby', 'board': 'incident'}, lambda _: data)
        self.assertEqual(rows[0].department, '')


if __name__ == '__main__':
    unittest.main()
