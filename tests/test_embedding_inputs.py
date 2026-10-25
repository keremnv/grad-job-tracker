"""Offline retrieval contracts: no model, Docker, live data or optional packages."""
from hashlib import sha256
import subprocess
import unittest
from unittest.mock import patch

from jobwatch.postgres.embeddings import DEFAULT, EmbeddingError, body_text, chunks, query_embedding, query_text, vector_text


class CharacterTokenizer:
    def __call__(self, text, *, add_special_tokens=True, return_offsets_mapping=False):
        return {'input_ids': list(range(len(text)+(2 if add_special_tokens else 0))),
                'offset_mapping': [(i,i+1) for i in range(len(text))]}


class EmbeddingInputTests(unittest.TestCase):
    def test_google_real_title_query_and_complete_long_document(self):
        version = {'title': 'Graduate C++ Engineer', 'description_text': 'αβ\0' + 'long text '*500 + 'final evidence'}
        original = version.copy()
        result = chunks(version, CharacterTokenizer(), max_tokens=180, overlap=20)
        body = body_text(version)
        self.assertGreater(len(result), 2)
        self.assertEqual(result[0]['start_char'],0)
        self.assertEqual(result[-1]['end_char'],len(body))
        self.assertIn('final evidence',result[-1]['input_text'])
        self.assertTrue(all(b['start_char'] <= a['end_char'] for a,b in zip(result,result[1:])))
        for item in result:
            self.assertTrue(item['input_text'].startswith('title: Graduate C++ Engineer | text: '))
            self.assertEqual(item['input_sha256'],sha256(item['input_text'].encode()).hexdigest())
            self.assertLessEqual(len(CharacterTokenizer()(item['input_text'])['input_ids']),180)
        self.assertEqual(version,original)
        self.assertEqual(query_text('systems roles'),'task: search result | query: systems roles')

    def test_unknown_empty_and_oversized_title_fail_honestly(self):
        a=chunks({'title':'Role','description_text':None},CharacterTokenizer())
        b=chunks({'title':'Role','description_text':''},CharacterTokenizer())
        self.assertNotEqual(a[0]['input_sha256'],b[0]['input_sha256'])
        with self.assertRaisesRegex(EmbeddingError,'title_too_long'):
            chunks({'title':'x'*2000},CharacterTokenizer())

    def test_dimensions_nonfinite_and_zero_vectors(self):
        for v in ([],[0]*768,[float('nan')]*768,[float('inf')]*768,['bad']*768):
            with self.assertRaises(EmbeddingError):vector_text(v,768)
        self.assertEqual(len(DEFAULT.model_revision),40)
        self.assertEqual(DEFAULT.dimensions,768)

    def test_inference_timeout_closes_process_boundary_and_releases_slot(self):
        with patch('jobwatch.postgres.embeddings.subprocess.run',side_effect=subprocess.TimeoutExpired('fixture',60)) as run:
            for _ in range(2):
                with self.assertRaisesRegex(EmbeddingError,'embedding_timeout'):query_embedding('Python')
            self.assertEqual(run.call_count,2)
        with patch('jobwatch.postgres.embeddings.subprocess.run') as run:
            with self.assertRaisesRegex(EmbeddingError,'invalid_query'):query_embedding('\0')
            run.assert_not_called()
