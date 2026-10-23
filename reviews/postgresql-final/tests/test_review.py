"""Run only against the disposable loopback container; no production DSN accepted."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import threading
import time
import unittest
from uuid import uuid4

import psycopg
from psycopg.types.json import Jsonb

from reference import actor, compare, connect, encoded, event, fixture, normalize, publish, register, version
from jobwatch.identity import loose_url, exact_url


class ReviewTests(unittest.TestCase):
    def setUp(self):
        with connect() as c, c.transaction():
            actor(c)
            names = c.execute("SELECT tablename FROM pg_tables WHERE schemaname='jobwatch'").fetchall()
            c.execute('TRUNCATE '+','.join('jobwatch.'+r['tablename'] for r in names)+' RESTART IDENTITY CASCADE')
            c.execute("INSERT INTO jobwatch.employers(slug,name) VALUES ('test','Test')")
            self.source = c.execute("INSERT INTO jobwatch.sources(provider,board,employer_id,status,validation_url,"
                "endpoint_checked_at,mapping_verified_at,mapping_verified_by,mapping_evidence) "
                "VALUES ('greenhouse','test',1,'monitored','https://example.com',now(),now(),'test','official page') RETURNING id"
            ).fetchone()['id']

    def scalar(self, sql, params=()):
        with connect() as c:
            return next(iter(c.execute(sql,params).fetchone().values()))

    def initial(self, rows=None):
        run = register(self.source)
        self.assertEqual(publish(run['id'],rows if rows is not None else [fixture()]), 'published')
        return run

    def wait_for_source_lock(self):
        deadline=time.monotonic()+3
        with connect() as c:
            while time.monotonic()<deadline:
                if c.execute("SELECT count(*) AS n FROM pg_stat_activity WHERE datname='final_review' "
                    "AND wait_event_type='Lock' AND query LIKE 'SELECT * FROM jobwatch.sources%'").fetchone()['n']:
                    return
                time.sleep(.01)
        self.fail('second publisher did not demonstrably wait on the source lock')

    def rejected(self, sql, user='postgres', params=()):
        with connect(user) as c:
            with self.assertRaises(psycopg.Error):
                with c.transaction():
                    actor(c)
                    c.execute(sql,params if params else None)

    def test_identity_and_relationship_constraints(self):
        run = self.initial([fixture('1'),fixture('2')])
        self.rejected("INSERT INTO jobwatch.sources(provider,board,region) VALUES ('greenhouse','Test','eu')")
        self.rejected("INSERT INTO jobwatch.sources(provider,board,status) VALUES ('lever','bad','monitored')")
        vid = self.scalar("SELECT current_version_id FROM jobwatch.postings WHERE native_id='1'")
        self.rejected("UPDATE jobwatch.postings SET current_version_id=%s WHERE native_id='2'", params=(vid,))
        self.rejected("UPDATE jobwatch.postings SET posting_key='greenhouse:other:1' WHERE native_id='1'")
        self.rejected("INSERT INTO jobwatch.posting_events(posting_key,source_id,observed_at,category,kind,after_version_id) "
                      "VALUES ('greenhouse:test:2',1,now(),'import','legacy_event',%s)",params=(vid,))
        self.rejected('DELETE FROM jobwatch.posting_versions WHERE id=%s',params=(vid,))
        self.rejected("UPDATE jobwatch.postings SET status='not_seen' WHERE native_id='1'")
        with connect() as c, c.transaction():
            actor(c)
            other = c.execute("INSERT INTO jobwatch.sources(provider,board) VALUES ('lever','other') RETURNING id").fetchone()['id']
        self.rejected('UPDATE jobwatch.sources SET last_published_run_id=%s WHERE id=%s',params=(run['id'],other))

    def test_role_boundaries_and_required_actor(self):
        self.initial()
        for sql in ('SELECT * FROM jobwatch.applications','SELECT * FROM jobwatch.triage',
                    'SELECT * FROM jobwatch.verifications','SELECT * FROM jobwatch.owner_audit',
                    'SELECT notes FROM jobwatch.employers',
                    "UPDATE jobwatch.posting_versions SET description_text='forged'",
                    'DELETE FROM jobwatch.posting_events'):
            self.rejected(sql,'jobwatch_collector')
        for sql in ('CREATE TABLE jobwatch.bad(id int)','CREATE TABLE public.bad(id int)',
                    'DELETE FROM jobwatch.owner_audit','ALTER TABLE jobwatch.triage DISABLE TRIGGER ALL',
                    "UPDATE jobwatch.postings SET verdict='priority'",
                    'UPDATE jobwatch.sources SET last_published_run_id=NULL',
                    "INSERT INTO jobwatch.sources(provider,board,last_published_run_id) VALUES ('lever','bad',NULL)"):
            self.rejected(sql,'jobwatch_owner')
        with connect('jobwatch_owner') as c:
            with self.assertRaises(psycopg.Error):
                c.execute("INSERT INTO jobwatch.triage(posting_key,decision,recorded_by) "
                          "VALUES ('greenhouse:test:1','interested','owner')")
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.triage'),0)

    def test_audit_attribution_multirow_and_rollback(self):
        self.initial([fixture('1'),fixture('2')])
        before = self.scalar('SELECT count(*) FROM jobwatch.owner_audit')
        with connect('jobwatch_owner') as c, c.transaction():
            actor(c,'agent:review/session-1')
            c.execute("INSERT INTO jobwatch.triage(posting_key,decision,recorded_by) VALUES "
                      "('greenhouse:test:1','interested','agent_on_owner_instruction'),"
                      "('greenhouse:test:2','later','agent_on_owner_instruction')")
        with connect() as c:
            rows = c.execute("SELECT db_login,actor,transaction_id FROM jobwatch.owner_audit WHERE table_name='triage'").fetchall()
        self.assertEqual(len(rows),2)
        self.assertEqual({r['db_login'] for r in rows},{'jobwatch_owner'})
        self.assertEqual({r['actor'] for r in rows},{'agent:review/session-1'})
        self.assertEqual(len({r['transaction_id'] for r in rows}),1)
        with connect('jobwatch_owner') as c:
            with self.assertRaises(psycopg.errors.DivisionByZero):
                with c.transaction():
                    actor(c)
                    c.execute("UPDATE jobwatch.triage SET decision='dismissed'")
                    self.assertEqual(c.execute('SELECT count(*) AS n FROM jobwatch.owner_audit').fetchone()['n'],before+4)
                    c.execute('SELECT 1/0')
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.owner_audit'),before+2)
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.triage WHERE decision='dismissed'"),0)

    def test_url_identity_and_manual_application_link(self):
        urls = ['https://Jobs.Lever.co/palantir/abc','https://example.com:443/x',
                'https://example.com/a?b=1#c','https://example.com/a#c?d','https://example.com?x=1',
                'https://EXAMPLE.com/Path?Q=A','https://[::1]:443/a','https://example.com/a@b',
                'https://bücher.example/x']
        with connect() as c:
            for u in urls:
                self.assertEqual(c.execute('SELECT jobwatch.url_identity(%s,false) AS u',(u,)).fetchone()['u'],loose_url(u))
                self.assertEqual(c.execute('SELECT jobwatch.url_identity(%s,true) AS u',(u,)).fetchone()['u'],exact_url(u))
        self.initial()
        with connect('jobwatch_owner') as c,c.transaction():
            actor(c)
            c.execute("INSERT INTO jobwatch.applications(job_key,company,role,url,posting_key) "
                      "VALUES ('manual:stable','Test','Grad','https://EXAMPLE.com:443/a#one','greenhouse:test:1')")
        self.rejected("INSERT INTO jobwatch.applications(job_key,company,role,url) "
                      "VALUES ('manual:duplicate','Test','Grad','https://example.com/a#two')",'jobwatch_owner')
        self.assertEqual(self.scalar('SELECT application_job_key FROM jobwatch.opportunity_workflow'),'manual:stable')

    def test_programme_cycles_and_verification_binding(self):
        self.initial([fixture('1'),fixture('2')])
        with connect('jobwatch_owner') as c,c.transaction():
            actor(c)
            c.execute("INSERT INTO jobwatch.leads(lead_key,kind,company,title,programme_key,cycle_year,cycle_wording,"
                "state,url,official,source_note,checked_at,checked_by) VALUES "
                "('programme:test:2027','programme','Test','Grad','test:graduate',2027,'2027 intake',"
                "'open','https://example.com/grad',true,'official',now(),'agent')")
        self.rejected("UPDATE jobwatch.leads SET cycle_year=2028",'jobwatch_owner')
        vid = self.scalar("SELECT current_version_id FROM jobwatch.postings WHERE native_id='1'")
        self.rejected("INSERT INTO jobwatch.verifications(posting_key,kind,checked_at,checked_by,url,version_id) "
                      "VALUES ('greenhouse:test:2','description',now(),'agent','https://example.com',%s)",
                      'jobwatch_owner',(vid,))

    def test_nul_and_evidence_preservation(self):
        raw = fixture(content='before\0after')
        self.initial([raw])
        with connect('jobwatch_owner') as c:
            v = c.execute('SELECT * FROM jobwatch.posting_versions').fetchone()
        self.assertEqual(json.loads(bytes(v['evidence'])),raw)
        self.assertEqual(v['description_text'],'before\ufffdafter')
        self.assertTrue(v['nul_replaced'])
        self.rejected("SELECT '{\"d\":\"before\\u0000after\"}'::jsonb")

    def test_real_history_regressions_and_new_field_baseline(self):
        fixture_path = Path(__file__).with_name('source_pairs.json')
        pairs = json.loads(fixture_path.read_text())
        for pair in pairs['ashby']:
            self.assertNotEqual(encoded(pair['before']),encoded(pair['after']))
            self.assertEqual(compare('ashby',pair['before'],pair['after']),({},{}))
        pair = pairs['greenhouse']
        changed,base = compare('greenhouse',pair['before'],pair['after'])
        self.assertEqual(set(changed),{'metadata'})
        self.assertEqual(base,{})
        prior = fixture()
        after = {**prior,'metadata':[{'name':'Job Summary','value':'Newly retained'}]}
        changed,base=compare('greenhouse',prior,after)
        self.assertEqual(changed,{})
        self.assertEqual(set(base),{'metadata'})

    def test_formatting_preserved_without_material_event(self):
        self.initial([fixture(content='<p>Build Python</p>')])
        self.initial([fixture(content='<p style="margin-top:0px">Build Python</p><p>&nbsp;</p>')])
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.posting_versions'),2)
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.posting_events WHERE category='provider'"),0)

    def test_consecutive_revisions_and_immutable_normalization(self):
        first = self.initial()
        self.initial([fixture(content='Second wording')])
        third=self.initial([fixture(content='Third wording')])
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.posting_versions'),3)
        with connect('jobwatch_collector') as c,c.transaction():
            actor(c)
            c.execute('SELECT id FROM jobwatch.sources WHERE id=%s FOR NO KEY UPDATE',(self.source,))
            old = c.execute('SELECT current_version_id FROM jobwatch.postings').fetchone()['current_version_id']
            vid,_,_=version(c,'greenhouse:test:1',self.source,fixture(content='Third wording'),third['id'],
                            datetime.now(timezone.utc),'v2',{'description_text':'THIRD WORDING'})
            event(c,'greenhouse:test:1',self.source,None,'normalizer','renormalized',old,vid)
            c.execute('UPDATE jobwatch.postings SET current_version_id=%s',(vid,))
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.posting_versions'),4)
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.posting_versions WHERE description_text='Third wording'"),1)
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.posting_events WHERE category='provider'"),2)

    def test_out_of_order_and_replay(self):
        older,newer = register(self.source),register(self.source)
        # Same timestamp cannot make two distinct attempts unordered.
        with connect() as c:
            c.execute('UPDATE jobwatch.collection_runs SET started_at=%s WHERE id=ANY(%s::uuid[])',
                      (older['started_at'],[older['id'],newer['id']]))
        self.assertLess(older['run_order'],newer['run_order'])
        self.assertEqual(publish(newer['id'],[fixture(content='new')]),'published')
        self.assertEqual(publish(older['id'],[fixture(content='old')]),'superseded')
        count = self.scalar('SELECT count(*) FROM jobwatch.posting_events')
        self.assertEqual(publish(newer['id'],[]),'published')
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.posting_events'),count)
        self.assertEqual(self.scalar('SELECT description_text FROM jobwatch.research_postings'),'new')

    def test_concurrent_publishers(self):
        older,newer = register(self.source),register(self.source)
        entered,release = threading.Event(),threading.Event()
        with ThreadPoolExecutor(2) as pool:
            a=pool.submit(publish,older['id'],[fixture(content='old')],True,entered,release)
            self.assertTrue(entered.wait(5))
            b=pool.submit(publish,newer['id'],[fixture(content='new')])
            self.wait_for_source_lock()
            release.set()
            self.assertEqual(a.result(5),'published')
            self.assertEqual(b.result(5),'published')
        self.assertEqual(self.scalar('SELECT description_text FROM jobwatch.research_postings'),'new')

    def test_rollback_then_retry_same_run(self):
        self.initial()
        run=register(self.source)
        receipt=datetime.now(timezone.utc)
        with self.assertRaises(psycopg.errors.DivisionByZero):
            publish(run['id'],[fixture(content='new'),fixture('2')],fault=True,fetched_at=receipt)
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.postings'),1)
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.posting_versions'),1)
        self.assertEqual(self.scalar('SELECT status FROM jobwatch.collection_runs WHERE id=%s',(run['id'],)),'running')
        self.assertEqual(publish(run['id'],[fixture(content='new'),fixture('2')],fetched_at=receipt),'published')
        self.assertEqual(self.scalar('SELECT fetched_at FROM jobwatch.collection_runs WHERE id=%s',(run['id'],)),receipt)

    def test_legacy_health_unknown_counters(self):
        with connect() as c,c.transaction():
            actor(c,'import:legacy-health')
            rid=uuid4()
            c.execute("INSERT INTO jobwatch.collection_runs(id,source_id,mode,status,started_at,fetched_at,finished_at,"
                "request_version,processing_revision,vacancies,details) VALUES "
                "(%s,%s,'import','imported',now(),now(),now(),'legacy','legacy',7,%s)",
                (rid,self.source,Jsonb({'legacy_health':{'last_count':7,'source_records':None}})))
            c.execute('UPDATE jobwatch.sources SET last_published_run_id=%s WHERE id=%s',(rid,self.source))
            row=c.execute('SELECT * FROM jobwatch.source_health').fetchone()
            self.assertEqual(row['vacancies'],7)
            self.assertFalse(row['absence_safe'])

    def test_network_malformed_and_empty_feed(self):
        self.initial([fixture('1'),fixture('2')])
        run=register(self.source)
        # Network/malformed path only finalizes health. All status transitions use source->run lock order.
        with connect('jobwatch_collector') as c,c.transaction():
            actor(c)
            c.execute('SELECT id FROM jobwatch.sources WHERE id=%s FOR NO KEY UPDATE',(self.source,))
            c.execute("UPDATE jobwatch.collection_runs SET status='failed',finished_at=clock_timestamp(),error='network' "
                      "WHERE id=%s AND status='running'",(run['id'],))
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.postings WHERE status='listed'"),2)
        run=register(self.source)
        with self.assertRaises(ValueError):
            publish(run['id'],[{'id':'invalid'}])
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.posting_events WHERE kind='not_seen'"),0)
        with connect('jobwatch_collector') as c,c.transaction():
            actor(c)
            c.execute('SELECT id FROM jobwatch.sources WHERE id=%s FOR NO KEY UPDATE',(self.source,))
            c.execute("UPDATE jobwatch.collection_runs SET status='failed',finished_at=clock_timestamp(),error='malformed' "
                      "WHERE id=%s AND status='running'",(run['id'],))
        # Successful protocol without snapshot confidence: retain positive observations, infer no absence.
        run=register(self.source)
        self.assertEqual(publish(run['id'],[fixture('1')],absence_safe=False),'published')
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.postings WHERE status='listed'"),2)
        self.initial([])
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.postings WHERE status='not_seen'"),2)
        self.assertEqual(self.scalar('SELECT vacancies FROM jobwatch.source_health'),0)

    def test_rescreen_serialization_and_old_pipeline_rejected(self):
        self.initial()
        run=register(self.source)
        with connect('jobwatch_collector') as c,ThreadPoolExecutor(1) as pool:
            with c.transaction():
                actor(c)
                c.execute('SELECT id FROM jobwatch.sources WHERE id=%s FOR NO KEY UPDATE',(self.source,))
                c.execute("UPDATE jobwatch.sources SET processing_revision='pipeline-v2' WHERE id=%s",(self.source,))
                c.execute("UPDATE jobwatch.postings SET rules_version='review-new-rules'")
                future=pool.submit(publish,run['id'],[fixture(content='old worker')])
                self.wait_for_source_lock()
            self.assertEqual(future.result(5),'superseded')
        self.assertEqual(self.scalar('SELECT rules_version FROM jobwatch.postings'),'review-new-rules')

    def test_concurrent_owner_write_and_publication(self):
        self.initial()
        run=register(self.source)
        with connect('jobwatch_collector') as c,ThreadPoolExecutor(1) as pool:
            with c.transaction():
                actor(c)
                c.execute('SELECT id FROM jobwatch.sources WHERE id=%s FOR NO KEY UPDATE',(self.source,))
                c.execute('UPDATE jobwatch.postings SET screened_at=clock_timestamp()')
                def owner_write():
                    with connect('jobwatch_owner') as o,o.transaction():
                        actor(o)
                        o.execute("SET LOCAL statement_timeout='2s'")
                        o.execute("INSERT INTO jobwatch.triage(posting_key,decision,recorded_by) "
                                  "VALUES ('greenhouse:test:1','interested','owner')")
                pool.submit(owner_write).result(4)
        self.assertEqual(publish(run['id'],[fixture(content='new')]),'published')
        self.assertEqual(self.scalar('SELECT decision FROM jobwatch.triage'),'interested')


if __name__=='__main__':
    unittest.main(verbosity=2)
