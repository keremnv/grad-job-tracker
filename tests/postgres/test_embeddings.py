"""Production worker/search/migration integration, only verified disposable PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import unittest
from uuid import uuid4

from jobwatch.postgres.connection import connect
from jobwatch.postgres.embeddings import DEFAULT, EmbeddingError, LocalEncoder, chunks, index_missing, query_text, write_chunks
from jobwatch.postgres.migrate import MigrationError, load_migrations, migrate
from jobwatch.postgres import research
from support import DatabaseTest, actor, version, source, run, posting


def unit(axis=0, dimensions=768):
    values=[0.0]*dimensions;values[axis]=1.0;return values


class Tokenizer:
    def __call__(self, text, *, add_special_tokens=True, return_offsets_mapping=False):
        return {'input_ids':list(range(len(text)+(2 if add_special_tokens else 0))),
                'offset_mapping':[(i,i+1) for i in range(len(text))]}


class Encoder:
    tokenizer=Tokenizer()
    def encode(self,texts):return [unit() for _ in texts]


class EmbeddingTests(DatabaseTest):
    def retained(self,vid):
        with self.target.connect('jobwatch_owner') as conn:
            return conn.execute('SELECT * FROM jobwatch.posting_versions WHERE id=%s',(vid,)).fetchone()

    def embed(self,vid,axis=0,profile=DEFAULT):
        v=self.retained(vid);items=chunks(v,Tokenizer())
        with self.target.connect('jobwatch_embedding_writer') as conn:
            return write_chunks(conn,v,items,[unit(axis,profile.dimensions) for _ in items],profile=profile)

    def test_insert_replay_source_hashes_and_concurrent_workers(self):
        _,vid,_=self.seed(description='Python systems')
        v=self.retained(vid);items=chunks(v,Tokenizer())
        def publish(_):
            with self.target.connect('jobwatch_embedding_writer') as conn:return write_chunks(conn,v,items,[unit()]*len(items))
        with ThreadPoolExecutor(2) as pool:counts=list(pool.map(publish,range(2)))
        self.assertEqual(sum(counts),len(items));self.assertEqual(publish(0),0)
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.posting_events'),0)
        self.assertEqual(self.scalar('SELECT profile_id FROM jobwatch.embedding_chunks LIMIT 1'),DEFAULT.key)
        broken={**v,'normalized_sha256':'0'*64}
        with self.target.connect('jobwatch_embedding_writer') as conn:
            with self.assertRaisesRegex(EmbeddingError,'source_hash_mismatch'):write_chunks(conn,broken,items,[unit()]*len(items))

    def test_mid_transaction_rollback_and_existing_input_conflict(self):
        _,vid,_=self.seed(description='Long text '*400)
        v=self.retained(vid);items=chunks(v,Tokenizer(),max_tokens=180,overlap=20)
        values=[unit() for _ in items];values[1]=[0]*768
        with self.target.connect('jobwatch_embedding_writer') as conn:
            with self.assertRaisesRegex(EmbeddingError,'invalid_vector'):write_chunks(conn,v,items,values)
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.embedding_chunks'),0)
        self.embed(vid)
        altered=chunks(v,Tokenizer());altered[0]['input_text']+=' changed'
        altered[0]['input_sha256']=sha256(altered[0]['input_text'].encode()).hexdigest()
        with self.target.connect('jobwatch_embedding_writer') as conn:
            with self.assertRaisesRegex(EmbeddingError,'embedding_conflict'):write_chunks(conn,v,altered,[unit()]*len(altered))

    def test_exact_cosine_exclusions_missing_coverage_and_lexical_fallback(self):
        a,one,_=self.seed(native_id='1',description='Python services')
        b,two,_=self.seed(native_id='2',description='C++ systems')
        c,_,_=self.seed(native_id='3',description='Python graduate')
        with self.target.connect() as conn:conn.execute("UPDATE jobwatch.postings SET verdict='excluded' WHERE posting_key=%s",(a,))
        self.embed(one);self.embed(two,axis=1)
        with self.target.connect('jobwatch_owner') as conn:
            result=research.search(conn,'Python',method='vector',query_vector=unit())
            self.assertEqual([r['posting_key'] for r in result['items']],[a,b])
            self.assertAlmostEqual(result['items'][0]['cosine_distance'],0)
            self.assertAlmostEqual(result['items'][1]['cosine_distance'],1)
            self.assertEqual(result['embedding_coverage']['missing_versions'],1)
            hybrid=research.search(conn,'Python',method='hybrid',query_vector=unit())
            self.assertIn(c,[r['posting_key'] for r in hybrid['items']])
            self.assertEqual(hybrid['ranking'],'reciprocal_rank_fusion')
            ranks=hybrid['items'][0]['component_ranks']
            lexical=research.search(conn,'Python')['items']
            self.assertEqual(ranks,{'lexical':next(i for i,r in enumerate(lexical,1) if r['posting_key']==a),'vector':1})
            self.assertEqual(research.search(conn,'C++')['items'][0]['posting_key'],b)

    def test_new_version_late_worker_history_not_seen_and_other_profiles(self):
        key,before,attempt=self.seed(description='old Python')
        with self.target.connect('jobwatch_collector') as conn,conn.transaction():
            actor(conn);after=version(conn,key,self.source,attempt['id'],description='new Python')
            conn.execute('UPDATE jobwatch.postings SET current_version_id=%s WHERE posting_key=%s',(after,key))
        self.embed(before)  # A worker finishing late must not appear current.
        with self.target.connect('jobwatch_owner') as conn:
            self.assertEqual(research.search(conn,'Python',method='vector',query_vector=unit())['items'],[])
            historic=research.search(conn,'Python',method='vector',query_vector=unit(),history=True)
            self.assertFalse(historic['items'][0]['is_current'])
        profile=replace(DEFAULT,model_id='synthetic/another',dimensions=3)
        self.embed(after,profile=profile)
        with self.target.connect('jobwatch_owner') as conn:
            self.assertEqual(research.search(conn,'Python',method='vector',query_vector=unit())['items'],[])
            self.assertEqual(len(research.search(conn,'Python',method='vector',profile=profile,query_vector=unit(dimensions=3))['items']),1)
        self.embed(after)
        with self.target.connect('jobwatch_collector') as conn:
            conn.execute("UPDATE jobwatch.postings SET status='not_seen',missing_since=now() WHERE posting_key=%s",(key,))
        with self.target.connect('jobwatch_owner') as conn:
            self.assertEqual(research.search(conn,'Python',method='vector',query_vector=unit())['items'],[])
            self.assertEqual(len(research.search(conn,'Python',method='vector',query_vector=unit(),include_not_seen=True)['items']),1)

    def test_worker_discovers_missing_and_repairs_only_matching_partial_sets(self):
        self.seed(evidence={'id':'1','description':'Python\0evidence '*200})
        with self.target.connect('jobwatch_embedding_writer') as conn:
            with conn.transaction():
                with self.assertRaisesRegex(EmbeddingError,'idle_connection_required'):index_missing(conn,Encoder())
            self.assertGreater(index_missing(conn,Encoder())['inserted_chunks'],1)
            self.assertEqual(index_missing(conn,Encoder())['versions'],0)
            conn.execute('DELETE FROM jobwatch.embedding_chunks WHERE ordinal=1')
            self.assertGreater(index_missing(conn,Encoder())['inserted_chunks'],0)
        raw=self.scalar('SELECT evidence FROM jobwatch.posting_versions')
        self.assertIn('\0',json.loads(bytes(raw))['description'])

    def test_worker_owner_collector_database_privileges(self):
        self.seed()
        import psycopg
        queries=('SELECT * FROM jobwatch.applications','SELECT * FROM jobwatch.owner_audit',
                 'INSERT INTO jobwatch.collection_runs DEFAULT VALUES','UPDATE jobwatch.postings SET verdict=verdict',
                 'UPDATE jobwatch.posting_versions SET title=title','CREATE TABLE jobwatch.forbidden(id int)',
                 'UPDATE jobwatch.embedding_chunks SET ordinal=ordinal')
        for query in queries:self.rejected(psycopg.errors.InsufficientPrivilege,query,user='jobwatch_embedding_writer')
        for role in ('jobwatch_owner','jobwatch_collector'):
            self.rejected(psycopg.errors.InsufficientPrivilege,'DELETE FROM jobwatch.embedding_chunks',user=role)

    def test_dimension_zero_nonfinite_fk_and_hash_constraints(self):
        _,vid,_=self.seed();self.embed(vid)
        import psycopg
        for expr in ("'[0,0,0]'::public.vector","'[NaN]'::public.vector","'[1,0,0]'::public.vector"):
            self.rejected(psycopg.Error,'UPDATE jobwatch.embedding_chunks SET embedding='+expr)
        self.rejected(psycopg.errors.CheckViolation,"UPDATE jobwatch.embedding_chunks SET input_text='tampered'")
        self.rejected(psycopg.errors.ForeignKeyViolation,'UPDATE jobwatch.embedding_chunks SET version_id=%s',params=(uuid4(),))

    def test_missing_extension_rolls_back_and_lexical_prevector_still_works(self):
        from psycopg.conninfo import make_conninfo
        name='jobwatch_no_vector_'+uuid4().hex
        container=os.environ['JOBWATCH_PG_TEST_CONTAINER']
        subprocess.run(['docker','exec',container,'createdb','-U','postgres','-O','jobwatch_migrator','-T','template0',name],check=True)
        config=replace(self.target.config(),dsn=make_conninfo(self.target.config().dsn,dbname=name))
        try:
            with connect(config) as conn:
                with self.assertRaisesRegex(MigrationError,'0004'):migrate(conn)
                self.assertIsNone(conn.execute("SELECT to_regnamespace('jobwatch') AS n").fetchone()['n'])
                migrate(conn,load_migrations()[:3])
                with conn.transaction():
                    actor(conn)
                    sid=source(conn);attempt=run(conn,sid)
                    key,vid=posting(conn,sid,attempt['id'],description='Python source before vector migration')
                    conn.execute("INSERT INTO jobwatch.applications(job_key,posting_key,company,role,url,notes) "
                                 "VALUES ('manual:upgrade',%s,'Synthetic','Graduate','https://example.org/upgrade','synthetic private note')",(key,))
                tables=('postings','posting_versions','collection_runs','applications','owner_audit')
                before={t:conn.execute('SELECT to_jsonb(t) AS data FROM jobwatch.'+t+' t ORDER BY to_jsonb(t)::text').fetchall() for t in tables}
                result=research.search(conn,'Python',method='fts')
                self.assertFalse(result['embedding_coverage']['available'])
                self.assertEqual(len(result['items']),1)
                subprocess.run(['docker','exec','-i',container,'psql','-X','-U','postgres','-d',name,'-v','ON_ERROR_STOP=1','-f','-'],
                               input=(Path(__file__).resolve().parents[2]/'sql/postgresql/vector_extension.sql').read_bytes(),
                               capture_output=True,check=True)
                self.assertEqual(migrate(conn),['0004_embeddings.sql'])
                self.assertEqual(migrate(conn),[])
                after={t:conn.execute('SELECT to_jsonb(t) AS data FROM jobwatch.'+t+' t ORDER BY to_jsonb(t)::text').fetchall() for t in tables}
                self.assertEqual(before,after)
                self.assertEqual(conn.execute('SELECT count(*) AS n FROM jobwatch.embedding_chunks').fetchone()['n'],0)
        finally:subprocess.run(['docker','exec',container,'dropdb','-U','postgres',name],check=True)

    @unittest.skipUnless(os.environ.get('JOBWATCH_REAL_MODEL_TEST')=='1','Explicit local cached real-model smoke test')
    def test_real_google_float32_text_only_worker_and_search(self):
        software,_,_=self.seed(native_id='1',title='Graduate Software Engineer',description='Build Python software systems and distributed services.')
        self.seed(native_id='2',title='Graduate Landscape Gardener',description='Grow flowers, prune hedges and plant trees.')
        encoder=LocalEncoder()
        with self.target.connect('jobwatch_embedding_writer') as conn:self.assertEqual(index_missing(conn,encoder)['versions'],2)
        query=encoder.encode([query_text('graduate Python programming jobs')])[0]
        with self.target.connect('jobwatch_owner') as conn:
            result=research.search(conn,'graduate Python programming jobs',method='vector',query_vector=query)
            self.assertEqual(result['items'][0]['posting_key'],software)
            self.assertEqual(result['embedding_coverage']['missing_versions'],0)
