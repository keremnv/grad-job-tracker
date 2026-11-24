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


class WorkableAndSlugTests(unittest.TestCase):
    def test_workable_single_and_multi_country_shortcode_grouping(self):
        payload = {
            'name': 'Stalis',
            'jobs': [
                {
                    'title': 'Data Science & AI Graduate',
                    'shortcode': '1C9CCEA6B4',
                    'employment_type': 'Full-time',
                    'telecommuting': True,
                    'department': None,
                    'function': 'Data Science',
                    'url': 'https://apply.workable.com/j/1C9CCEA6B4',
                    'shortlink': 'https://apply.workable.com/j/1C9CCEA6B4',
                    'application_url': 'https://apply.workable.com/j/1C9CCEA6B4/apply',
                    'published_on': '2026-11-23',
                    'created_at': '2026-11-18',
                    'country': 'Ireland',
                    'city': '',
                    'state': '',
                    'locations': [{'country': 'Ireland', 'countryCode': 'IE', 'city': '', 'region': None}],
                    'description': '<p>Python &amp; AI graduate role.</p>',
                },
                {
                    'title': 'Data Science & AI Graduate',
                    'shortcode': '1C9CCEA6B4',
                    'employment_type': 'Full-time',
                    'telecommuting': True,
                    'department': None,
                    'function': 'Data Science',
                    'url': 'https://apply.workable.com/j/1C9CCEA6B4',
                    'shortlink': 'https://apply.workable.com/j/1C9CCEA6B4',
                    'application_url': 'https://apply.workable.com/j/1C9CCEA6B4/apply',
                    'published_on': '2026-11-23',
                    'created_at': '2026-11-18',
                    'country': 'United Kingdom',
                    'city': '',
                    'state': '',
                    'locations': [{'country': 'United Kingdom', 'countryCode': 'GB', 'city': '', 'region': None}],
                    'description': '<p>Python &amp; AI graduate role.</p>',
                },
            ],
        }
        stats = {}
        rows = collect_board({'company': 'Stalis', 'provider': 'workable', 'board': 'stalis'}, lambda u: payload, stats=stats)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].key, 'workable:stalis:1C9CCEA6B4')
        self.assertEqual(rows[0].published_at, '2026-11-23')
        self.assertEqual(rows[0].team, 'Data Science')
        self.assertIn('United Kingdom', rows[0].countries)
        self.assertIn('Ireland', rows[0].countries)
        self.assertIn('Remote', rows[0].location)
        self.assertEqual(len(rows[0].raw['_widget_multi_location_entries']), 2)
        self.assertEqual(stats, {'skipped_non_vacancies': 0, 'source_records': 1})

    def test_workable_conflicting_duplicate_shortcode_fails_closed(self):
        payload = {
            'name': 'BadCo',
            'jobs': [
                {'title': 'Role A', 'shortcode': 'ABC123', 'url': 'https://apply.workable.com/j/ABC123'},
                {'title': 'Role B', 'shortcode': 'ABC123', 'url': 'https://apply.workable.com/j/ABC123'},
            ],
        }
        with self.assertRaisesRegex(ValueError, 'conflicting duplicate Workable shortcode'):
            collect_board({'company': 'BadCo', 'provider': 'workable', 'board': 'badco'}, lambda _: payload)

    def test_workable_url_and_region_validation_fail_closed(self):
        bad_url = {'jobs': [{'title': 'SWE', 'shortcode': 'ABC123', 'url': 'https://evil.example/j/ABC123'}]}
        with self.assertRaises(ValueError):
            collect_board({'company': 'Bad', 'provider': 'workable', 'board': 'bad'}, lambda _: bad_url)
        with self.assertRaises(ValueError):
            collect_board({'company': 'Bad', 'provider': 'workable', 'board': 'bad', 'region': 'eu'}, lambda _: {'jobs': []})

    def test_ashby_dotted_and_mixed_case_board_slugs(self):
        payload_snyk = {'jobs': [{'title': 'AI Engineer', 'jobUrl': 'https://jobs.ashbyhq.com/Snyk/uuid-1', 'location': 'London', 'isListed': True}]}
        rows = collect_board({'company': 'Snyk', 'provider': 'ashby', 'board': 'snyk'}, lambda _: payload_snyk)
        self.assertEqual(rows[0].key, 'ashby:snyk:uuid-1')
        payload_iwoca = {'jobs': [{'title': 'Backend Engineer', 'jobUrl': 'https://jobs.ashbyhq.com/iwoca.co.uk/uuid-2', 'location': 'London', 'isListed': True}]}
        rows2 = collect_board({'company': 'iwoca', 'provider': 'ashby', 'board': 'iwoca.co.uk'}, lambda _: payload_iwoca)
        self.assertEqual(rows2[0].key, 'ashby:iwoca.co.uk:uuid-2')
        for bad_slug in ('..', '.hidden', 'trailing.', 'a..b', 'a/b'):
            with self.subTest(bad_slug=bad_slug), self.assertRaises(ValueError):
                collect_board({'company': 'Bad', 'provider': 'ashby', 'board': bad_slug}, lambda _: {'jobs': []})


class CustomGreenhouseHostTests(unittest.TestCase):
    def _resp(self, url, identifier=8172510):
        return {
            'jobs': [{
                'id': identifier,
                'internal_job_id': 99,
                'title': 'Graduate Software Engineer',
                'absolute_url': url,
                'location': {'name': 'London, UK'},
                'content': 'Synthetic role',
            }],
            'meta': {'total': 1},
        }

    def test_verified_custom_hosts_accept_canonical_and_hosted_urls(self):
        cases = [
            ({'company': 'Stripe', 'provider': 'greenhouse', 'board': 'stripe', 'region': 'global'},
             8172510,
             ['https://stripe.com/jobs/search?gh_jid=8172510',
              'https://job-boards.greenhouse.io/stripe/jobs/8172510']),
            ({'company': 'Jane Street', 'provider': 'greenhouse', 'board': 'janestreet', 'region': 'global'},
             8631912002,
             ['https://www.janestreet.com/join-jane-street/apply/8631912002?gh_jid=8631912002',
              'https://www.janestreet.com/join-jane-street/apply/8631912002/?gh_jid=8631912002',
              'https://boards.greenhouse.io/janestreet/jobs/8631912002']),
            ({'company': 'Ocado Group', 'provider': 'greenhouse', 'board': 'ocadogroup', 'region': 'eu'},
             4973939101,
             ['https://careers.ocadogroup.com/jobs/details?gh_jid=4973939101&gh_jid=4973939101',
              'https://careers.ocadogroup.com/jobs/details?gh_jid=4973939101',
              'https://job-boards.eu.greenhouse.io/ocadogroup/jobs/4973939101']),
        ]
        for entry, native_id, urls in cases:
            for url in urls:
                with self.subTest(board=entry['board'], url=url):
                    rows = collect_board(entry, lambda _, u=url, nid=native_id: self._resp(u, nid))
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0].key, f"greenhouse:{entry['board']}:{native_id}")
                    self.assertEqual(rows[0].url, url)

    def test_custom_hosts_fail_closed_on_mismatches_spoofing_and_wrong_board_or_region(self):
        bad_cases = [
            # Stripe: wrong host, extra param, wrong region, mismatched ID, fragment
            ({'company': 'Stripe', 'provider': 'greenhouse', 'board': 'stripe', 'region': 'eu'},
             8172510, 'https://stripe.com/jobs/search?gh_jid=8172510'),
            ({'company': 'Stripe', 'provider': 'greenhouse', 'board': 'monzo', 'region': 'global'},
             8172510, 'https://stripe.com/jobs/search?gh_jid=8172510'),
            ({'company': 'Stripe', 'provider': 'greenhouse', 'board': 'stripe', 'region': 'global'},
             8172510, 'https://stripe.com/jobs/search?gh_jid=8172510&extra=1'),
            ({'company': 'Stripe', 'provider': 'greenhouse', 'board': 'stripe', 'region': 'global'},
             8172510, 'https://stripe.com.evil.example/jobs/search?gh_jid=8172510'),
            ({'company': 'Stripe', 'provider': 'greenhouse', 'board': 'stripe', 'region': 'global'},
             8172510, 'https://stripe.com/jobs/search?gh_jid=9999999'),
            # Jane Street: path ID != query ID, extra param, wrong host, wrong region
            ({'company': 'Jane Street', 'provider': 'greenhouse', 'board': 'janestreet', 'region': 'global'},
             8631912002, 'https://www.janestreet.com/join-jane-street/apply/8631912002?gh_jid=8631912003'),
            ({'company': 'Jane Street', 'provider': 'greenhouse', 'board': 'janestreet', 'region': 'global'},
             8631912002, 'https://www.janestreet.com/join-jane-street/apply/8631912003?gh_jid=8631912002'),
            ({'company': 'Jane Street', 'provider': 'greenhouse', 'board': 'janestreet', 'region': 'global'},
             8631912002, 'https://www.janestreet.com/join-jane-street/apply/8631912002?gh_jid=8631912002&foo=1'),
            ({'company': 'Jane Street', 'provider': 'greenhouse', 'board': 'janestreet', 'region': 'eu'},
             8631912002, 'https://www.janestreet.com/join-jane-street/apply/8631912002?gh_jid=8631912002'),
            # Ocado: mismatched duplicate ID, triple duplicate, wrong region, spoofed host
            ({'company': 'Ocado Group', 'provider': 'greenhouse', 'board': 'ocadogroup', 'region': 'eu'},
             4973939101, 'https://careers.ocadogroup.com/jobs/details?gh_jid=4973939101&gh_jid=4973939102'),
            ({'company': 'Ocado Group', 'provider': 'greenhouse', 'board': 'ocadogroup', 'region': 'eu'},
             4973939101, 'https://careers.ocadogroup.com/jobs/details?gh_jid=4973939101&gh_jid=4973939101&gh_jid=4973939101'),
            ({'company': 'Ocado Group', 'provider': 'greenhouse', 'board': 'ocadogroup', 'region': 'global'},
             4973939101, 'https://careers.ocadogroup.com/jobs/details?gh_jid=4973939101&gh_jid=4973939101'),
            ({'company': 'Ocado Group', 'provider': 'greenhouse', 'board': 'ocadogroup', 'region': 'eu'},
             4973939101, 'https://careers.ocadogroup.com.evil.example/jobs/details?gh_jid=4973939101'),
        ]
        for entry, native_id, url in bad_cases:
            with self.subTest(board=entry['board'], url=url), self.assertRaises(ValueError):
                collect_board(entry, lambda _, u=url, nid=native_id: self._resp(u, nid))


if __name__ == '__main__':
    unittest.main()
