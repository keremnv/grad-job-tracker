"""Least-privilege research and actual cookbook owner writes; no production target."""
import asyncio
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
        for filename in ('agent_roles.sql','agent_access.sql'):
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
