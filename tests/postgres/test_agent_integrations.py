"""Least-privilege research and actual cookbook owner writes; no production target."""
import asyncio
import importlib.util
import sys
from copy import deepcopy
from dataclasses import replace
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from jobwatch.postgres.connection import connect
from jobwatch.postgres import reports,calendar_feed
from jobwatch.postgres.owner_tools import OwnerTools
from jobwatch.postgres.read_tools import ReadTools
from support import DatabaseTest,actor

ROOT=Path(__file__).resolve().parents[2]


class AgentIntegrationTests(DatabaseTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        container=os.environ['JOBWATCH_PG_TEST_CONTAINER']
        for filename in ('agent_roles.sql','agent_access.sql','lead_deadline_access.sql','posting_deadline_access.sql'):
            p=subprocess.run(['docker','exec','-i',container,'psql','-X','-U','postgres',
                '-d','jobwatch_foundation_test','-v','ON_ERROR_STOP=1','-f','-'],
                input=(ROOT/'sql/postgresql'/filename).read_text(),text=True,capture_output=True)
            if p.returncode:raise RuntimeError(p.stderr)

    def setUp(self):
        super().setUp()
        self.key,self.vid,_=self.seed()
        self.read=ReadTools(self.target.config('jobwatch_agent_readonly'),shared=True)
        self.refreshed=[]
        self.owner=OwnerTools(self.target.config('jobwatch_agent_owner'),on_commit=lambda:self.refreshed.append(True))

    def change(self,recipe,**values):
        p=self.owner.call('prepare_owner_change',{'recipe':recipe,'values':values})
        self.assertTrue(p['ok'],p)
        return p['data']

    def commit(self,p):
        r=self.owner.call('commit_owner_change',{'change_id':p['change_id'],'review':p['review']})
        self.assertTrue(r['ok'],r)
        return r['data']


    @unittest.skipUnless(importlib.util.find_spec('mcp') is not None,
                         'Install the optional chat-mcp dependencies for the synthetic walkthrough')
    def test_portfolio_walkthrough(self):
        """One disposable source -> real MCP SDK -> authorised owner -> private ICS trace.

        A local synthetic client invokes the write tool, not the ChatGPT approval UI.
        The selected PostgreSQL target is verified by DatabaseTest; no real ATS or AWS.
        """
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        revisit = (datetime.now(timezone.utc) + timedelta(days=5)).date().isoformat()
        read_env = {'PATH': os.defpath, 'PYTHONPATH': str(ROOT/'src'),
                    'PYTHONDONTWRITEBYTECODE': '1',
                    'JOBWATCH_CHAT_DATABASE_URL': self.target.config('jobwatch_agent_readonly').dsn}
        owner_env = {'PATH': os.defpath, 'PYTHONPATH': str(ROOT/'src'),
                     'PYTHONDONTWRITEBYTECODE': '1',
                     'JOBWATCH_AGENT_OWNER_DATABASE_URL': self.target.config('jobwatch_agent_owner').dsn}

        async def exercise():
            rparams = StdioServerParameters(command=sys.executable,
                args=['-m', 'jobwatch.postgres.read_mcp', '--shared'], env=read_env)
            wparams = StdioServerParameters(command=sys.executable,
                args=['-m', 'jobwatch.postgres.owner_mcp'], env=owner_env)
            with open(os.devnull, 'w') as errors:
                async with stdio_client(rparams, errlog=errors) as (rr, rt), ClientSession(rr, rt) as research:
                    await research.initialize()
                    listing = await research.call_tool('sql_query', {
                        'sql': 'SELECT posting_key,title,verdict FROM jobwatch.research_postings ORDER BY posting_key',
                        'limit': 5})
                    self.assertTrue(listing.structuredContent['ok'], listing.structuredContent)
                    rows = listing.structuredContent['data']['rows']
                    self.assertEqual(len(rows), 1)
                    self.assertEqual(rows[0][0], self.key)
                    self.assertEqual(rows[0][1], 'Graduate Software Engineer')
                    print('DEMO 1/5  Research MCP: one synthetic job found; verdict = ' + rows[0][2])

                    evidence = await research.call_tool('inspect_evidence', {
                        'posting_key': self.key, 'events_limit': 5})
                    self.assertTrue(evidence.structuredContent['ok'], evidence.structuredContent)
                    data = evidence.structuredContent['data']
                    self.assertEqual(data['posting_key'], self.key)
                    self.assertEqual(data['untrusted_evidence']['data']['description'],
                                     'Build Python services')
                    self.assertEqual(data['untrusted_evidence']['trust'], 'untrusted_source_data')
                    print('DEMO 2/5  Evidence MCP: exact parsed source and provenance inspected')

                    async with stdio_client(wparams, errlog=errors) as (wr, wt), ClientSession(wr, wt) as owner:
                        await owner.initialize()
                        later = await owner.call_tool('prepare_owner_change', {
                            'recipe': 'set_triage',
                            'values': {'posting_key': self.key, 'decision': 'later',
                                       'reason': 'role_fit', 'revisit_on': revisit}})
                        self.assertTrue(later.structuredContent['ok'], later.structuredContent)
                        approved = later.structuredContent['data']
                        self.assertEqual(approved['review']['values']['decision'], 'later')
                        self.assertEqual(approved['review']['values']['revisit_on'], revisit)

                        stale = await owner.call_tool('prepare_owner_change', {
                            'recipe': 'set_triage',
                            'values': {'posting_key': self.key, 'decision': 'interested',
                                       'reason': 'role_fit', 'revisit_on': ''}})
                        self.assertTrue(stale.structuredContent['ok'], stale.structuredContent)
                        outdated = stale.structuredContent['data']

                        # Simulated confirmation in a disposable fixture. Real ChatGPT
                        # confirmation is a separate, client-side security requirement.
                        committed = await owner.call_tool('commit_owner_change', {
                            'change_id': approved['change_id'], 'review': approved['review']})
                        self.assertTrue(committed.structuredContent['ok'], committed.structuredContent)
                        self.assertEqual(committed.structuredContent['data']['db_login'], 'jobwatch_agent_owner')
                        print('DEMO 3/5  Owner MCP: prepared review committed by synthetic client; audited')

                        rejected = await owner.call_tool('commit_owner_change', {
                            'change_id': outdated['change_id'], 'review': outdated['review']})
                        self.assertFalse(rejected.structuredContent['ok'])
                        self.assertEqual(rejected.structuredContent['error']['code'], 'stale_state')
                        print('DEMO 4/5  Owner MCP: competing stale review refused (stale_state)')

        asyncio.run(exercise())
        with self.target.connect('jobwatch_calendar_reader') as conn:
            snapshot = reports.calendar_data(conn, shared=True)
        files, manifest = calendar_feed.build(snapshot)
        self.assertEqual(manifest['files']['actions.ics']['events'], 1)
        self.assertIn(b'BEGIN:VEVENT', files['actions.ics'])
        self.assertIn(b'Revisit: Test', files['actions.ics'])
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.owner_audit "
                         "WHERE table_name='triage' AND db_login='jobwatch_agent_owner'"), 1)
        self.assertEqual(str(self.scalar("SELECT revisit_on FROM jobwatch.triage")), revisit)
        print('DEMO 5/5  Calendar: one minimal synthetic revisit event generated for ' + revisit)

    def test_shared_complex_sql_reports_and_private_column_denials(self):
        with self.target.connect('jobwatch_owner') as c,c.transaction():
            actor(c);c.execute("INSERT INTO jobwatch.applications(job_key,company,role,url,notes,cv_version) VALUES ('manual:one','Synthetic','Role','https://example.org/manual','PRIVATE_NOTE','PRIVATE_CV')")
        for sql in ('SELECT notes FROM jobwatch.applications','SELECT cv_version FROM jobwatch.applications',
                'SELECT * FROM jobwatch.owner_audit','SELECT details FROM jobwatch.employers',
                'SELECT legacy_details FROM jobwatch.verifications',
                'SELECT details FROM jobwatch.leads','SELECT note FROM jobwatch.triage'):
            r=self.read.call('sql_query',{'sql':sql,'limit':5});self.assertFalse(r['ok'],sql)
        for tool,args in [('sql_schema',{'table':'postings'}),('sql_query',{'sql':'WITH all_roles AS (SELECT p.posting_key,p.verdict,e.name FROM jobwatch.postings p JOIN jobwatch.sources s ON s.id=p.source_id JOIN jobwatch.employers e ON e.id=s.employer_id) SELECT * FROM all_roles','limit':5}),
                          ('inspect_evidence',{'posting_key':self.key}),('search',{'query':'Role','method':'bm25'}),('search',{'query':'Role','method':'fts'})]+[('run_report',{'kind':k}) for k in ('daily','weekly','requirements','calendar')]:
            r=self.read.call(tool,args);self.assertTrue(r['ok'],r)
            self.assertNotIn('PRIVATE_NOTE',json.dumps(r));self.assertNotIn('PRIVATE_CV',json.dumps(r))

    def test_natural_posting_application_followup_replay_calendar_completion(self):
        created=self.change('create_application',posting_key=self.key)
        self.commit(created)
        self.assertTrue(self.commit(created)['replayed'])
        due=(datetime.now(timezone.utc)+timedelta(days=3)).date().isoformat()
        p=self.change('set_followup',job_key=self.key,action='Review requirements',action_date=due)
        self.commit(p)
        with self.target.connect('jobwatch_calendar_reader') as c:
            data=reports.calendar_data(c,shared=True)
        files,manifest=calendar_feed.build(data)
        self.assertIn(b'Follow up:',files['actions.ics'])
        self.assertNotIn(b'Review requirements',files['actions.ics'])
        self.commit(self.change('set_followup',job_key=self.key,action='',action_date=''))
        with self.target.connect('jobwatch_calendar_reader') as c:data=reports.calendar_data(c,shared=True)
        files,_=calendar_feed.build(data,manifest)
        self.assertNotIn(b'BEGIN:VEVENT',files['actions.ics'])
        self.assertEqual(len(self.refreshed),3)
        with self.target.connect() as c:
            row=c.execute("SELECT db_login,actor FROM jobwatch.owner_audit WHERE table_name='applications' ORDER BY id DESC LIMIT 1").fetchone()
            self.assertEqual(row['db_login'],'jobwatch_agent_owner');self.assertIn('agent_on_owner_instruction',row['actor'])

    def test_stale_tamper_invalid_replay_and_private_note_preservation(self):
        p=self.change('set_triage',posting_key=self.key,decision='later',reason='role_fit',revisit_on='2026-12-01')
        wrong=deepcopy(p['review']);wrong['values']['decision']='dismissed'
        self.assertFalse(self.owner.call('commit_owner_change',{'change_id':p['change_id'],'review':wrong})['ok'])
        with self.target.connect('jobwatch_owner') as c,c.transaction():
            actor(c);c.execute("INSERT INTO jobwatch.triage(posting_key,decision,note,recorded_by) VALUES (%s,'interested','PRIVATE_NOTE','owner')",(self.key,))
        r=self.owner.call('commit_owner_change',{'change_id':p['change_id'],'review':p['review']});self.assertFalse(r['ok'])
        p=self.change('set_triage',posting_key=self.key,decision='later',reason='role_fit',revisit_on='2026-12-01')
        self.assertNotIn('PRIVATE_NOTE',json.dumps(p));self.commit(p)
        self.assertEqual(self.scalar('SELECT note FROM jobwatch.triage'),'PRIVATE_NOTE')
        self.assertFalse(self.owner.call('prepare_owner_change',{'recipe':'sql_query','values':{'sql':'DELETE FROM jobwatch.postings'}})['ok'])
        self.assertFalse(self.owner.call('prepare_owner_change',{'recipe':'set_triage','values':{'actor':'owner','posting_key':self.key}})['ok'])

    def test_manual_application_link_stage_and_lead_link_keep_identity(self):
        self.commit(self.change('create_application',job_key='manual:synthetic',company='Synthetic',
            role='Manual role',url='https://example.org/manual'))
        linked=self.change('link_application',job_key='manual:synthetic',posting_key=self.key)
        self.assertEqual(linked['review']['target']['link_to']['posting_key'],self.key)
        self.commit(linked)
        self.commit(self.change('update_stage',job_key='manual:synthetic',stage='applied',date_applied='2026-10-27'))
        self.assertEqual(self.scalar('SELECT job_key FROM jobwatch.applications'),'manual:synthetic')
        self.assertEqual(self.scalar('SELECT stage FROM jobwatch.applications'),'applied')
        self.commit(self.change('record_lead',lead_key='vacancy:synthetic',kind='vacancy',
            company='Synthetic',title='Unlinked',state='unverified',official=False,
            source_note='Synthetic source',url='https://example.org/manual',wording='Observed wording',
            checked_at=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()))
        linked=self.change('link_lead',lead_key='vacancy:synthetic',posting_key=self.key)
        self.commit(linked)
        self.assertEqual(self.scalar('SELECT posting_key FROM jobwatch.leads'),self.key)

    def test_write_role_cannot_publish_admin_or_read_private_images(self):
        import psycopg
        for query in ("UPDATE jobwatch.postings SET verdict='priority'",'DELETE FROM jobwatch.owner_audit',
                "UPDATE jobwatch.sources SET status='paused'",'SELECT old_row FROM jobwatch.owner_audit',
                'SELECT notes FROM jobwatch.applications','CREATE TABLE jobwatch.forbidden(id int)'):
            with self.target.connect('jobwatch_agent_owner') as c:
                with self.assertRaises(psycopg.Error),c.transaction():
                    c.execute('SET TRANSACTION READ WRITE');c.execute("SELECT set_config('jobwatch.actor','attempt',true)");c.execute(query)

    def test_deadline_sql_selection_and_triage_calendar_need_no_application(self):
        due=(datetime.now(timezone.utc)+timedelta(days=4)).date().isoformat()
        with self.target.connect('jobwatch_owner') as c,c.transaction():
            actor(c)
            c.execute("INSERT INTO jobwatch.verifications(posting_key,kind,checked_at,checked_by,url,wording,value) VALUES (%s,'deadline',now()-interval '1 minute','owner','https://example.org/jobs/1','Apply by the explicit date',%s)",(self.key,due))
        self.commit(self.change('set_triage',posting_key=self.key,decision='later',reason='role_fit',revisit_on=due))
        with self.target.connect('jobwatch_calendar_reader') as c:
            all_data=reports.calendar_data(c,shared=True)
            selected=reports.calendar_data(c,shared=True,deadline_selection_sql="SELECT posting_key AS evidence_key FROM jobwatch.postings WHERE false")
        # Compare selection behavior at one publication time, even across a UTC second boundary.
        selected['generated_at']=all_data['generated_at']
        all_files,_=calendar_feed.build(all_data);limited,_=calendar_feed.build(selected)
        self.assertIn(b'BEGIN:VEVENT',all_files['deadlines.ics'])
        self.assertIn(b'BEGIN:VEVENT',all_files['actions.ics'])
        self.assertNotIn(b'BEGIN:VEVENT',limited['deadlines.ics'])
        self.assertEqual(all_files['actions.ics'],limited['actions.ics'])
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.applications'),0)
        with self.target.connect('jobwatch_calendar_reader') as c:
            with self.assertRaises(Exception):reports.calendar_data(c,shared=True,deadline_selection_sql="DELETE FROM jobwatch.postings RETURNING posting_key AS evidence_key")
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.postings'),1)

    def test_evidenced_programme_input_is_bound_and_future_observation_refused(self):
        now=datetime.now(timezone.utc)
        values=dict(lead_key='programme:synthetic:2027',kind='programme',company='Synthetic',title='Graduate 2027',
            programme_key='synthetic-grad',cycle_year='2027',cycle_wording='2027 intake',state='open',
            url='https://example.org/graduate',official=True,source_note='Official page observed',
            wording="Apply by 2026-11-15; '); DELETE FROM jobwatch.postings; --",deadline_date='2026-11-15',
            deadline_wording='Apply by 15 November 2026',checked_at=(now-timedelta(seconds=1)).isoformat())
        p=self.change('record_lead',**values);self.commit(p)
        with self.target.connect('jobwatch_owner') as c,c.transaction():
            actor(c)
            legacy=json.dumps({'deadline':{'url':'https://example.org/official-deadline'},'note':'PRIVATE_LEGACY_NOTE'})
            c.execute("UPDATE jobwatch.leads SET details=jsonb_build_object('legacy_json',%s::text) WHERE lead_key=%s",(legacy,values['lead_key']))
        result=self.read.call('sql_query',{'sql':'SELECT details FROM jobwatch.agent_leads','limit':5})
        self.assertNotIn('PRIVATE_LEGACY_NOTE',json.dumps(result))
        with self.target.connect('jobwatch_calendar_reader') as c:
            projected=reports.calendar_data(c,shared=True)
        lead=next(d for d in projected['deadlines'] if d.get('lead_key')==values['lead_key'])
        self.assertEqual(lead['url'],'https://example.org/official-deadline')
        self.assertTrue(self.commit(p)['replayed'])
        self.assertEqual(self.scalar('SELECT cycle_year FROM jobwatch.leads'),2027)
        self.assertEqual(str(self.scalar('SELECT deadline_date FROM jobwatch.leads')),'2026-11-15')
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.postings'),1)
        bad={**values,'checked_at':(now+timedelta(days=1)).isoformat()}
        self.assertFalse(self.owner.call('prepare_owner_change',{'recipe':'record_lead','values':bad})['ok'])

    def deadline_lead(self):
        self.commit(self.change('record_lead',lead_key='vacancy:deadline-test',kind='vacancy',
            company='Synthetic',title='Deadline role',state='listed',official=True,
            source_note='PRIVATE_SOURCE',url='https://example.org/manual-deadline',
            wording='Original observation',deadline_date='2026-11-10',
            deadline_wording='Apply by 10 November',
            checked_at=(datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat()))
        with self.target.connect('jobwatch_owner') as c,c.transaction():
            actor(c)
            c.execute("UPDATE jobwatch.leads SET details=%s::jsonb WHERE lead_key='vacancy:deadline-test'",
                (json.dumps({'legacy_json':json.dumps({'deadline':{'url':'https://example.org/old-source'},
                    'note':'PRIVATE_LEGACY'})}),))

    def correction(self,**overrides):
        return dict(lead_key='vacancy:deadline-test',deadline_date='2026-11-20',
            deadline_wording='Applications close 20 November 2026',
            deadline_source_url='https://example.org/new-source',deadline_official=True,
            deadline_checked_at=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),**overrides)

    def deadline_data(self):
        with self.target.connect('jobwatch_calendar_reader') as c:
            return reports.calendar_data(c,shared=True)

    def test_correct_lead_deadline_provenance_audit_replay_and_calendar_revision(self):
        self.deadline_lead()
        before=self.deadline_data()
        claim=next(d for d in before['deadlines'] if d.get('lead_key')=='vacancy:deadline-test')
        self.assertEqual(claim['url'],'https://example.org/old-source')
        files,manifest=calendar_feed.build(before)
        values=self.correction()
        p=self.change('correct_lead_deadline',**values)
        self.assertNotIn('PRIVATE_',json.dumps(p))
        audit_before=self.scalar('SELECT count(*) FROM jobwatch.owner_audit')
        receipt=self.commit(p)
        self.assertTrue(receipt['calendar_refresh_queued'])
        self.assertEqual(receipt['row']['deadline_source_url'],values['deadline_source_url'])
        self.assertTrue(self.commit(p)['replayed'])
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.owner_audit'),audit_before+1)
        with self.target.connect() as c:
            audit=c.execute("SELECT * FROM jobwatch.owner_audit WHERE table_name='leads' ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(audit['db_login'],'jobwatch_agent_owner')
        self.assertEqual(audit['actor'],receipt['actor'])
        self.assertEqual(audit['old_row']['deadline_date'],'2026-11-10')
        self.assertEqual(audit['new_row']['deadline_date'],'2026-11-20')
        for field in ('details','source_note','checked_at','checked_by','official','url','wording','state'):
            self.assertEqual(audit['old_row'][field],audit['new_row'][field],field)
        after=self.deadline_data()
        claim=next(d for d in after['deadlines'] if d.get('lead_key')=='vacancy:deadline-test')
        self.assertEqual(claim['url'],values['deadline_source_url'])
        self.assertEqual(claim['checked_by'],receipt['actor'])
        self.assertEqual(claim['checked_at'].isoformat(),receipt['row']['deadline_checked_at'])
        updated,new_manifest=calendar_feed.build(after,manifest)
        self.assertEqual(set(manifest['events']),set(new_manifest['events']))
        uid=next(iter(manifest['events']))
        self.assertEqual(new_manifest['events'][uid]['sequence'],manifest['events'][uid]['sequence']+1)
        self.assertIn(b'20261120',updated['deadlines.ics'])
        self.assertNotIn(b'PRIVATE_',updated['deadlines.ics'])
        self.assertNotEqual(files['deadlines.ics'],updated['deadlines.ics'])
        # Full owner projection and sanitized shared projection agree on corrected provenance.
        with self.target.connect('jobwatch_owner') as c:full=reports.calendar_data(c)
        self.assertEqual(full['deadlines'],after['deadlines'])

    def test_correct_lead_deadline_stale_tampered_invalid_and_unofficial(self):
        self.deadline_lead()
        p=self.change('correct_lead_deadline',**self.correction())
        changed=deepcopy(p['review']);changed['values']['deadline_date']='2026-11-30'
        result=self.owner.call('commit_owner_change',{'change_id':p['change_id'],'review':changed})
        self.assertEqual(result['error']['code'],'review_mismatch')
        # Even a change confined to the legacy deadline source makes the review stale.
        with self.target.connect('jobwatch_owner') as c,c.transaction():
            actor(c);c.execute("UPDATE jobwatch.leads SET details='{}' WHERE lead_key='vacancy:deadline-test'")
        count=self.scalar('SELECT count(*) FROM jobwatch.owner_audit')
        result=self.owner.call('commit_owner_change',{'change_id':p['change_id'],'review':p['review']})
        self.assertEqual(result['error']['code'],'stale_state')
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.owner_audit'),count)
        for patch in ({'deadline_wording':''},{'deadline_source_url':'javascript:bad'},
                {'deadline_checked_at':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()},
                {'deadline_checked_at':'2026-10-01T12:00:00'},{'deadline_date':'invented'}):
            values={**self.correction(),**patch}
            self.assertFalse(self.owner.call('prepare_owner_change',{'recipe':'correct_lead_deadline','values':values})['ok'])
        values={**self.correction(),'deadline_official':False}
        self.commit(self.change('correct_lead_deadline',**values))
        self.assertNotIn(b'BEGIN:VEVENT',calendar_feed.build(self.deadline_data())[0]['deadlines.ics'])

    def test_correct_lead_deadline_competing_preparations_and_permissions(self):
        import psycopg
        self.deadline_lead()
        a=self.change('correct_lead_deadline',**self.correction())
        b=self.change('correct_lead_deadline',**{**self.correction(),'deadline_date':'2026-11-21'})
        self.commit(a)
        result=self.owner.call('commit_owner_change',{'change_id':b['change_id'],'review':b['review']})
        self.assertEqual(result['error']['code'],'stale_state')
        for role in ('jobwatch_agent_readonly','jobwatch_calendar_reader','jobwatch_collector'):
            with self.target.connect(role) as c:
                with self.assertRaises(psycopg.Error),c.transaction():
                    c.execute('SET TRANSACTION READ WRITE')
                    actor(c)
                    c.execute("UPDATE jobwatch.leads SET deadline_date='2026-11-25'")
        for column,value in (('checked_by',"'owner'"),('url',"'https://example.org/other'"),
                ('official','false'),('details',"'{}'::jsonb")):
            with self.target.connect('jobwatch_agent_owner') as c:
                with self.assertRaises(psycopg.Error),c.transaction():
                    c.execute('SET TRANSACTION READ WRITE');actor(c)
                    c.execute('UPDATE jobwatch.leads SET '+column+'='+value)

    def posting_check(self,**changes):
        return dict(posting_key=self.key,value='2026-11-12',wording='Applications close 12 November 2026',
            url='https://example.org/official-deadline',
            checked_at=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),
            supersedes=[],reason='Independently checked the official vacancy page',**changes)

    def posting_claims(self):
        with self.target.connect('jobwatch_agent_readonly') as c:
            return c.execute('SELECT * FROM jobwatch.posting_deadline_claims WHERE posting_key=%s ORDER BY claim_id',
                (self.key,)).fetchall()

    def feed_claim(self,value='2026-11-10',observed_at='2026-10-23T12:00:00Z',*,new_version=False):
        from support import version,run
        with self.target.connect() as c,c.transaction():
            actor(c)
            if new_version:
                attempt=run(c,self.source)
                vid=version(c,self.key,self.source,attempt['id'],
                    evidence={'id':'1','description':'Build Python services','deadline':value,'observed_at':observed_at},
                    title='Deadline fixture '+str(attempt['id']))
                c.execute('UPDATE jobwatch.postings SET current_version_id=%s WHERE posting_key=%s',(vid,self.key))
            else:vid=self.vid
            evidence=[dict(value=value,wording='Apply by '+value,url='https://example.org/feed-deadline',
                observed_at=observed_at,source_field='application_deadline')]
            c.execute('UPDATE jobwatch.posting_versions SET deadlines=%s::jsonb WHERE id=%s',(json.dumps(evidence),vid))
        return next(c['claim_id'] for c in self.posting_claims() if c['provenance']=='feed_observation' and c['value']==value
            and c['checked_at']==observed_at)

    def test_posting_deadline_new_check_audit_receipt_and_retries(self):
        p=self.change('verify_posting_deadline',**self.posting_check())
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.verifications'),0)
        receipt=self.commit(p)
        self.assertTrue(self.commit(p)['replayed'])
        self.assertEqual(receipt['row']['value'],'2026-11-12')
        self.assertEqual(receipt['row']['deadline_supersedes'],[])
        self.assertEqual(receipt['row']['checked_by'],'agent_on_owner_instruction')
        self.assertEqual(receipt['row']['actor'],receipt['actor'])
        with self.target.connect() as c:
            audit=c.execute("SELECT * FROM jobwatch.owner_audit WHERE table_name='verifications'").fetchall()
        self.assertEqual(len(audit),1)
        self.assertIsNone(audit[0]['old_row'])
        self.assertEqual(audit[0]['new_row']['id'],receipt['row']['id'])
        self.assertEqual(audit[0]['db_login'],'jobwatch_agent_owner')
        self.assertEqual(audit[0]['actor'],receipt['actor'])
        # Fixed-ID cookbook retry after reconnect is also idempotent, despite changed state.
        from jobwatch.postgres.owner_tools import execute_recipe
        with self.owner.session(write=True) as c:
            actor(c,receipt['actor'])
            execute_recipe(c,'verify_posting_deadline',{**p['review']['values'],'actor':receipt['actor']})
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.verifications'),1)
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.owner_audit WHERE table_name='verifications'"),1)
        self.assertIn(b'20261112',calendar_feed.build(self.deadline_data())[0]['deadlines.ics'])

    def test_posting_deadline_explicit_feed_correction_stable_calendar_and_history(self):
        old=self.feed_claim()
        original=self.scalar('SELECT deadlines FROM jobwatch.posting_versions WHERE id=%s',(self.vid,))
        files,manifest=calendar_feed.build(self.deadline_data())
        values={**self.posting_check(),'supersedes':[old],'reason':'Official page explicitly extends the feed date'}
        receipt=self.commit(self.change('verify_posting_deadline',**values))
        data=self.deadline_data()
        inactive=next(c for c in data['deadlines'] if c.get('claim_id')==old)
        self.assertFalse(inactive['active'])
        self.assertEqual(inactive['value'],'2026-11-10')
        self.assertEqual(inactive['superseded_by'],['verification:'+receipt['row']['id']])
        self.assertFalse(any(c['date_conflict'] for c in data['deadlines']))
        updated,state=calendar_feed.build(data,manifest)
        self.assertEqual(set(manifest['events']),set(state['events']))
        uid=next(iter(manifest['events']))
        self.assertEqual(state['events'][uid]['sequence'],manifest['events'][uid]['sequence']+1)
        self.assertIn(b'20261112',updated['deadlines.ics'])
        self.assertNotEqual(files['deadlines.ics'],updated['deadlines.ics'])
        self.assertEqual(self.scalar('SELECT deadlines FROM jobwatch.posting_versions WHERE id=%s',(self.vid,)),original)
        # A subsequent contradictory observation has a new identity and remains active.
        later=self.feed_claim('2026-11-10','2026-10-24T12:00:00Z',new_version=True)
        self.assertNotEqual(later,old)
        data=self.deadline_data()
        self.assertTrue(next(c for c in data['deadlines'] if c.get('claim_id')==later)['active'])
        self.assertFalse(next(c for c in data['deadlines'] if c.get('claim_id')==old)['active'])
        omitted,omitted_state=calendar_feed.build(data,state)
        self.assertNotIn(b'BEGIN:VEVENT',omitted['deadlines.ics'])
        # Explicitly resolving that later claim restores the same UID with a later revision.
        self.commit(self.change('verify_posting_deadline',**{**self.posting_check(),'supersedes':[later]}))
        restored,state2=calendar_feed.build(self.deadline_data(),omitted_state)
        self.assertIn(b'BEGIN:VEVENT',restored['deadlines.ics'])
        self.assertIn(uid,state2['events'])
        self.assertGreater(state2['events'][uid]['sequence'],state['events'][uid]['sequence'])

    def test_posting_deadline_unrelated_sources_and_chain_supersession(self):
        feed=self.feed_claim()
        a=self.commit(self.change('verify_posting_deadline',**self.posting_check()))
        aid='verification:'+a['row']['id']
        self.assertNotIn(b'BEGIN:VEVENT',calendar_feed.build(self.deadline_data())[0]['deadlines.ics'])
        # Replacing only the check does not resolve the unrelated feed claim.
        b=self.commit(self.change('verify_posting_deadline',**{**self.posting_check(),
            'value':'2026-11-14','supersedes':[aid]}))
        self.assertNotIn(b'BEGIN:VEVENT',calendar_feed.build(self.deadline_data())[0]['deadlines.ics'])
        bid='verification:'+b['row']['id']
        self.commit(self.change('verify_posting_deadline',**{**self.posting_check(),
            'value':'2026-11-16','supersedes':[feed,bid]}))
        claims=self.posting_claims()
        self.assertEqual(sum(not c['superseded_by'] for c in claims),1)
        self.assertEqual(len(claims),4)
        self.assertIn(b'20261116',calendar_feed.build(self.deadline_data())[0]['deadlines.ics'])
        # Even a duplicate date/source/quote is a separate independently observed check.
        self.commit(self.change('verify_posting_deadline',**{**self.posting_check(),'value':'2026-11-14','url':'https://example.org/another-source'}))
        self.assertNotIn(b'BEGIN:VEVENT',calendar_feed.build(self.deadline_data())[0]['deadlines.ics'])

    def test_posting_deadline_copied_feed_observation_keeps_claim_identity(self):
        old=self.feed_claim()
        copied=self.feed_claim(new_version=True)
        self.assertEqual(old,copied)
        self.assertEqual(len(self.posting_claims()),1)
        self.commit(self.change('verify_posting_deadline',**{**self.posting_check(),'supersedes':[old]}))
        self.feed_claim(new_version=True)
        claims=self.posting_claims()
        self.assertEqual(len(claims),2)
        self.assertTrue(next(c for c in claims if c['claim_id']==old)['superseded_by'])
        self.assertIn(b'BEGIN:VEVENT',calendar_feed.build(self.deadline_data())[0]['deadlines.ics'])

    def test_posting_deadline_stale_checks_and_new_feed_refuse_write(self):
        a=self.change('verify_posting_deadline',**self.posting_check())
        b=self.change('verify_posting_deadline',**self.posting_check())
        self.commit(a)
        result=self.owner.call('commit_owner_change',{'change_id':b['change_id'],'review':b['review']})
        self.assertEqual(result['error']['code'],'stale_state')
        p=self.change('verify_posting_deadline',**self.posting_check())
        self.feed_claim(new_version=True)
        result=self.owner.call('commit_owner_change',{'change_id':p['change_id'],'review':p['review']})
        self.assertEqual(result['error']['code'],'stale_state')
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.verifications'),1)
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.owner_audit WHERE table_name='verifications'"),1)

    def test_posting_deadline_concurrent_corrections_have_one_winner(self):
        from concurrent.futures import ThreadPoolExecutor
        claim=self.feed_claim()
        prepared=[self.change('verify_posting_deadline',**{**self.posting_check(),'value':value,
            'supersedes':[claim]}) for value in ('2026-11-12','2026-11-14')]
        def write(p):
            return self.owner.call('commit_owner_change',{'change_id':p['change_id'],'review':p['review']})
        with ThreadPoolExecutor(2) as pool:results=list(pool.map(write,prepared))
        self.assertEqual(sum(r['ok'] for r in results),1,results)
        self.assertEqual(next(r['error']['code'] for r in results if not r['ok']),'stale_state')
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.verifications'),1)
        self.assertEqual(sum(not c['superseded_by'] for c in self.posting_claims()),1)

    def test_posting_deadline_invalid_targets_inputs_and_database_permissions(self):
        import psycopg
        for patch in ({'supersedes':['verification:missing']},{'supersedes':['x','x']},
                {'supersedes':'[]'},{'url':'javascript:bad'},{'reason':''},{'value':'unknown'},
                {'checked_at':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}):
            r=self.owner.call('prepare_owner_change',{'recipe':'verify_posting_deadline','values':{**self.posting_check(),**patch}})
            self.assertFalse(r['ok'],r)
        p=self.change('verify_posting_deadline',**self.posting_check())
        bad=deepcopy(p['review']);bad['values']['supersedes']='["unapproved"]'
        self.assertEqual(self.owner.call('commit_owner_change',{'change_id':p['change_id'],'review':bad})['error']['code'],'review_mismatch')
        for role in ('jobwatch_agent_readonly','jobwatch_calendar_reader','jobwatch_collector'):
            with self.target.connect(role) as c:
                with self.assertRaises(psycopg.Error),c.transaction():
                    c.execute('SET TRANSACTION READ WRITE');actor(c)
                    c.execute("INSERT INTO jobwatch.verifications(posting_key,kind,checked_at,checked_by,url,wording,value) VALUES (%s,'deadline',now(),'owner','https://example.org','Quote','2026-11-12')",(self.key,))
        for sql in ("UPDATE jobwatch.verifications SET value='2026-11-20'",'DELETE FROM jobwatch.verifications',
                "UPDATE jobwatch.posting_versions SET deadlines='[]'", "UPDATE jobwatch.postings SET verdict='priority'",
                "INSERT INTO jobwatch.verifications(posting_key,kind,checked_at,checked_by,url,wording,value,deadline_supersedes,deadline_reason) VALUES (%s,'deadline',now(),'agent_on_owner_instruction','https://example.org','Quote','2026-11-12',jsonb_build_array('feed:missing'),'Reason')"):
            with self.target.connect('jobwatch_agent_owner') as c:
                with self.assertRaises(psycopg.Error),c.transaction():
                    c.execute('SET TRANSACTION READ WRITE');actor(c)
                    c.execute(sql,(self.key,) if '%s' in sql else None)
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.verifications'),0)

    def test_posting_deadline_unconfirmed_commit_requires_research_before_retry(self):
        from contextlib import contextmanager
        from unittest.mock import patch
        from jobwatch.postgres.owner_tools import OwnerError
        p=self.change('verify_posting_deadline',**self.posting_check())
        real=self.owner.session
        @contextmanager
        def interrupted(*,write=False):
            if not write:raise OwnerError('read_back_failed')
            with real(write=write) as c:yield c
        with patch.object(self.owner,'session',interrupted):
            r=self.owner.call('commit_owner_change',{'change_id':p['change_id'],'review':p['review']})
        self.assertFalse(r['ok'])
        retry=self.owner.call('commit_owner_change',{'change_id':p['change_id'],'review':p['review']})
        self.assertEqual(retry['error']['code'],'write_unconfirmed')
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.verifications'),1)
        self.assertEqual(self.posting_claims()[0]['claim_id'],'verification:'+p['review']['values']['verification_id'])

    def test_official_mcp_clients_composed_research_and_confirmed_owner_change(self):
        from mcp import ClientSession,StdioServerParameters
        from mcp.client.stdio import stdio_client
        async def exercise():
            read_env={'PATH':os.defpath,'PYTHONPATH':str(ROOT/'src'),'PYTHONDONTWRITEBYTECODE':'1',
                'JOBWATCH_CHAT_DATABASE_URL':self.target.config('jobwatch_agent_readonly').dsn}
            owner_env={'PATH':os.defpath,'PYTHONPATH':str(ROOT/'src'),'PYTHONDONTWRITEBYTECODE':'1',
                'JOBWATCH_AGENT_OWNER_DATABASE_URL':self.target.config('jobwatch_agent_owner').dsn}
            rparams=StdioServerParameters(command=sys.executable,args=['-m','jobwatch.postgres.read_mcp','--shared'],env=read_env)
            wparams=StdioServerParameters(command=sys.executable,args=['-m','jobwatch.postgres.owner_mcp'],env=owner_env)
            with open(os.devnull,'w') as errors:
                async with stdio_client(rparams,errlog=errors) as (rr,rt),ClientSession(rr,rt) as read:
                    await read.initialize()
                    rows=await read.call_tool('sql_query',{'sql':'SELECT posting_key,title FROM jobwatch.research_postings ORDER BY posting_key','limit':5})
                    key=rows.structuredContent['data']['rows'][0][0]
                    async with stdio_client(wparams,errlog=errors) as (wr,wt),ClientSession(wr,wt) as write:
                        await write.initialize();tools=(await write.list_tools()).tools
                        self.assertEqual({t.name for t in tools},{'prepare_owner_change','commit_owner_change'})
                        self.assertFalse(next(t for t in tools if t.name=='commit_owner_change').annotations.readOnlyHint)
                        prepared=await write.call_tool('prepare_owner_change',{'recipe':'set_triage','values':{'posting_key':key,'decision':'interested','reason':'role_fit','revisit_on':''}})
                        change=prepared.structuredContent['data']
                        # Synthetic client confirmation; not proof of the real ChatGPT UI.
                        result=await write.call_tool('commit_owner_change',{'change_id':change['change_id'],'review':change['review']})
                        self.assertTrue(result.structuredContent['ok'])
                        self.assertEqual(result.structuredContent['data']['db_login'],'jobwatch_agent_owner')
        asyncio.run(exercise())

    def test_official_http_client_over_permissioned_unix_socket(self):
        import httpx
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        with tempfile.TemporaryDirectory(prefix='gjw-mcp-') as directory:
            os.chmod(directory,0o700)
            path=Path(directory)/'mcp.sock'
            env={'PATH':os.defpath,'PYTHONPATH':str(ROOT/'src'),'PYTHONDONTWRITEBYTECODE':'1',
                 'JOBWATCH_CHAT_DATABASE_URL':self.target.config('jobwatch_agent_readonly').dsn}
            with open(os.devnull,'w') as errors:
                process=subprocess.Popen([sys.executable,'-m','jobwatch.postgres.read_mcp','--shared',
                    '--transport','streamable-http','--unix-socket',str(path)],env=env,stdout=errors,stderr=errors)
                try:
                    for _ in range(100):
                        if path.exists():break
                        if process.poll() is not None:self.fail('Unix MCP exited before readiness')
                        time.sleep(.05)
                    self.assertTrue(path.exists())
                    self.assertEqual(path.stat().st_mode&0o777,0o660)
                    async def exercise():
                        async with httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(uds=str(path)),timeout=10) as http:
                            async with streamable_http_client('http://127.0.0.1:8765/mcp',http_client=http) as (reader,writer,_):
                                async with ClientSession(reader,writer) as session:
                                    await session.initialize()
                                    self.assertEqual(len((await session.list_tools()).tools),5)
                                    result=await session.call_tool('sql_query',{'sql':'SELECT current_user, count(*) FROM jobwatch.postings','limit':5})
                                    self.assertTrue(result.structuredContent['ok'])
                                    self.assertEqual(result.structuredContent['data']['rows'][0],['jobwatch_agent_readonly',1])
                    asyncio.run(exercise())
                finally:
                    process.terminate()
                    try:process.wait(timeout=10)
                    except subprocess.TimeoutExpired:process.kill();process.wait()

    def test_wrong_identity_expiry_and_fault_roll_back_the_whole_owner_transaction(self):
        from unittest.mock import patch
        from jobwatch.postgres import owner_tools
        wrong=OwnerTools(self.target.config('jobwatch_owner'))
        self.assertFalse(wrong.call('prepare_owner_change',{'recipe':'create_application','values':{'posting_key':self.key}})['ok'])
        tick=[0];core=OwnerTools(self.target.config('jobwatch_agent_owner'),clock=lambda:tick[0])
        p=core.call('prepare_owner_change',{'recipe':'create_application','values':{'posting_key':self.key}})['data']
        tick[0]=601
        self.assertEqual(core.call('commit_owner_change',{'change_id':p['change_id'],'review':p['review']})['error']['code'],'expired_or_unknown_change')
        before=self.scalar('SELECT count(*) FROM jobwatch.owner_audit')
        p=self.change('create_application',posting_key=self.key)
        real=owner_tools.execute_recipe
        def fail_link(conn,name,values):
            if name=='link_application':raise owner_tools.OwnerError('synthetic_rollback')
            return real(conn,name,values)
        with patch.object(owner_tools,'execute_recipe',side_effect=fail_link):
            result=self.owner.call('commit_owner_change',{'change_id':p['change_id'],'review':p['review']})
        self.assertFalse(result['ok'])
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.applications'),0)
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.owner_audit'),before)
        self.assertFalse(self.refreshed)

    def test_failed_refresh_hint_does_not_undo_a_confirmed_owner_commit(self):
        def unavailable():raise OSError('synthetic hint failure')
        self.owner.on_commit=unavailable
        p=self.change('create_application',posting_key=self.key)
        result=self.commit(p)
        self.assertTrue(result['committed']);self.assertFalse(result['calendar_refresh_queued'])
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.applications'),1)
        self.assertTrue(self.commit(p)['replayed'])
