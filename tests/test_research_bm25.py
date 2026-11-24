"""BM25 lexical search over fabricated rows (offline, no database)."""
import unittest
from uuid import uuid4

from jobwatch.postgres import research
from jobwatch.search import BM25, document, symbol_query_terms, symbol_search_text


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

    def test_symbol_search_text_and_query_terms_preserve_technical_tokens(self):
        self.assertIn('sym_cpp', symbol_search_text('C++20 Systems Engineer'))
        self.assertIn('sym_csharp', symbol_search_text('C#/.NET Developer'))
        self.assertIn('sym_dotnet', symbol_search_text('C#/.NET Developer'))
        self.assertIn('sym_fsharp', symbol_search_text('Functional F# Engineer'))
        self.assertIn('sym_dotnet', symbol_search_text('ASP.NET Core'))
        self.assertNotIn('sym_cpp', symbol_search_text('Embedded C Engineer'))
        self.assertNotIn('sym_csharp', symbol_search_text('Embedded C Engineer'))
        self.assertEqual(symbol_query_terms('C++ and C# graduate'), ['sym_cpp', 'sym_csharp', 'graduate'])
        self.assertEqual(symbol_query_terms('???'), [])

    def test_bm25_prefiltered_offline_uses_symbol_or_query_and_reranks(self):
        captured = []
        rows = [
            row('C++ Graduate Engineer', 'Write modern C++ systems'),
            row('C Embedded Engineer', 'Write bare metal C firmware'),
        ]

        class RecordingConn(FakeConn):
            def execute(self, sql, params=None):
                captured.append((sql, params))
                return super().execute(sql, params)

        conn = RecordingConn(rows)
        result = research._lexical_search(conn, 'C++ graduate', method='bm25_prefiltered', limit=5)
        self.assertEqual(result['ranking'], 'postgresql_gin_prefiltered_bm25')
        self.assertEqual(result['ranking_scope'], 'candidate_pool_rerank')
        self.assertEqual(result['candidate_pool_size'], 2)
        self.assertEqual(len(captured), 1)
        self.assertIn('search_sym_tsv', captured[0][0])
        self.assertEqual(captured[0][1][2], 'sym_cpp | graduate')
        self.assertEqual(result['items'][0]['title'], 'C++ Graduate Engineer')
        # Punctuation-only query never issues an invalid tsquery.
        conn2 = RecordingConn(rows)
        empty = research._lexical_search(conn2, '???', method='bm25_prefiltered', limit=5)
        self.assertEqual(empty['items'], [])
        self.assertEqual(conn2.calls, 0)


if __name__ == '__main__':
    unittest.main()
