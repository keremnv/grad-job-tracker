"""BM25 pre-tokenised index equivalence (offline, no database)."""
import unittest
from collections import Counter

from jobwatch.search import BM25, tokens


class BM25CountersTest(unittest.TestCase):
    DOCS = {
        'a': 'graduate software engineer python python',
        'b': 'senior accountant finance',
        'c': 'software engineer C++ distributed systems',
        'd': 'C# backend engineer',
        'e': '',
    }

    def test_from_counters_matches_text_index(self):
        text_index = BM25(self.DOCS)
        counter_index = BM25.from_counters({k: Counter(tokens(v)) for k, v in self.DOCS.items()})
        for query in ('software engineer', 'python', 'C++', 'engineer', 'absent-term'):
            self.assertEqual(text_index.rank(query, 3), counter_index.rank(query, 3), query)
            self.assertEqual(text_index.rank(query), counter_index.rank(query), query)

    def test_from_counters_empty(self):
        self.assertEqual(BM25.from_counters({}).rank('engineer'), [])


if __name__ == '__main__':
    unittest.main()
