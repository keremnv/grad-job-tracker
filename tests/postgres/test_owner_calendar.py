"""Actual cookbook SQL and calendar producer against the verified disposable DB."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

import psycopg

from jobwatch import calendar
from jobwatch.postgres import calendar_feed, reports, research
from jobwatch.postgres.importer import primitive
from jobwatch.postgres.embeddings import chunks, write_chunks
from support import DatabaseTest, actor
from test_embeddings import Tokenizer, unit

ROOT=Path(__file__).resolve().parents[2]
BOOK=(ROOT/'sql/postgresql/owner_workflows.sql').read_text()
OWNER='agent:synthetic-owner-instruction'
TODAY=datetime.now(timezone.utc).date()
DUE=(TODAY+timedelta(days=5)).isoformat()
LATER=(TODAY+timedelta(days=6)).isoformat()
URL='https://example.org/jobs/manual'


class OwnerCalendarTests(DatabaseTest):
    def recipe(self,name,**values):
        sql=BOOK.split('-- BEGIN '+name+'\n',1)[1].split('-- END '+name,1)[0]
        args=['docker','exec','-i',os.environ['JOBWATCH_PG_TEST_CONTAINER'],'psql','-X','-w','-q',
              '-U','jobwatch_owner','-d','jobwatch_foundation_test']
        for key,value in {'actor':OWNER,**values}.items():args+=['-v',key+'='+str(value)]
        return subprocess.run(args+['-f','-'],input=sql,text=True,capture_output=True)

    def ok(self,name,**values):
        result=self.recipe(name,**values)
        self.assertEqual(result.returncode,0,result.stderr)
        return result

    def app(self,**values):
        return self.ok('create_application',job_key='manual:one',company='Synthetic',role='Engineer',url=URL,**values)

    def follow(self,**values):
        return {'job_key':'manual:one','expected_action':'','expected_action_date':'',
                'action':'PRIVATE_ACTION','action_date':DUE,**values}

    def triage(self,key,**values):
        return {'posting_key':key,'expected_decision':'absent','expected_reason':'','expected_note':'',
            'expected_revisit_on':'','decision':'later','reason':'role_fit','note':'PRIVATE_TRIAGE',
            'revisit_on':DUE,**values}

    def lead(self,**values):
        return {'lead_key':'vacancy:synthetic','kind':'vacancy','company':'Synthetic','title':'Engineer',
            'programme_key':'','cycle_year':'','cycle_wording':'','state':'listed','url':URL,'official':'true',
            'source_note':'Exact official source observed in synthetic fixture',
            'wording':"Quoted source; '); DELETE FROM jobwatch.postings; --",
            'deadline_date':DUE,'deadline_wording':'Apply by '+DUE,
            'checked_at':(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat(),**values}

    def audit_count(self):return self.scalar('SELECT count(*) FROM jobwatch.owner_audit')

    def calendar(self):
        with self.target.connect('jobwatch_owner') as conn:return reports.calendar_data(conn)

    def source_fingerprint(self):
        with self.target.connect() as conn:
            return {t:sha256(json.dumps(primitive(conn.execute('SELECT to_jsonb(t) AS row FROM jobwatch.'+t+
                ' t ORDER BY to_jsonb(t)::text').fetchall()),sort_keys=True).encode()).hexdigest()
                for t in ('postings','posting_versions','posting_events','collection_runs','embedding_chunks')}

    def test_manual_create_replay_explicit_link_and_identity_conflict(self):
        key,_,_=self.seed();self.app();count=self.audit_count();self.app()
        self.assertEqual(count,self.audit_count())
        self.ok('link_application',job_key='manual:one',expected_posting_key='',posting_key=key)
        self.assertEqual(self.scalar('SELECT job_key FROM jobwatch.applications'),'manual:one')
        self.assertEqual(self.scalar('SELECT posting_key FROM jobwatch.applications'),key)
        self.ok('link_application',job_key='manual:one',expected_posting_key='',posting_key=key)
        self.assertNotEqual(self.recipe('create_application',job_key='manual:one',company='Wrong',role='Engineer',url=URL).returncode,0)
        self.assertNotEqual(self.recipe('create_application',job_key='manual:duplicate',company='Synthetic',role='Engineer',url=URL).returncode,0)
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.applications'),1)

    def test_stage_dates_stale_guards_followup_reschedule_clear_and_readback(self):
        self.app();stage={'job_key':'manual:one','expected_stage':'bookmarked','expected_date_applied':'',
                         'stage':'applied','date_applied':TODAY.isoformat()}
        self.ok('update_stage',**stage);count=self.audit_count();self.ok('update_stage',**stage)
        self.assertEqual(count,self.audit_count())
        self.assertEqual(self.recipe('update_stage',**{**stage,'stage':'withdrawn'}).returncode,3)
        self.ok('set_followup',**self.follow());count=self.audit_count();self.ok('set_followup',**self.follow())
        self.assertEqual(count,self.audit_count())
        self.assertEqual(self.recipe('set_followup',**self.follow(action_date=LATER)).returncode,3)
        self.ok('set_followup',**self.follow(expected_action='PRIVATE_ACTION',expected_action_date=DUE,action_date=LATER))
        self.ok('set_followup',**self.follow(expected_action='PRIVATE_ACTION',expected_action_date=LATER,action='',action_date=''))
        with self.target.connect('jobwatch_owner') as conn:
            row=conn.execute('SELECT * FROM jobwatch.applications').fetchone()
            self.assertIsNone(row['next_action']);self.assertIsNone(row['next_action_date'])
            self.assertEqual(row['stage'],'applied');self.assertEqual(row['date_applied'],TODAY)

    def test_concurrent_owner_updates_one_wins_other_is_stale(self):
        self.app()
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(lambda day:self.recipe('set_followup',**self.follow(action_date=day)),(DUE,LATER)))
        self.assertEqual(sorted(r.returncode for r in results),[0,3])
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.owner_audit WHERE table_name='applications' AND op='UPDATE'"),1)

    def test_sql_reschedule_completion_and_revisit_reschedule_refresh_snapshot(self):
        self.app();self.ok('set_followup',**self.follow())
        key,_,_=self.seed();self.ok('set_triage',**self.triage(key))
        with TemporaryDirectory() as temp:
            root=Path(temp)/'feed';calendar_feed.publish(self.calendar(),root,initialize=True)
            before=calendar_feed.current_snapshot(root)[1]
            self.ok('set_followup',**self.follow(expected_action='PRIVATE_ACTION',expected_action_date=DUE,action_date=LATER))
            self.ok('set_triage',**self.triage(key,expected_decision='later',expected_reason='role_fit',
                expected_note='PRIVATE_TRIAGE',expected_revisit_on=DUE,revisit_on=LATER))
            calendar_feed.publish(self.calendar(),root)
            changed=calendar_feed.current_snapshot(root)[1]
            for uid in before['events']:
                self.assertEqual(changed['events'][uid]['sequence'],before['events'][uid]['sequence']+1)
            self.ok('set_followup',**self.follow(expected_action='PRIVATE_ACTION',expected_action_date=LATER,action='',action_date=''))
            self.ok('set_triage',**self.triage(key,expected_decision='later',expected_reason='role_fit',
                expected_note='PRIVATE_TRIAGE',expected_revisit_on=LATER,decision='interested',revisit_on=''))
            calendar_feed.publish(self.calendar(),root)
            self.assertEqual(calendar_feed.current_snapshot(root)[1]['files']['actions.ics']['events'],0)
            self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.owner_audit WHERE op='UPDATE' AND table_name IN ('applications','triage')"),5)

    def test_triage_revisit_application_precedence_and_noop_audit(self):
        key,_,_=self.seed();values=self.triage(key)
        self.ok('set_triage',**values)
        before=self.scalar('SELECT decided_at FROM jobwatch.triage');count=self.audit_count()
        self.ok('set_triage',**values)
        self.assertEqual(before,self.scalar('SELECT decided_at FROM jobwatch.triage'))
        self.assertEqual(count,self.audit_count())
        data=self.calendar();self.assertEqual(len(data['revisits']),1)
        content,_=calendar_feed.build(data)
        self.assertIn(calendar.uid('revisit',key).encode(),content['actions.ics'].replace(b'\r\n ',b''))
        stale=self.recipe('set_triage',**self.triage(key,decision='interested',revisit_on=''))
        self.assertEqual(stale.returncode,3)
        self.app();self.ok('link_application',job_key='manual:one',expected_posting_key='',posting_key=key)
        self.assertEqual(self.calendar()['revisits'],[])
        self.assertEqual(self.scalar('SELECT decision FROM jobwatch.triage'),'later')

    def test_evidenced_lead_recipe_idempotence_and_programme_years(self):
        values=self.lead();self.ok('record_lead',**values);count=self.audit_count();self.ok('record_lead',**values)
        self.assertEqual(count,self.audit_count())
        self.assertEqual(self.scalar('SELECT wording FROM jobwatch.leads'),values['wording'])
        self.assertEqual(self.recipe('record_lead',**{**values,'deadline_date':LATER}).returncode,3)
        for year,state in ((2026,'previous_cycle'),(2027,'open')):
            self.ok('record_lead',**self.lead(lead_key=f'programme:synthetic:{year}',kind='programme',
                programme_key='synthetic',cycle_year=year,cycle_wording=f'Autumn {year} intake',state=state,
                deadline_date=DUE,url='https://example.org/graduate'))
        projected,_=reports.calendar_events(self.calendar(),minimal=True)
        programmes=[e for e in projected['deadlines'] if 'cycle ' in e['summary']]
        self.assertEqual(len(programmes),1);self.assertIn('2027',programmes[0]['summary'])
        self.assertEqual(programmes[0]['date'][2],'date:'+DUE)
        self.assertEqual(self.scalar("SELECT cycle_year FROM jobwatch.leads WHERE state='previous_cycle'"),2026)

    def test_lead_linkage_retains_published_uid_and_removes_duplicate(self):
        self.ok('record_lead',**self.lead())
        with TemporaryDirectory() as temp:
            root=Path(temp)/'calendar';calendar_feed.publish(self.calendar(),root,initialize=True)
            first=(root/'current'/'deadlines.ics').read_bytes().replace(b'\r\n ',b'')
            self.assertIn(calendar.uid('deadline',URL).encode(),first)
            key,_,_=self.seed()
            self.ok('link_lead',lead_key='vacancy:synthetic',expected_posting_key='',posting_key=key)
            calendar_feed.publish(self.calendar(),root)
            after=(root/'current'/'deadlines.ics').read_bytes().replace(b'\r\n ',b'')
            self.assertEqual(after.count(b'BEGIN:VEVENT'),1)
            self.assertIn(calendar.uid('deadline',URL).encode(),after)
            with self.target.connect('jobwatch_owner') as conn,conn.transaction():
                actor(conn,OWNER);conn.execute('DELETE FROM jobwatch.leads')
            calendar_feed.publish(self.calendar(),root)
            self.assertNotIn(b'BEGIN:VEVENT',(root/'current'/'deadlines.ics').read_bytes().replace(b'\r\n ',b''))

    def test_ambiguous_exact_url_never_guesses_lead_or_application_link(self):
        key,_,_=self.seed(native_id='1',url=URL);self.seed(native_id='2',url=URL)
        self.ok('record_lead',**self.lead());self.app()
        data=self.calendar();claim=data['deadlines'][0]
        self.assertTrue(claim['ambiguous'])
        feeds,warnings=reports.calendar_events(data,minimal=True)
        self.assertEqual(feeds['deadlines'],[]);self.assertTrue(warnings)
        self.assertIsNone(self.scalar('SELECT application_job_key FROM jobwatch.opportunity_workflow WHERE posting_key=%s',(key,)))

    def test_research_to_authorised_owner_sql_audit_and_private_snapshot(self):
        key,vid,_=self.seed(description='Python C++ graduate evidence')
        with self.target.connect() as conn:conn.execute("UPDATE jobwatch.postings SET verdict='excluded' WHERE posting_key=%s",(key,))
        with self.target.connect('jobwatch_owner') as conn:
            version=conn.execute('SELECT * FROM jobwatch.posting_versions WHERE id=%s',(vid,)).fetchone()
        items=chunks(version,Tokenizer())
        with self.target.connect('jobwatch_embedding_writer') as conn:write_chunks(conn,version,items,[unit() for _ in items])
        with self.target.connect('jobwatch_owner') as conn:
            for method in ('bm25','fts','vector','hybrid'):
                result=research.search(conn,'Python',method=method,query_vector=unit())
                self.assertEqual(result['items'][0]['posting_key'],key)
                self.assertEqual(result['items'][0]['verdict'],'excluded')
        before=self.source_fingerprint()
        self.app();self.ok('link_application',job_key='manual:one',expected_posting_key='',posting_key=key)
        self.ok('set_followup',**self.follow());self.ok('set_triage',**self.triage(key))
        self.ok('record_lead',**self.lead())
        with self.target.connect('jobwatch_owner') as conn,conn.transaction():
            actor(conn,OWNER);conn.execute("UPDATE jobwatch.applications SET notes='PRIVATE_NOTE',cv_version='PRIVATE_CV'")
        with TemporaryDirectory() as temp:
            root=Path(temp)/'feed';result=calendar_feed.publish(self.calendar(),root,initialize=True)
            all_bytes=b''.join(p.read_bytes() for p in (root/'current').iterdir())
            for secret in (b'PRIVATE_NOTE',b'PRIVATE_CV',b'PRIVATE_ACTION',b'PRIVATE_TRIAGE',b'DELETE FROM'):
                self.assertNotIn(secret,all_bytes)
            self.assertEqual(result['files']['actions.ics']['events'],1)
        self.assertEqual(before,self.source_fingerprint())
        with self.target.connect('jobwatch_owner') as conn:
            rows=conn.execute("SELECT actor,db_login,table_name FROM jobwatch.owner_audit WHERE table_name IN ('applications','triage','leads')").fetchall()
            self.assertTrue(rows);self.assertTrue(all(r['actor']==OWNER and r['db_login']=='jobwatch_owner' for r in rows))
        self.rejected(psycopg.errors.InsufficientPrivilege,'UPDATE jobwatch.posting_versions SET title=title',user='jobwatch_owner')
        self.rejected(psycopg.errors.InsufficientPrivilege,'SELECT * FROM jobwatch.applications',user='jobwatch_collector')

    def test_owner_transaction_rollback_includes_audit_and_requires_attribution(self):
        self.app();count=self.audit_count()
        with self.target.connect('jobwatch_owner') as conn:
            with self.assertRaises(psycopg.errors.CheckViolation),conn.transaction():
                actor(conn,OWNER)
                conn.execute("UPDATE jobwatch.applications SET next_action='PRIVATE_ACTION',next_action_date=%s",(DUE,))
                conn.execute("INSERT INTO jobwatch.applications(job_key,company,role,url,stage) VALUES ('manual:bad','Bad','Bad','https://example.org/bad','invalid')")
            self.assertIsNone(conn.execute('SELECT next_action FROM jobwatch.applications').fetchone()['next_action'])
            with self.assertRaises(psycopg.errors.RaiseException),conn.transaction():
                conn.execute("UPDATE jobwatch.applications SET notes='no attribution'")
        self.assertEqual(count,self.audit_count())

    def test_operational_snapshot_cli_status_and_failure_preservation(self):
        self.app();self.ok('set_followup',**self.follow())
        env={**os.environ,'JOBWATCH_DATABASE_URL':self.target.config('jobwatch_owner').dsn,'PYTHONPATH':str(ROOT/'src')}
        with TemporaryDirectory() as temp:
            root=Path(temp)/'feed'
            args=[sys.executable,'-m','jobwatch.postgres.operations','calendar','snapshot','--output-dir',str(root)]
            first=subprocess.run(args+['--initialize'],env=env,text=True,capture_output=True)
            self.assertEqual(first.returncode,0,first.stderr)
            self.assertNotIn('PRIVATE_ACTION',first.stdout)
            directory,manifest=calendar_feed.current_snapshot(root)
            status=subprocess.run([sys.executable,'-m','jobwatch.postgres.operations','calendar','status','--output-dir',str(root)],
                                  env=env,text=True,capture_output=True)
            self.assertEqual(status.returncode,0,status.stderr);self.assertTrue(json.loads(status.stdout)['fresh'])
            failed=subprocess.run(args+['--max-bytes','128'],env=env,text=True,capture_output=True)
            self.assertEqual(failed.returncode,2)
            self.assertEqual(calendar_feed.current_snapshot(root),(directory,manifest))
