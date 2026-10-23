"""The actual one-time importer, independent reconciliation and real PostgreSQL restore."""
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

try:
    import psycopg
except ImportError:
    psycopg=None

from jobwatch.postgres.git_snapshot import ImportProblem
from jobwatch.postgres.import_plan import build_plan
from jobwatch.postgres.importer import apply_import,reconcile,logical_snapshot,_reconcile
from jobwatch.postgres.migrate import migrate
from jobwatch.postgres.publisher import Collector
from import_fixture import repository, KEY, WORDS, native, write, git
from rehearsal import rehearse
from support import ENABLED, Target, DOMAIN_TABLES, actor


@unittest.skipUnless(ENABLED,'Run scripts/test_postgres.sh for disposable PostgreSQL integration tests')
class ImporterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.target=Target.verified();cls.target.reset_schema()
        with cls.target.connect() as conn:
            migrate(conn)
        cls.temp=TemporaryDirectory();cls.addClassCleanup(cls.temp.cleanup)
        cls.repo,cls.commit=repository(cls.temp.name)
        cls.plan=build_plan(cls.repo,cls.commit)

    def setUp(self):
        with self.target.connect() as conn:
            conn.execute('TRUNCATE '+','.join('jobwatch.'+t for t in DOMAIN_TABLES)+' RESTART IDENTITY CASCADE')

    def load(self):
        with self.target.connect() as conn:
            try:
                return apply_import(conn,self.plan)
            except ImportProblem as exc:
                if getattr(exc,'report',None):
                    self.fail(str(exc.report['reconciliation']['failures']))
                raise

    def test_complete_import_preserves_judgments_keys_and_owner_fields(self):
        report=self.load()
        self.assertTrue(report['reconciliation']['ok'])
        self.assertFalse(report['ready_for_cutover'])
        self.assertEqual(report['counts']['postings'],3)
        self.assertEqual(report['distributions']['verdict'],{'review':1,'excluded':2})
        with self.target.connect('jobwatch_owner') as conn:
            row=conn.execute('SELECT * FROM jobwatch.research_postings WHERE posting_key=%s',(KEY,)).fetchone()
            self.assertEqual(row['rules_version'],'fixture-old-rules')
            self.assertEqual(row['codes'],['x.legacy','x.order'])
            self.assertEqual(row['screening_input_sha256'],'a'*64)
            self.assertEqual(row['screening_basis'],'full')
            self.assertEqual(row['screened_at'].isoformat(),'2026-01-02T00:00:00+00:00')
            self.assertEqual(row['last_seen_at'].isoformat(),'2026-01-04T00:00:00+00:00')
            self.assertEqual(row['description_text'],WORDS[-1])
            self.assertEqual(conn.execute('SELECT note FROM jobwatch.triage').fetchone()['note'],'private triage fixture note')
            rows=conn.execute('SELECT * FROM jobwatch.applications ORDER BY job_key').fetchall()
            self.assertEqual(len(rows),3)
            manual=next(r for r in rows if r['job_key']=='manual:stable')
            self.assertIsNone(manual['posting_key'])
            self.assertEqual(manual['notes'],'private application fixture note 2')
            self.assertEqual(manual['cv_version'],'fixture-cv-label')
            self.assertEqual(str(manual['next_action_date']),'2026-11-02')
            states={r['posting_key']:r for r in conn.execute('SELECT posting_key,application_job_key,action_state FROM jobwatch.opportunity_workflow')}
            self.assertEqual(states[KEY]['application_job_key'],KEY)
            self.assertEqual(states['greenhouse:test:2']['application_job_key'],'manual:stable')
            self.assertIsNone(states['greenhouse:test:3']['application_job_key'])
            with conn.transaction():
                actor(conn,'owner:fixture-link')
                conn.execute("UPDATE jobwatch.applications SET posting_key='greenhouse:test:3' WHERE job_key='manual:fragment'")
            self.assertEqual(conn.execute("SELECT job_key FROM jobwatch.applications WHERE posting_key='greenhouse:test:3'").fetchone()['job_key'],'manual:fragment')

    def test_all_historical_descriptions_native_and_mixed_events_recovered(self):
        self.load()
        with self.target.connect() as conn:
            versions=conn.execute('SELECT * FROM jobwatch.posting_versions WHERE posting_key=%s',(KEY,)).fetchall()
            current=conn.execute('SELECT evidence FROM jobwatch.posting_versions WHERE id=(SELECT current_version_id FROM jobwatch.postings WHERE posting_key=%s)',(KEY,)).fetchone()
            evidence=json.loads(bytes(current['evidence']))
            self.assertEqual({v['text'] for v in evidence['descriptions'].values()},set(WORDS))
            for h,v in evidence['descriptions'].items():
                self.assertEqual(sha256(v['text'].encode()).hexdigest(),h)
                self.assertTrue(v['provenance'])
            natives=[v for v in versions if v['origin']=='git_history']
            self.assertEqual(len(natives),1)
            self.assertEqual(json.loads(bytes(natives[0]['evidence'])),native())
            self.assertEqual(natives[0]['observed_at'].isoformat(),'2026-01-01T00:00:00+00:00')
            self.assertNotEqual(natives[0]['id'],conn.execute('SELECT current_version_id FROM jobwatch.postings WHERE posting_key=%s',(KEY,)).fetchone()['current_version_id'])
            events=conn.execute('SELECT * FROM jobwatch.posting_events ORDER BY id').fetchall()
            self.assertTrue(all(e['category']=='import' and e['kind']=='legacy_event' for e in events))
            mixed=[e for e in events if e['changes']['legacy_category']=='mixed']
            self.assertEqual(len(mixed),3)
            self.assertEqual(json.loads(mixed[0]['changes']['original_json'])['changes']['reason'],{'before':'old','after':'new'})
            self.assertTrue(all(e['before_version_id'] is None and e['after_version_id'] is None for e in events))

    def test_missing_historical_evidence_retains_event_and_exact_reference(self):
        with TemporaryDirectory() as temp:
            repo,commit=repository(temp,missing=True);plan=build_plan(repo,commit)
        self.assertEqual(len(plan.manifest['missing_evidence']),1)
        self.assertEqual(plan.manifest['missing_evidence'][0]['sha256'],'f'*64)
        with self.target.connect() as conn:
            report=apply_import(conn,plan)
            self.assertTrue(report['reconciliation']['ok'])
            self.assertFalse(report['ready_for_cutover'])
            events=conn.execute("SELECT changes FROM jobwatch.posting_events WHERE posting_key=%s",(KEY,)).fetchall()
            unavailable=[r for e in events for r in e['changes']['description_references'] if not r['available']]
            self.assertEqual(unavailable,[{'sha256':'f'*64,'available':False,'version_id':next(r['current_version_id'] for r in plan.rows['postings'] if r['posting_key']==KEY)}])
            self.assertTrue(plan.manifest['missing_evidence'][0]['references'][0]['commit'])

    def test_source_activation_and_known_health_are_not_fabricated(self):
        report=self.load()
        self.assertEqual(report['activation_required'],[{'source':'greenhouse:test','legacy_enabled':True,'missing':['endpoint_checked_at']}])
        with self.target.connect() as conn:
            sources={r['board']:r for r in conn.execute('SELECT * FROM jobwatch.sources')}
            self.assertEqual(sources['test']['status'],'candidate');self.assertIsNone(sources['test']['endpoint_checked_at'])
            self.assertEqual(sources['paused']['status'],'paused')
            self.assertEqual(conn.execute("SELECT last_success_at FROM jobwatch.source_health WHERE board='test'").fetchone()['last_success_at'].isoformat(),'2026-01-04T00:00:00+00:00')
            runs=conn.execute('SELECT * FROM jobwatch.collection_runs ORDER BY run_order').fetchall()
            self.assertTrue(all(r['mode']=='import' and r['status']=='imported' and not r['absence_safe'] for r in runs))
            latest=runs[-1]
            old=json.loads(latest['details']['legacy_health_json'])
            self.assertEqual(old['status'],'failure')
            self.assertIsNone(latest['source_records']);self.assertIsNone(latest['skipped_non_vacancies'])
            self.assertIn('last_attempt',old)

    def test_previously_recorded_endpoint_validation_allows_monitoring(self):
        with TemporaryDirectory() as temp:
            repo,commit=repository(temp)
            cfg=json.loads((repo/'config/employers.json').read_text())
            cfg['employers'][0]['endpoint_checked_at']='2026-01-05T00:00:00Z'
            write(repo,'config/employers.json',cfg);git(repo,'add','.');git(repo,'commit','-qm','explicit fixture endpoint check',day=6)
            plan=build_plan(repo,git(repo,'rev-parse','HEAD'))
        with self.target.connect() as conn:
            apply_import(conn,plan)
            self.assertEqual(conn.execute("SELECT status FROM jobwatch.sources WHERE board='test'").fetchone()['status'],'monitored')
        self.assertEqual(plan.manifest['activation_required'],[])

    def test_programme_years_leads_verifications_and_unknown_status(self):
        self.load()
        with self.target.connect() as conn:
            programmes=conn.execute("SELECT * FROM jobwatch.leads WHERE kind='programme' ORDER BY cycle_year").fetchall()
            self.assertEqual([(r['cycle_year'],r['state']) for r in programmes],[(2026,'previous_cycle'),(2027,'open')])
            self.assertEqual(programmes[0]['programme_key'],programmes[1]['programme_key'])
            lead=conn.execute("SELECT * FROM jobwatch.leads WHERE kind='vacancy'").fetchone()
            self.assertEqual(lead['posting_key'],KEY);self.assertIsNone(lead['deadline_date'])
            self.assertEqual(json.loads(lead['details']['legacy_json'])['deadline']['timezone'],'unknown')
            checks=conn.execute('SELECT * FROM jobwatch.verifications').fetchall()
            self.assertEqual(len(checks),3)
            self.assertEqual({r['kind'] for r in checks},{'description','application_status','deadline'})
            self.assertEqual({r['checked_by'] for r in checks},{'agent','unrecorded_legacy'})
            self.assertEqual({r['checked_at'].isoformat() for r in checks},{'2026-01-03T00:00:00+00:00'})
            self.assertFalse(any(r['posting_key']=='greenhouse:test:3' for r in checks))
            self.assertTrue(all(r['actor']=='import:'+self.commit for r in checks))

    def test_refuses_populated_database_and_runtime_credentials(self):
        report=self.load()
        with self.target.connect() as conn:
            before=logical_snapshot(conn)
            with self.assertRaisesRegex(ImportProblem,'contains domain'):
                apply_import(conn,self.plan)
            self.assertEqual(before,logical_snapshot(conn))
        with self.target.connect('jobwatch_owner') as conn,self.assertRaisesRegex(ImportProblem,'42501'):
            apply_import(conn,self.plan)

    def test_embedded_legacy_verification_is_recovered_as_an_independent_claim(self):
        with TemporaryDirectory() as temp:
            repo,commit=repository(temp)
            data=json.loads((repo/'data/jobs.json').read_text())
            data['postings'][KEY]['application_verification']={'status':'closed_verified','checked_at':'2026-01-06T00:00:00Z',
                'url':native()['absolute_url'],'method':'Read-only closed-page fixture'}
            write(repo,'data/jobs.json',data);git(repo,'add','.');git(repo,'commit','-qm','embedded retained observation',day=6)
            plan=build_plan(repo,git(repo,'rev-parse','HEAD'))
        with self.target.connect() as conn:
            apply_import(conn,plan)
            rows=conn.execute("SELECT status,checked_by,legacy_details FROM jobwatch.verifications WHERE kind='application_status' ORDER BY checked_at").fetchall()
        self.assertEqual([r['status'] for r in rows],['open_verified','closed_verified'])
        self.assertEqual(rows[-1]['checked_by'],'unrecorded_legacy')
        self.assertTrue(any(p['path']=='data/jobs.json' and p['pointer'].endswith('/application_verification') for p in rows[-1]['legacy_details']['provenance']))

    def test_late_failure_rolls_back_all_records_audit_and_allows_clean_retry(self):
        def fail(conn,plan):
            self.assertTrue(_reconcile(conn,plan)['reconciliation']['ok'])
            conn.execute('SELECT 1/0')
        with self.target.connect() as conn:
            with patch('jobwatch.postgres.importer._reconcile',side_effect=fail),self.assertRaisesRegex(ImportProblem,'22012'):
                apply_import(conn,self.plan)
            self.assertTrue(all(not conn.execute('SELECT EXISTS(SELECT 1 FROM jobwatch.'+t+') AS e').fetchone()['e'] for t in DOMAIN_TABLES))
            self.assertTrue(apply_import(conn,self.plan)['reconciliation']['ok'])

    def test_unexpected_schema_objects_are_not_silently_adopted(self):
        with self.target.connect() as conn:
            conn.execute('CREATE TABLE jobwatch.unexpected(value text)')
            try:
                with self.assertRaisesRegex(ImportProblem,'Unexpected tables'):
                    apply_import(conn,self.plan)
            finally:
                conn.execute('DROP TABLE jobwatch.unexpected')

    def test_reconciliation_catches_field_changes_and_never_reports_notes(self):
        self.load()
        with self.target.connect() as conn:
            conn.execute("UPDATE jobwatch.postings SET screening_input_sha256=repeat('b',64) WHERE posting_key=%s",(KEY,))
            with conn.transaction():
                actor(conn)
                conn.execute("UPDATE jobwatch.applications SET notes='changed private synthetic note' WHERE job_key='manual:stable'")
            report=reconcile(conn,self.plan)
        self.assertFalse(report['reconciliation']['ok'])
        fields={field for r in report['reconciliation']['failures'] for field in r.get('fields',[])}
        self.assertTrue({'notes','screening_input_sha256'}<=fields)
        serialized=json.dumps(report)
        for private in ('private triage fixture note','changed private synthetic note','private application fixture note'):
            self.assertNotIn(private,serialized)

    def test_stage_two_baseline_and_owner_preservation_after_import(self):
        self.load()
        with self.target.connect('jobwatch_owner') as conn,conn.transaction():
            actor(conn,'owner:test-activation')
            conn.execute("UPDATE jobwatch.sources SET endpoint_checked_at=now(),status='monitored' WHERE board='test'")
            sid=conn.execute("SELECT id FROM jobwatch.sources WHERE board='test'").fetchone()['id']
            owner=conn.execute('SELECT * FROM jobwatch.applications ORDER BY job_key').fetchall()
            audit=conn.execute('SELECT * FROM jobwatch.owner_audit ORDER BY id').fetchall()
        collector=Collector(self.target.config('jobwatch_collector'),actor='collector:after-import')
        response={'jobs':[native(text='New native wording')],'meta':{'total':1}}
        collector.collect_source(sid,get_json=lambda _:response)
        collector.collect_source(sid,get_json=lambda _:response)
        with self.target.connect('jobwatch_owner') as conn:
            events=conn.execute("SELECT * FROM jobwatch.posting_events WHERE category='provider'").fetchall()
            self.assertEqual([r['kind'] for r in events],['baseline'])
            self.assertTrue(events[0]['changes']['baseline']['native_evidence'])
            self.assertEqual(conn.execute('SELECT * FROM jobwatch.applications ORDER BY job_key').fetchall(),owner)
            self.assertEqual(conn.execute('SELECT * FROM jobwatch.owner_audit ORDER BY id').fetchall(),audit)
            self.assertEqual(conn.execute('SELECT count(*) AS n FROM jobwatch.postings').fetchone()['n'],3)

    def test_two_fresh_imports_and_actual_backup_restore_with_owner_records(self):
        result=rehearse(self.repo,self.commit)
        self.assertTrue(result['independent_imports_equal'])
        self.assertTrue(result['restored_matches_source'])
        self.assertTrue(result['schema_ownership_grants_equal'])
        self.assertEqual(result['counts']['applications'],3)
        self.assertEqual(result['counts']['triage'],1)
        self.assertGreater(result['backup']['bytes'],1000)
        self.assertIn('17.',result['backup']['dump_version'])

    def test_explicit_cli_and_reconcile_exit_status(self):
        with TemporaryDirectory() as temp:
            report=Path(temp)/'import.json'
            env={**os.environ,'JOBWATCH_MIGRATION_DATABASE_URL':self.target.config().dsn}
            command=[sys.executable,'-m','jobwatch.postgres.import_cli','import','--repo',str(self.repo),'--commit',self.commit,'--report',str(report)]
            result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn(self.commit,result.stdout)
            self.assertTrue(json.loads(report.read_text())['reconciliation']['ok'])
            self.assertEqual(report.stat().st_mode&0o777,0o600)
            with self.target.connect() as conn:
                conn.execute("UPDATE jobwatch.postings SET rules_version='tampered' WHERE posting_key=%s",(KEY,))
            command[3]='reconcile';command[-1]=str(Path(temp)/'reconcile.json')
            result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,2,result.stderr)
            self.assertFalse(json.loads(Path(command[-1]).read_text())['reconciliation']['ok'])
