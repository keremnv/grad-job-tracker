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


if __name__ == '__main__':
    unittest.main()
