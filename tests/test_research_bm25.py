"""BM25 lexical search over fabricated rows (offline, no database)."""
import unittest
from uuid import uuid4

from jobwatch.postgres import research
from jobwatch.search import BM25, document


def row(title, description):
    return {'version_id': uuid4(), 'posting_key': 'x:1', 'is_current': True,
            'origin': 'collector', 'normalizer_version': 'v1', 'version_observed_at': None,
            'evidence_sha256': '0' * 64, 'company': 'Co', 'title': title, 'posting_url': None,
            'location_text': 'London', 'department': '', 'team': '',
            'description_text': description, 'status': 'listed', 'verdict': 'review',
            'last_seen_at': None, 'last_success_at': None, 'stale': False,
            'last_attempt_status': 'published', 'absence_safe': True}


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def execute(self, _sql, _params=None):
        self.calls += 1
        return FakeResult([dict(r) for r in self.rows])


class LexicalBm25Tests(unittest.TestCase):
    def test_ranking_and_snippets_match_text_index(self):
        rows = [row('Engineer', 'graduate software engineer python'),
                row('Accountant', 'senior accountant finance'),
                row('Engineer 2', 'software engineer distributed systems')]
        conn = FakeConn(rows)
        result = research._lexical_search(conn, 'software engineer', method='bm25', limit=2)
        docs = {str(r['version_id']): document({**r, 'location': r['location_text']}, r['description_text'])
                for r in rows}
        expected = [key for key, _ in BM25(docs).rank('software engineer', 2)]
        self.assertEqual([str(i['version_id']) for i in result['items']], expected)
        self.assertEqual(conn.calls, 1)  # No post-ranking query inside the transaction.
        for item in result['items']:
            self.assertNotIn('description_text', item)
            self.assertIn('software', item['untrusted_match']['text'].lower())


if __name__ == '__main__':
    unittest.main()
