"""Standard-library BM25 (Okapi) for lexical search and as an evaluation baseline."""
from __future__ import annotations

from collections import Counter
import math
import re

from .filtering import plain

TOKEN = re.compile(r"[a-z0-9]+(?:[+#][+#]?)?")
STOP = frozenset('a an and are as at be by for from has in is it of on or our the to we will with you your'.split())


def tokens(value: str) -> list[str]:
    return [t for t in TOKEN.findall(plain(value or '').lower()) if t not in STOP]


class BM25:
    def __init__(self, documents: dict[str, str], k1=1.2, b=0.75):
        self.k1, self.b = k1, b
        self.docs = {key: Counter(tokens(body)) for key, body in documents.items()}
        self.length = {key: sum(c.values()) for key, c in self.docs.items()}
        self.avg = (sum(self.length.values()) / len(self.docs)) if self.docs else 0.0
        df = Counter(t for c in self.docs.values() for t in c)
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def score(self, key: str, query: list[str]) -> float:
        counts, length, total = self.docs[key], self.length[key], 0.0
        for term in query:
            f = counts.get(term, 0)
            if f:
                total += self.idf[term] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * length / (self.avg or 1)))
        return total

    def rank(self, query: str, limit=None) -> list[tuple[str, float]]:
        q = tokens(query)
        scored = [(key, self.score(key, q)) for key in self.docs]
        scored = sorted((s for s in scored if s[1] > 0), key=lambda s: (-s[1], s[0]))
        return scored[:limit] if limit else scored


def document(row: dict, description: str | None = None) -> str:
    fields = [row.get('title', ''), row.get('company', ''), row.get('location', ''), row.get('department', ''), row.get('team', '')]
    return ' '.join(fields + ([description] if description else []))
