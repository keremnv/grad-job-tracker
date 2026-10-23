"""Small SQL capability check with synthetic vectors; no model inference/quality claim."""
import unittest
from reference import connect, fixture, publish, register
from test_review import ReviewTests


class RetrievalTests(ReviewTests):
    # Run only this method, avoiding inheriting all the other tests in the command below.
    def test_current_vector_join_and_fts(self):
        self.initial([fixture(content='C++ and C# programming')])
        with connect() as c:
            v=c.execute('SELECT current_version_id FROM jobwatch.postings').fetchone()['current_version_id']
            e='['+','.join(['1']+['0']*383)+']'
            c.execute("INSERT INTO jobwatch.embedding_chunks(version_id,model_revision,input_recipe,chunker_revision,"
                "ordinal,input_sha256,chunk_text,embedding) VALUES (%s,'test@immutable','description-v1','one-v1',"
                "0,repeat('a',64),'C++ and C# programming',%s::vector)",(v,e))
            query="""SELECT p.posting_key,e.embedding <=> %s::vector AS distance
                     FROM jobwatch.postings p JOIN jobwatch.embedding_chunks e ON e.version_id=p.current_version_id
                     WHERE p.status='listed' AND e.model_revision='test@immutable'
                     AND e.input_recipe='description-v1' AND e.chunker_revision='one-v1'
                     ORDER BY distance LIMIT 10"""
            self.assertEqual(len(c.execute(query,(e,)).fetchall()),1)
            self.assertEqual(c.execute("SELECT to_tsvector('english','C++')=to_tsvector('english','C') AS same").fetchone()['same'],True)
        publish(register(self.source)['id'],[fixture(content='New source wording')])
        with connect() as c:
            self.assertEqual(len(c.execute(query,(e,)).fetchall()),0)
            self.assertEqual(c.execute('SELECT count(*) AS n FROM jobwatch.embedding_chunks').fetchone()['n'],1)


if __name__=='__main__':
    unittest.main(defaultTest='RetrievalTests.test_current_vector_join_and_fts',verbosity=2)
