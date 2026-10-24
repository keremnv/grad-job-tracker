"""Actual SQL views, operational tools and Stage 2/3 integration; mocked networks only."""
from collections import Counter
from copy import deepcopy
from datetime import datetime,timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

try:
    import psycopg
except ImportError:
    psycopg=None

from jobwatch import filtering
from jobwatch.postgres import research,reports,readiness,endpoints
from jobwatch.postgres.collection import iso
from jobwatch.postgres.endpoints import SourceCheckError
from jobwatch.postgres.import_plan import build_plan
from jobwatch.postgres.importer import apply_import,primitive
from jobwatch.postgres.migrate import migrate
from jobwatch.postgres.orchestrator import collect_monitored,actions_summary
from jobwatch.postgres.publisher import Collector,CollectionError
from import_fixture import repository,KEY,native
from rehearsal import rehearse
from support import ENABLED,Target,DOMAIN_TABLES,actor


@unittest.skipUnless(ENABLED,'Run scripts/test_postgres.sh for verified disposable PostgreSQL')
class OperationalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.target=Target.verified();cls.target.reset_schema()
        with cls.target.connect() as conn:migrate(conn)
        cls.temp=TemporaryDirectory();cls.addClassCleanup(cls.temp.cleanup)
        cls.repo,cls.commit=repository(cls.temp.name);cls.plan=build_plan(cls.repo,cls.commit)
        cls.restore=rehearse(cls.repo,cls.commit)  # real nonempty-owner pg_dump/pg_restore proof

    def setUp(self):
        with self.target.connect() as conn:
            conn.execute('TRUNCATE '+','.join('jobwatch.'+t for t in DOMAIN_TABLES)+' RESTART IDENTITY CASCADE')
            self.baseline=apply_import(conn,self.plan)
            self.sid=conn.execute("SELECT id FROM jobwatch.sources WHERE board='test'").fetchone()['id']
            self.lever=conn.execute("SELECT id FROM jobwatch.sources WHERE board='paused'").fetchone()['id']
        self.collector=Collector(self.target.config('jobwatch_collector'),actor='collector:operations-test')

    def check(self,sid=None,response=None,fixture=False):
        sid=sid or self.sid
        response=response if response is not None else {'jobs':[native()],'meta':{'total':1}}
        # Mock transport models the future live path solely inside the verified test DB.
        return endpoints.check_endpoint(self.collector,sid,get_json=lambda _:response,pause=lambda _:None,fixture=fixture)

    def activate(self,sid=None,response=None):
        sid=sid or self.sid
        check=self.check(sid,response)
        with self.target.connect('jobwatch_owner') as conn:
            endpoints.activate_source(conn,sid,check['run_id'],actor='owner:mock-activation')
        return check

    def publish(self,rows):
        return self.collector.collect_source(self.sid,get_json=lambda _:{'jobs':rows,'meta':{'total':len(rows)}})

    def test_public_research_all_verdicts_and_private_attention_view(self):
        with self.target.connect('jobwatch_collector') as conn:
            rows=conn.execute('SELECT * FROM jobwatch.research_postings').fetchall()
            self.assertEqual(len(rows),3);self.assertEqual(Counter(r['verdict'] for r in rows),{'review':1,'excluded':2})
            self.assertEqual({r['coverage_state'] for r in rows},{'legacy_snapshot','legacy_not_seen_unproven'})
            with self.assertRaises(psycopg.errors.InsufficientPrivilege),conn.transaction():
                conn.execute('SELECT * FROM jobwatch.operational_postings')
        with self.target.connect('jobwatch_owner') as conn:
            queue=research.queue(conn)
            self.assertEqual([r['posting_key'] for r in queue],[KEY])
            self.assertEqual(queue[0]['action_state'],'preparing')  # application outranks later triage
            self.assertEqual(queue[0]['application_link_basis'],'explicit_posting_key')
            row=conn.execute("SELECT * FROM jobwatch.operational_postings WHERE posting_key='greenhouse:test:3'").fetchone()
            self.assertIsNone(row['application_job_key']);self.assertTrue(row['possible_application_match'])

    def test_owner_conflict_and_ambiguous_url_are_visible_without_guessing(self):
        with self.target.connect('jobwatch_owner') as conn,conn.transaction():
            actor(conn)
            conn.execute("UPDATE jobwatch.triage SET decision='dismissed',revisit_on=NULL WHERE posting_key=%s",(KEY,))
        with self.target.connect('jobwatch_owner') as conn:
            row=research.queue(conn)[0]
            self.assertTrue(row['triage_application_conflict']);self.assertEqual(row['action_state'],'preparing')
        # Simulate an honestly retained ambiguous legacy URL, not a new provider normalization.
        with self.target.connect() as conn:
            conn.execute("UPDATE jobwatch.posting_versions SET posting_url=%s WHERE id=(SELECT current_version_id FROM jobwatch.postings WHERE posting_key='greenhouse:test:3')",(native('2')['absolute_url'],))
        with self.target.connect('jobwatch_owner') as conn:
            row=conn.execute("SELECT * FROM jobwatch.operational_postings WHERE posting_key='greenhouse:test:2'").fetchone()
            self.assertTrue(row['ambiguous_posting_url']);self.assertIsNone(row['application_job_key'])
            self.assertEqual(row['action_state'],'unreviewed')

    def test_material_history_and_current_historical_native_evidence(self):
        with self.target.connect('jobwatch_owner') as conn:
            events=research.history(conn,'2026-01-01T00:00:00Z')
            self.assertEqual(len(events),15)
            self.assertEqual({r['legacy_category'] for r in events},{'source','screening','mixed'})
            self.assertNotIn('original_json',json.dumps(primitive(events)))
            self.assertTrue(any('description_sha256' in r['changed_fields'] for r in events))
            current=research.evidence(conn,KEY)
            self.assertTrue(current['is_current']);self.assertEqual(current['origin'],'legacy_index')
            old_id=conn.execute("SELECT id FROM jobwatch.posting_versions WHERE posting_key=%s AND origin='git_history'",(KEY,)).fetchone()['id']
            old=research.evidence(conn,KEY,old_id)
            self.assertFalse(old['is_current']);self.assertEqual(old['untrusted_evidence']['data'],native())
            with self.assertRaises(ValueError):research.evidence(conn,'greenhouse:test:2',old_id)

    def test_exact_current_description_verification_required_for_requirements(self):
        with self.target.connect('jobwatch_owner') as conn:
            self.assertEqual(research.verified_requirements(conn),[])  # old hash in legacy map != current wording
        self.activate();self.publish([native(text='Python, C++, C# and Kubernetes')])
        with self.target.connect('jobwatch_owner') as conn,conn.transaction():
            actor(conn)
            vid=conn.execute('SELECT current_version_id FROM jobwatch.postings WHERE posting_key=%s',(KEY,)).fetchone()['current_version_id']
            conn.execute("INSERT INTO jobwatch.verifications(posting_key,kind,checked_at,checked_by,url,version_id) VALUES (%s,'description',now(),'agent',%s,%s)",(KEY,native()['absolute_url'],vid))
        with self.target.connect('jobwatch_owner') as conn:
            result=reports.requirement_mentions(conn)
            self.assertEqual(result['sample_size'],1)
            sample=next(iter(result['groups'].values()))[0]
            self.assertTrue({'Python','C++','C#','Kubernetes'}<=set(sample['mentions']))

    def test_screening_preview_uses_current_rules_without_writes(self):
        with self.target.connect('jobwatch_collector') as conn:
            before=conn.execute('SELECT rules_version,screening_input_sha256 FROM jobwatch.postings ORDER BY posting_key').fetchall()
            result=research.screening_preview(conn)
            self.assertFalse(result['written']);self.assertEqual(len(result['items']),3)
            self.assertEqual(conn.execute('SELECT rules_version,screening_input_sha256 FROM jobwatch.postings ORDER BY posting_key').fetchall(),before)
            self.assertEqual(result['rules_version'],filtering.RULES_VERSION)

    def test_search_all_verdicts_current_scope_and_c_family_tokenisation(self):
        self.activate()
        jobs=[native('1','Use C++ systems'),native('2','Use C# services'),native('3','Use C systems')]
        jobs[2]['title']='Senior C Engineer'
        self.publish(jobs)
        with self.target.connect('jobwatch_collector') as conn:
            for query,key in (('C++','greenhouse:test:1'),('C#','greenhouse:test:2'),('C','greenhouse:test:3')):
                result=research.search(conn,query)
                self.assertEqual([r['posting_key'] for r in result['items']],[key])
                self.assertEqual(result['ranking'],'python_bm25')
                self.assertEqual(result['items'][0]['untrusted_match']['trust'],'untrusted_employer_content')
            self.assertEqual(research.search(conn,'C')['items'][0]['verdict'],'excluded')
            fts=research.search(conn,'C++',method='fts')
            self.assertEqual({r['posting_key'] for r in fts['items']},{'greenhouse:test:1','greenhouse:test:2','greenhouse:test:3'})
            self.assertEqual(fts['ranking'],'postgresql_fts_ts_rank_cd')
        self.publish([])
        with self.target.connect('jobwatch_collector') as conn:
            self.assertEqual(research.search(conn,'C++')['items'],[])
            self.assertEqual(len(research.search(conn,'C++',include_not_seen=True)['items']),1)
            historical=research.search(conn,'First',history=True)
            self.assertTrue(historical['items']);self.assertTrue(all(not r['is_current'] for r in historical['items']))

    def test_deadlines_cycles_daily_weekly_calendar_preserve_uncertainty_and_privacy(self):
        with self.target.connect('jobwatch_owner') as conn:
            data=reports.snapshot(conn,days=7,since='2026-01-01T00:00:00Z',requirements=True)
        claims=data['deadlines']
        self.assertEqual(next(r for r in data['source_health'] if r['board']=='test')['legacy_attempt_status'],'failure')
        self.assertEqual(data['recent_observations'],[])  # old retained rows are not new listings
        self.assertEqual({d['provenance'] for d in claims},{'independent_verification','checked_official_lead'})
        self.assertTrue(any(d['value'] is None and 'unknown' in d['urgency'] for d in claims))
        self.assertEqual({(r['cycle_year'],r['state']) for r in data['leads'] if r['kind']=='programme'},{(2026,'previous_cycle'),(2027,'open')})
        for weekly in (False,True):
            rendered=reports.markdown(data,weekly=weekly)
            self.assertIn('not_seen does not prove closure',rendered)
            self.assertIn('independent',rendered)
            self.assertIn('owner action',rendered)
            self.assertNotIn('private application fixture note',rendered);self.assertNotIn('fixture-cv-label',rendered)
        self.assertNotIn('description_text',json.dumps(__import__('jobwatch.postgres.importer',fromlist=['primitive']).primitive(data)))
        blob,warnings=reports.calendar_export(data)
        self.assertIn(b'BEGIN:VCALENDAR',blob);self.assertIn(b'CLASS:PRIVATE',blob)
        self.assertIn(b'20261201',blob);self.assertIn(b'expiry time and timezone unknown',blob)
        self.assertNotIn(b'private triage fixture note',blob)

    def test_deadline_conflict_and_ambiguous_timezone_do_not_invent_calendar_dates(self):
        with self.target.connect('jobwatch_owner') as conn,conn.transaction():
            actor(conn)
            for value in ('2026-12-02','2026-12-03T12:00:00-00:00'):
                conn.execute("INSERT INTO jobwatch.verifications(posting_key,kind,checked_at,checked_by,url,wording,value) VALUES (%s,'deadline',now(),'agent',%s,'Deadline wording',%s)",(KEY,native()['absolute_url'],value))
        with self.target.connect('jobwatch_owner') as conn:data=reports.snapshot(conn)
        self.assertTrue(any(d['date_conflict'] for d in data['deadlines']))
        blob,warnings=reports.calendar_export(data)
        self.assertTrue(warnings);self.assertNotIn(b'Recorded application deadline',blob)
        self.assertTrue(any('timezone unknown' in d['urgency'] for d in data['deadlines']))

    def test_endpoint_check_is_not_mapping_or_collection_and_fixture_cannot_activate(self):
        with self.target.connect() as conn:
            before=conn.execute('SELECT last_published_run_id FROM jobwatch.sources WHERE id=%s',(self.sid,)).fetchone()['last_published_run_id']
        check=self.check(fixture=True)
        self.assertEqual(check['status'],'validated');self.assertFalse(check['published'])
        with self.target.connect('jobwatch_owner') as conn:
            inspected=endpoints.inspect_source(conn,self.sid)
            self.assertTrue(inspected['mapping_complete']);self.assertIsNone(inspected['source']['endpoint_checked_at'])
            with self.assertRaisesRegex(SourceCheckError,'Fixture'):
                endpoints.activate_source(conn,self.sid,check['run_id'],actor='owner:test')
        with self.target.connect() as conn:
            self.assertEqual(conn.execute('SELECT last_published_run_id FROM jobwatch.sources WHERE id=%s',(self.sid,)).fetchone()['last_published_run_id'],before)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM jobwatch.postings').fetchone()['n'],3)
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM jobwatch.posting_events WHERE category<>'import'").fetchone()['n'],0)

    def test_live_transport_mock_activation_records_receipt_and_requires_association(self):
        def get(_):
            with self.target.connect('postgres') as observer:
                count=observer.execute("SELECT count(*) AS n FROM pg_stat_activity WHERE application_name='jobwatch-runtime' AND state='idle in transaction'").fetchone()['n']
                self.assertEqual(count,0)
            return {'jobs':[],'meta':{'total':0}}
        check=endpoints.check_endpoint(self.collector,self.sid,get_json=get)
        with self.target.connect('jobwatch_owner') as conn,conn.transaction():
            actor(conn);conn.execute('UPDATE jobwatch.sources SET mapping_verified_at=NULL WHERE id=%s',(self.sid,))
        with self.target.connect('jobwatch_owner') as conn:
            with self.assertRaisesRegex(SourceCheckError,'association'):
                endpoints.activate_source(conn,self.sid,check['run_id'],actor='owner:test')
            with conn.transaction():
                actor(conn);conn.execute('UPDATE jobwatch.sources SET mapping_verified_at=now() WHERE id=%s',(self.sid,))
            result=endpoints.activate_source(conn,self.sid,check['run_id'],actor='owner:test')
            self.assertEqual(result['endpoint_checked_at'],check['fetched_at'])
            self.assertEqual(endpoints.inspect_source(conn,self.sid)['source']['status'],'monitored')
        with self.target.connect('jobwatch_collector') as conn:
            with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                endpoints.activate_source(conn,self.sid,check['run_id'],actor='collector:forbidden')

    def test_stale_failed_and_cross_source_checks_refuse_activation(self):
        first=self.check()
        failed=self.check(response={'jobs':[{'id':'malformed'}]})
        self.assertEqual(failed['status'],'failed')
        with self.target.connect('jobwatch_owner') as conn:
            with self.assertRaisesRegex(SourceCheckError,'latest successful'):
                endpoints.activate_source(conn,self.sid,first['run_id'],actor='owner:test')
        lever=self.check(self.lever,response=[])
        with self.target.connect('jobwatch_owner') as conn:
            with self.assertRaisesRegex(SourceCheckError,'same-source'):
                endpoints.activate_source(conn,self.sid,lever['run_id'],actor='owner:test')

    def test_endpoint_identity_change_during_fetch_is_superseded(self):
        def get(_):
            with self.target.connect() as conn,conn.transaction():
                actor(conn);conn.execute("UPDATE jobwatch.sources SET region='eu' WHERE id=%s",(self.sid,))
            return {'jobs':[native()],'meta':{'total':1}}
        result=endpoints.check_endpoint(self.collector,self.sid,get_json=get)
        self.assertEqual(result['status'],'superseded')
        with self.target.connect('jobwatch_owner') as conn:
            self.assertIsNone(endpoints.inspect_source(conn,self.sid)['source']['endpoint_checked_at'])

    def test_multiple_sources_partial_failure_and_empty_feed_publish_independently(self):
        self.activate();self.activate(self.lever,[])
        def get(url):
            if 'lever' in url:raise TimeoutError('password=never-expose-this')
            return {'jobs':[],'meta':{'total':0}}
        result=collect_monitored(self.collector,get_json=get,pause=lambda _:None)
        self.assertEqual(result['counts'],{'published':1,'failed':1});self.assertEqual(result['exit_code'],1)
        self.assertEqual(result['absence_safe'],1)
        self.assertNotIn('password',json.dumps(__import__('jobwatch.postgres.importer',fromlist=['primitive']).primitive(result)))
        with self.target.connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) AS n FROM jobwatch.postings WHERE status='not_seen'").fetchone()['n'],3)
            coverage={r['coverage_state'] for r in conn.execute('SELECT * FROM jobwatch.research_postings')}
            self.assertEqual(coverage,{'not_seen_absence_safe'})
        summary=actions_summary(result)
        self.assertIn('failed',summary);self.assertNotIn('private',summary.lower())

    def test_scheduled_positive_only_lever_retains_unseen_positive_time(self):
        self.activate(self.lever,[])
        jobs=[{'id':str(i),'text':'Graduate Software Engineer','hostedUrl':f'https://jobs.lever.co/paused/{i}',
            'descriptionPlain':'Python','country':'GB','categories':{'location':'London'}} for i in range(101)]
        from jobwatch.postgres.operations import fixture_transport
        with TemporaryDirectory() as temp:
            file=Path(temp)/'feed.json';file.write_text(json.dumps(jobs))
            first=collect_monitored(self.collector,get_json=fixture_transport(file),pause=lambda _:None)
            file.write_text(json.dumps(jobs[:100]))
            second=collect_monitored(self.collector,get_json=fixture_transport(file),pause=lambda _:None)
        self.assertEqual(second['positive_only'],1);self.assertEqual(second['exit_code'],0)
        with self.target.connect('jobwatch_collector') as conn:
            row=conn.execute("SELECT * FROM jobwatch.research_postings WHERE posting_key='lever:paused:100'").fetchone()
            self.assertEqual(row['coverage_state'],'retained_after_positive_only_run')
            self.assertEqual(str(row['last_seen_run_id']),first['sources'][0]['run_id'])
            self.assertEqual(row['status'],'listed')

    def test_scheduled_skipped_superseded_unknown_and_source_bound(self):
        self.activate()
        original=self.collector.collect_source
        def paused(sid,**kwargs):
            with self.target.connect('jobwatch_owner') as conn,conn.transaction():
                actor(conn);conn.execute("UPDATE jobwatch.sources SET status='paused' WHERE id=%s",(sid,))
            return original(sid,**kwargs)
        with patch.object(self.collector,'collect_source',side_effect=paused):
            result=collect_monitored(self.collector,get_json=lambda _:self.fail('Paused source fetched'))
        self.assertEqual(result['counts'],{'skipped':1});self.assertEqual(result['exit_code'],1)
        self.assertEqual(collect_monitored(self.collector)['warning'],'no_monitored_sources')
        self.activate()
        def out_of_order(sid,**kwargs):
            from jobwatch.postgres.collection import fetch
            registration=self.collector.register(sid)
            batch=fetch(registration,get_json=lambda _:{'jobs':[native()],'meta':{'total':1}})
            original(sid,get_json=lambda _:{'jobs':[native()],'meta':{'total':1}})
            return registration.id,self.collector.publish(batch)
        with patch.object(self.collector,'collect_source',side_effect=out_of_order):
            replay=collect_monitored(self.collector)
        self.assertEqual(replay['counts'],{'superseded':1});self.assertEqual(replay['exit_code'],1)
        self.activate(self.lever,[])
        with self.assertRaises(ValueError):collect_monitored(self.collector,max_sources=1)
        with patch.object(self.collector,'collect_source',side_effect=CollectionError('safe',outcome='unknown')):
            result=collect_monitored(self.collector)
        self.assertEqual(result['counts'],{'unknown':2});self.assertEqual(result['exit_code'],2)

    def test_readiness_binds_import_and_restore_and_never_authorizes_hosting(self):
        with self.target.connect() as conn:
            baseline=readiness.inspect(conn,reconciliation=self.baseline,restore=self.restore)
        self.assertFalse(baseline['local_checks_passed']);self.assertFalse(baseline['deployment_ready'])
        pending=next(c for c in baseline['checks'] if c['check']=='intended_sources_activated')['pending']
        self.assertEqual(pending,['greenhouse:test'])
        self.activate();self.publish([native('1'),native('2'),native('3')])
        with self.target.connect() as conn:
            live=readiness.inspect(conn,reconciliation=self.baseline,restore=self.restore,phase='operational')
            self.assertTrue(live['local_checks_passed'],live['checks']);self.assertFalse(live['deployment_ready'])
            self.assertTrue(all(g['status']=='not_verified_by_this_command' for g in live['external_gates']))
            conn.execute('GRANT UPDATE(title) ON jobwatch.posting_versions TO jobwatch_owner')
            try:
                unsafe=readiness.inspect(conn,reconciliation=self.baseline,restore=self.restore,phase='operational')
                self.assertFalse(next(c['ok'] for c in unsafe['checks'] if c['check']=='immutable_evidence'))
            finally:
                conn.execute('REVOKE UPDATE(title) ON jobwatch.posting_versions FROM jobwatch_owner')
            wrong=deepcopy(self.restore);wrong['schema_sha256']='0'*64
            self.assertFalse(readiness.inspect(conn,reconciliation=self.baseline,restore=wrong,phase='operational')['local_checks_passed'])
            legacy_id=self.plan.rows['posting_versions'][0]['id']
            conn.execute('UPDATE jobwatch.posting_versions SET title=%s WHERE id=%s',('Tampered retained title',legacy_id))
            self.assertFalse(readiness.inspect(conn,reconciliation=self.baseline,restore=self.restore,phase='operational')['local_checks_passed'])

    def test_noninteractive_cli_reports_search_and_mock_scan(self):
        env={**os.environ,'JOBWATCH_DATABASE_URL':self.target.config('jobwatch_owner').dsn}
        base=[sys.executable,'-m','jobwatch.postgres.operations']
        for kind in ('daily','weekly','requirements','calendar'):
            result=subprocess.run(base+['report',kind],env=env,text=True,capture_output=True,timeout=15)
            self.assertIn(result.returncode,(0,1),result.stderr)
            self.assertNotIn('private application fixture note',result.stdout)
        result=subprocess.run(base+['search','wording'],env=env,text=True,capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout)['verdicts'],'all')
        with TemporaryDirectory() as temp:
            fixture=Path(temp)/'feed.json';fixture.write_text(json.dumps({'jobs':[native()],'meta':{'total':1}}))
            env['JOBWATCH_DATABASE_URL']=self.target.config('jobwatch_collector').dsn
            result=subprocess.run(base+['source','check',str(self.sid),'--actor','collector:fixture','--fixture',str(fixture)],env=env,text=True,capture_output=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout)['evidence_kind'],'fixture')
            self.activate()
            result=subprocess.run(base+['scan','--actor','collector:scheduled','--fixture',str(fixture)],env=env,text=True,capture_output=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout)['counts'],{'published':1})
