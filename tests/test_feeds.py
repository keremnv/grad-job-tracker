import unittest
from jobwatch.feeds import collect_board


class FeedTests(unittest.TestCase):
    def test_greenhouse_and_prospect_exclusion(self):
        def fake(url):
            self.assertIn('/monzo/jobs?content=true', url)
            return {'jobs': [
                {'id': 17, 'internal_job_id': 99, 'title': 'Graduate Software Engineer',
                 'absolute_url': 'https://job-boards.greenhouse.io/monzo/jobs/17',
                 'location': {'name': 'London'}, 'content': '2027 intake'},
                {'id': 19, 'internal_job_id': None, 'title': 'Prospect',
                 'absolute_url': 'https://job-boards.greenhouse.io/monzo/jobs/19',
                 'location': {'name': 'London'}}]}
        rows = collect_board({'company': 'Monzo', 'provider': 'greenhouse', 'board': 'monzo'}, fake)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].key, 'greenhouse:monzo:17')

    def test_lever_pagination_and_location(self):
        visited = []
        def fake(url):
            visited.append(url)
            return [{'id': 'abc', 'text': 'Software Engineer, New Grad',
                     'hostedUrl': 'https://jobs.lever.co/palantir/abc',
                     'categories': {'location': 'London, United Kingdom'},
                     'allLocations': ['London, United Kingdom']}]
        rows = collect_board({'company': 'Palantir', 'provider': 'lever', 'board': 'palantir'}, fake)
        self.assertEqual(len(visited), 1)
        self.assertIn('skip=0&limit=100', visited[0])
        self.assertEqual(rows[0].key, 'lever:palantir:abc')
        self.assertEqual(rows[0].location, 'London, United Kingdom')

    def test_ashby_hosted_url_and_other_countries(self):
        def fake(url):
            self.assertIn('job-board/incident?', url)
            return {'jobs': [
                {'title': 'Product Engineer Graduate', 'jobUrl': 'https://jobs.ashbyhq.com/incident/uuid-one',
                 'location': 'London', 'address': {'postalAddress': {'addressCountry': 'GB'}},
                 'isListed': True, 'compensation': {'compensationTierSummary': '£70k–£85k'}},
                {'title': 'Product Engineer', 'jobUrl': 'https://jobs.ashbyhq.com/incident/hidden',
                 'location': 'London', 'isListed': False}]}
        rows = collect_board({'company': 'incident.io', 'provider': 'ashby', 'board': 'incident'}, fake)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].key, 'ashby:incident:uuid-one')
        self.assertEqual(rows[0].salary, '£70k–£85k')

    def test_invalid_board_disallowed(self):
        with self.assertRaises(ValueError):
            collect_board({'company': 'Bad', 'provider': 'greenhouse', 'board': '../secret'}, lambda u: {})

    def test_schema_failure_is_not_empty(self):
        with self.assertRaises(ValueError):
            collect_board({'company': 'Monzo', 'provider': 'greenhouse', 'board': 'monzo'}, lambda u: {})


class RiverlaneURLTests(unittest.TestCase):
    entry = {'company': 'Riverlane', 'provider': 'greenhouse', 'board': 'riverlane', 'region': 'eu'}
    url = 'https://www.riverlane.com/jobs?gh_jid=17'

    def response(self, url=None, identifier=17):
        return {'jobs': [{'id': identifier, 'internal_job_id': 99, 'title': 'Graduate Engineer',
                          'absolute_url': self.url if url is None else url,
                          'location': {'name': 'Cambridge'}, 'content': 'Build systems'}],
                'meta': {'total': 1}}

    def test_custom_url_preserves_native_identity_and_original_url(self):
        for url in (self.url, self.url + '#vacancies'):
            with self.subTest(url=url):
                data = self.response(url)
                row = collect_board(self.entry, lambda _: data)[0]
                self.assertEqual(row.key, 'greenhouse:riverlane:17')
                self.assertEqual(row.url, url)
                self.assertEqual(row.raw, data['jobs'][0])
        for host in ('boards.eu.greenhouse.io', 'job-boards.eu.greenhouse.io'):
            old_url = f'https://{host}/riverlane/jobs/17'
            old = collect_board(self.entry, lambda _: self.response(old_url))[0]
            self.assertEqual(old.key, row.key)
            self.assertEqual(old.url, old_url)

    def test_custom_rule_requires_exact_board_and_eu_region(self):
        for changes in ({'board': 'monzo'}, {'board': 'other'}, {'board': 'Riverlane'},
                        {'region': 'global'}, {'region': 'us'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                collect_board({**self.entry, **changes}, lambda _: self.response())

    def test_spoofed_host_path_scheme_and_authority_fail_closed(self):
        urls = [
            'http://www.riverlane.com/jobs?gh_jid=17',
            'https://riverlane.com/jobs?gh_jid=17',
            'https://jobs.riverlane.com/jobs?gh_jid=17',
            'https://www.riverlane.com.evil.example/jobs?gh_jid=17',
            'https://www.riverlane.com@evil.example/jobs?gh_jid=17',
            'https://evil.example@www.riverlane.com/jobs?gh_jid=17',
            'https://www.riverlane.com%2eevil.example/jobs?gh_jid=17',
            'https://www.riverlane.com:8443/jobs?gh_jid=17',
            'https://www.riverlane.com/jobs/?gh_jid=17',
            'https://www.riverlane.com/Jobs?gh_jid=17',
            'https://www.riverlane.com/other/../jobs?gh_jid=17',
            'https://www.riverlane.com/%6aobs?gh_jid=17',
            self.url + '#other',
            self.url + '#%76acancies',
            self.url + '#vacancies#other',
            'https://www.riverlane.com/jobs\\?gh_jid=17',
        ]
        for url in urls:
            with self.subTest(url=url), self.assertRaises(ValueError):
                collect_board(self.entry, lambda _: self.response(url))

    def test_missing_wrong_duplicate_and_encoded_query_ids_fail_closed(self):
        # Even valid percent-encoded alternatives are outside this one canonical form.
        queries = ['', '?gh_jid=', '?id=17', '?gh_jid=18', '?gh_jid=017', '?gh_jid=abc',
                   '?gh_jid=17&gh_jid=17', '?gh_jid=17&gh_jid=18', '?gh_jid=17&other=1',
                   '?gh_jid=17&', '?gh_jid=17;gh_jid=18', '?gh%5Fjid=17', '?gh_jid=%31%37',
                   '?gh_jid=%', '?gh_jid=%1', '?gh_jid=%ZZ', '?gh_jid=%FF', '?gh_jid=17%00',
                   '?gh_jid=+17', '?gh_jid=%2531%2537', '?gh_jid=17%26gh_jid=17']
        for query in queries:
            with self.subTest(query=query), self.assertRaises(ValueError):
                collect_board(self.entry, lambda _: self.response('https://www.riverlane.com/jobs' + query))

    def test_bad_record_after_valid_record_is_not_a_partial_or_empty_board(self):
        data = self.response()
        data['jobs'] += self.response('https://www.riverlane.com/jobs?gh_jid=19', 18)['jobs']
        data['meta']['total'] = 2
        stats = {}
        with self.assertRaises(ValueError):
            collect_board(self.entry, lambda _: data, stats=stats)
        self.assertEqual(stats, {})
        self.assertEqual(collect_board(self.entry, lambda _: {'jobs': [], 'meta': {'total': 0}}), [])

    def test_duplicate_native_postings_remain_invalid(self):
        data = self.response()
        data['jobs'] *= 2
        data['meta']['total'] = 2
        with self.assertRaisesRegex(ValueError, 'duplicate posting identity'):
            collect_board(self.entry, lambda _: data)


if __name__ == '__main__':
    unittest.main()
