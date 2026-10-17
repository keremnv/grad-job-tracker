import unittest
from jobwatch.feeds import Posting
from jobwatch.filtering import classify
from jobwatch.state import merge


def post(identifier='a', title='Product Engineer Graduate', location='London', country='GB'):
    return Posting('incident.io', 'ashby', 'incident', identifier, title, location,
                   'https://jobs.ashbyhq.com/incident/' + identifier, country=country)


class FilterTests(unittest.TestCase):
    def test_early_uk(self):
        self.assertEqual(classify(post())[0], 'priority')
        self.assertEqual(classify(post(title='Backend Engineer'))[0], 'review')

    def test_senior_intern_and_us_excluded(self):
        for title in ('Senior Engineer', 'Engineering Manager', 'Software Engineer Intern'):
            self.assertEqual(classify(post(title=title))[0], 'excluded')
        self.assertEqual(classify(post(country='USA'))[0], 'review')
        self.assertEqual(classify(post(location='New York', country=''))[0], 'excluded')

    def test_non_engineering_excluded(self):
        self.assertEqual(classify(post(title='Sales Graduate'))[0], 'excluded')


class StateTests(unittest.TestCase):
    def setUp(self):
        self.state = {'schema': 1, 'postings': {}}
        self.board = 'ashby:incident'

    def test_new_stable_missing_reappeared(self):
        state, events = merge(self.state, {self.board: [post()]}, '2026-10-17T00:00:00Z')
        self.assertEqual([x['type'] for x in events], ['new'])
        state, events = merge(state, {self.board: [post()]}, '2026-10-18T00:00:00Z')
        self.assertEqual(events, [])
        self.assertEqual(state['postings']['ashby:incident:a']['first_seen'], '2026-10-17T00:00:00Z')
        state, events = merge(state, {self.board: []}, '2026-10-19T00:00:00Z')
        self.assertEqual([x['type'] for x in events], ['not_seen'])
        self.assertEqual(state['postings']['ashby:incident:a']['feed_status'], 'not_seen')
        state, events = merge(state, {self.board: [post()]}, '2026-10-20T00:00:00Z')
        self.assertEqual([x['type'] for x in events], ['reappeared'])
        self.assertNotIn('missing_since', state['postings']['ashby:incident:a'])

    def test_failing_board_cannot_change_existing(self):
        state, _ = merge(self.state, {self.board: [post()]}, '2026-10-17T00:00:00Z')
        state, events = merge(state, {}, '2026-10-18T00:00:00Z')
        self.assertEqual(events, [])
        self.assertEqual(state['postings']['ashby:incident:a']['feed_status'], 'listed')
        self.assertEqual(state['postings']['ashby:incident:a']['last_seen'], '2026-10-17T00:00:00Z')

    def test_title_change_reported(self):
        state, _ = merge(self.state, {self.board: [post()]}, '2026-10-17T00:00:00Z')
        state, events = merge(state, {self.board: [post(title='Software Engineer Graduate')]},
                              '2026-10-18T00:00:00Z')
        self.assertEqual([x['type'] for x in events], ['changed'])

    def test_no_false_closure(self):
        state, events = merge(self.state, {self.board: [post()]}, '2026-10-17T00:00:00Z')
        state, events = merge(state, {self.board: []}, '2026-10-18T00:00:00Z')
        self.assertNotEqual(state['postings']['ashby:incident:a']['feed_status'], 'closed')


if __name__ == '__main__':
    unittest.main()
