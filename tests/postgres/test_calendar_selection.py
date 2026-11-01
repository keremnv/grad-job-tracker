"""Reviewed calendar selection against the real cookbook, grants and projection."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import replace

from jobwatch.postgres import calendar_feed,reports
from jobwatch.postgres.owner_tools import OwnerTools
from jobwatch.postgres.read_tools import ReadTools
from support import DatabaseTest,actor,posting,run

ROOT=Path(__file__).resolve().parents[2]
TODAY=datetime.now(timezone.utc).date()
DUE=(TODAY+timedelta(days=5)).isoformat()
LATER=(TODAY+timedelta(days=6)).isoformat()
NARROW="SELECT posting_key AS evidence_key FROM jobwatch.postings WHERE false"


def checked():
    return (datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()


def lead(key,*,company='Synthetic',title='Engineer',due=DUE,official=True,url=None,**extra):
    return {'recipe':'record_lead','values':dict(lead_key=key,kind='vacancy',company=company,title=title,state='listed',
        official=official,source_note='PRIVATE_SOURCE_NOTE',url=url or 'https://example.org/'+key.replace(':','-'),
        wording='Observed vacancy page',deadline_date=due,deadline_wording='Apply by '+due,checked_at=checked(),**extra)}


class CalendarSelectionTests(DatabaseTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with cls.target.connect() as c:
            roles=c.execute("SELECT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='jobwatch_agent_owner') AS present").fetchone()['present']
        for filename in (*(() if roles else ('agent_roles.sql',)),'agent_access.sql','lead_deadline_access.sql','posting_deadline_access.sql','calendar_selection_access.sql'):
            p=subprocess.run(['docker','exec','-i',os.environ['JOBWATCH_PG_TEST_CONTAINER'],'psql','-X','-U','postgres',
                '-d','jobwatch_foundation_test','-v','ON_ERROR_STOP=1','-f','-'],
                input=(ROOT/'sql/postgresql'/filename).read_text(),text=True,capture_output=True)
            if p.returncode:raise RuntimeError(p.stderr)

    def setUp(self):
        super().setUp()
        self.key,_,_=self.seed()
        self.refreshed=[]
        self.owner=self.tools()
        self.read=ReadTools(self.target.config('jobwatch_agent_readonly'),shared=True)

    def tools(self,policy=None):
        return OwnerTools(self.target.config('jobwatch_agent_owner'),on_commit=lambda:self.refreshed.append(True),calendar_policy=policy)

    def prepare(self,*items,owner=None):
        r=(owner or self.owner).call('prepare_owner_change',{'recipe':'select_calendar_deadlines','values':{'items':list(items)}})
        self.assertTrue(r['ok'],r)
        return r['data']

    def commit(self,p,owner=None):
        r=(owner or self.owner).call('commit_owner_change',{'change_id':p['change_id'],'review':p['review']})
        self.assertTrue(r['ok'],r)
        return r['data']

    def verify_posting(self,key=None,due=DUE):
        with self.target.connect('jobwatch_owner') as c,c.transaction():
            actor(c)
            c.execute("INSERT INTO jobwatch.verifications(posting_key,kind,checked_at,checked_by,url,wording,value) VALUES (%s,'deadline',now()-interval '1 minute','owner','https://example.org/check','Apply by date',%s)",(key or self.key,due))

    def data(self,policy=None):
        with self.target.connect('jobwatch_calendar_reader') as c:
            return reports.calendar_data(c,shared=True,deadline_selection_sql=policy)

    def summaries(self,policy=None):
        files,manifest=calendar_feed.build(self.data(policy))
        return files['deadlines.ics'].decode().replace('\r\n ','').count('BEGIN:VEVENT'),files,manifest

    def writes(self):
        return self.scalar("SELECT count(*) FROM jobwatch.owner_audit WHERE table_name='calendar_selections'")

    def test_default_policy_item_is_already_included_with_no_write(self):
        self.verify_posting()
        p=self.prepare({'key':self.key,'action':'include'})
        self.assertIsNone(p['change_id'])
        self.assertEqual(p['review']['unchanged'][0]['outcome'],'already_included')
        self.assertEqual(p['review']['unchanged'][0]['deadline'],DUE)
        self.assertEqual(self.writes(),0)

    def test_mixed_batch_records_lead_and_selects_under_narrow_policy(self):
        with tempfile.NamedTemporaryFile('w',suffix='.sql') as f:
            f.write(NARROW);f.flush()
            owner=self.tools(Path(f.name))
            self.verify_posting()
            p=self.prepare({'key':self.key,'action':'include'},
                {'key':'vacancy:new','action':'include','evidence':[lead('vacancy:new')]},owner=owner)
            self.assertEqual({c['key']:c['change'] for c in p['review']['changes']},{self.key:'add','vacancy:new':'add'})
            self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.leads'),0,'prepare must not persist')
            self.assertEqual(self.writes(),0)
            receipt=self.commit(p,owner)
            self.assertEqual(self.refreshed,[True])
            self.assertTrue(all(i['in_projection'] and i['status']=='qualifies' for i in receipt['items']))
            self.assertIn('Database state only',receipt['publication'])
            self.assertEqual(self.summaries(NARROW)[0],2)
            audit=self.scalar("SELECT count(DISTINCT actor) FROM jobwatch.owner_audit WHERE table_name IN ('leads','calendar_selections') AND db_login='jobwatch_agent_owner'")
            self.assertEqual(audit,1)
            self.assertEqual(self.writes(),2)

    def test_insufficient_missing_and_conflicting_are_blocked_not_written(self):
        self.verify_posting();self.verify_posting(due=LATER)
        p=self.prepare({'key':self.key,'action':'include'},
            {'key':'vacancy:unverified','action':'include','evidence':[lead('vacancy:unverified',official=False)]},
            {'key':'vacancy:missing','action':'include'})
        reasons={b['key']:b['reasons'] for b in p['review']['blocked']}
        self.assertEqual(reasons[self.key],['conflicting'])
        self.assertEqual(reasons['vacancy:unverified'][0],'insufficient')
        self.assertIn('unverified_lead',reasons['vacancy:unverified'])
        self.assertEqual(reasons['vacancy:missing'],['not_found'])
        self.assertIsNone(p['change_id'])
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.leads'),0)

    def test_blocked_item_leaves_the_rest_committable_and_excludes_its_evidence(self):
        p=self.prepare({'key':'vacancy:good','action':'include','evidence':[lead('vacancy:good')]},
            {'key':'vacancy:weak','action':'include','evidence':[lead('vacancy:weak',title='Other',official=False)]})
        self.assertEqual([c['key'] for c in p['review']['changes']],['vacancy:good'])
        self.assertEqual([b['key'] for b in p['review']['blocked']],['vacancy:weak'])
        self.assertEqual(p['review']['blocked'][0]['title'],'Other')
        self.commit(p)
        self.assertEqual(self.scalar("SELECT string_agg(lead_key,',') FROM jobwatch.leads"),'vacancy:good')

    def test_failure_during_commit_rolls_back_the_whole_batch(self):
        p=self.prepare({'key':'vacancy:a','action':'include','evidence':[lead('vacancy:a')]},
            {'key':'vacancy:b','action':'include','evidence':[lead('vacancy:b',title='Other')]})
        # A competing writer takes the second key after review, so its exact-identity check fails.
        with self.target.connect('jobwatch_owner') as c,c.transaction():
            actor(c)
            c.execute("INSERT INTO jobwatch.leads(lead_key,kind,company,title,state,url,official,source_note,checked_at,checked_by) VALUES ('vacancy:b','vacancy','Rival','Role','listed','https://example.org/rival',true,'n',now(),'owner')")
        r=self.owner.call('commit_owner_change',{'change_id':p['change_id'],'review':p['review']})
        self.assertEqual(r['error']['code'],'stale_state')
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.leads WHERE lead_key='vacancy:a'"),0)
        self.assertEqual(self.writes(),0)
        self.assertEqual(self.refreshed,[])

    def test_stale_evidence_tampered_review_and_expiry_refuse_write(self):
        self.verify_posting()
        with tempfile.NamedTemporaryFile('w',suffix='.sql') as f:
            f.write(NARROW);f.flush()
            owner=self.tools(Path(f.name))
            p=self.prepare({'key':self.key,'action':'include'},owner=owner)
            tampered=json.loads(json.dumps(p['review']));tampered['changes'][0]['deadline']=LATER
            self.assertEqual(owner.call('commit_owner_change',{'change_id':p['change_id'],'review':tampered})['error']['code'],'review_mismatch')
            self.verify_posting(due=DUE)  # Same date, new evidence: still not what was reviewed.
            self.assertEqual(owner.call('commit_owner_change',{'change_id':p['change_id'],'review':p['review']})['error']['code'],'stale_state')
            self.assertEqual(self.writes(),0)
            q=self.prepare({'key':self.key,'action':'include'},owner=owner)
            owner.pending[q['change_id']]['created']-=601
            self.assertEqual(owner.call('commit_owner_change',{'change_id':q['change_id'],'review':q['review']})['error']['code'],'expired_or_unknown_change')

    def test_replay_and_restart_do_not_duplicate(self):
        evidence=lead('vacancy:replay')
        item={'key':'vacancy:replay','action':'include','evidence':[evidence]}
        with tempfile.NamedTemporaryFile('w',suffix='.sql') as f:
            f.write(NARROW);f.flush()
            owner=self.tools(Path(f.name))
            p=self.prepare(item,owner=owner)
            first=self.commit(p,owner);count=self.writes()
            self.assertTrue(self.commit(p,owner)['replayed'])
            self.assertEqual(self.writes(),count)
            self.assertEqual(self.refreshed,[True])
            # A restart forgets pending reviews; the same request then finds the committed state.
            restarted=self.tools(Path(f.name))
            self.assertEqual(restarted.call('commit_owner_change',{'change_id':p['change_id'],'review':p['review']})['error']['code'],'expired_or_unknown_change')
            again=self.prepare(item,owner=restarted)
            self.assertIsNone(again['change_id'])
            self.assertEqual(again['review']['unchanged'][0]['outcome'],'already_included')
            self.assertEqual(self.writes(),count)
            self.assertTrue(first['committed'])

    def test_riverlane_shape_lead_and_posting_stay_one_event(self):
        # Exact-URL lead and posting share one identity and one event.
        self.verify_posting()
        url=self.scalar('SELECT posting_url FROM jobwatch.research_postings')
        p=self.prepare({'key':'vacancy:same','action':'include','evidence':[lead('vacancy:same',url=url,title='Graduate Software Engineer',company='Test')]})
        self.commit(p)
        self.assertEqual(self.summaries()[0],1)
        # A differently addressed lead for the same role must not silently add a second event.
        q=self.prepare({'key':'vacancy:other-url','action':'include','evidence':[lead('vacancy:other-url',title='Graduate Software Engineer',company='Test')]})
        self.assertTrue(q['review']['blocked'][0]['reasons'][0].startswith('possible_duplicate_of:'))
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.leads WHERE lead_key='vacancy:other-url'"),0)
        # Two inputs for one identity are reported, not doubled.
        r=self.read.call('run_report',{'kind':'calendar_selection','selection':[{'key':self.key},{'key':'vacancy:same'}]})
        self.assertEqual(r['data']['items'][1]['duplicate_of_input'],0)

    def test_duplicate_evidence_conflict_across_lead_and_posting_is_not_hidden(self):
        self.verify_posting(due=DUE)
        url=self.scalar('SELECT posting_url FROM jobwatch.research_postings')
        p=self.prepare({'key':'vacancy:conflict','action':'include','evidence':[lead('vacancy:conflict',url=url,due=LATER,title='Graduate Software Engineer',company='Test')]})
        self.assertEqual(p['review']['blocked'][0]['reasons'],['conflicting'])

    def test_removal_is_explicit_identity_wide_and_uid_sequence_stable(self):
        self.verify_posting()
        url=self.scalar('SELECT posting_url FROM jobwatch.research_postings')
        other=self.prepare({'key':'vacancy:keep','action':'include','evidence':[lead('vacancy:keep',title='Unrelated role')]})
        self.commit(other)
        self.commit(self.prepare({'key':'vacancy:same','action':'include','evidence':[lead('vacancy:same',url=url,title='Graduate Software Engineer',company='Test')]}))
        count,_,first=self.summaries()
        self.assertEqual(count,2)
        uid=next(u for u,e in first['events'].items() if e['active'] and any(a in e['aliases'] for a in [calendar_feed.digest(['deadlines','posting:'+self.key])]))
        before=first['events'][uid]['sequence']
        # Publishing without any approval rows leaves UID and SEQUENCE untouched.
        again,_=None,None
        _,second=calendar_feed.build(self.data(),first)
        self.assertEqual(second['events'][uid]['sequence'],before)
        # Removing through the lead also removes the posting's claim, and only that identity.
        p=self.prepare({'key':'vacancy:same','action':'remove'})
        self.assertEqual(p['review']['changes'][0]['change'],'remove')
        self.commit(p)
        files,third=calendar_feed.build(self.data(),second)
        self.assertEqual(files['deadlines.ics'].count(b'BEGIN:VEVENT'),1)
        self.assertFalse(third['events'][uid]['active'])
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.verifications'),1,'evidence retained')
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.leads WHERE lead_key='vacancy:same'"),1)
        # A repeated removal is a no-op; reselecting restores the UID with a later SEQUENCE.
        self.assertEqual(self.prepare({'key':'vacancy:same','action':'remove'})['review']['unchanged'][0]['outcome'],'already_absent')
        self.commit(self.prepare({'key':self.key,'action':'include'}))
        files,fourth=calendar_feed.build(self.data(),third)
        self.assertEqual(files['deadlines.ics'].count(b'BEGIN:VEVENT'),2)
        self.assertTrue(fourth['events'][uid]['active'])
        self.assertGreater(fourth['events'][uid]['sequence'],before)

    def verified_item(self,key,*,due=DUE,supersedes=(),at=None):
        return {'key':key,'action':'include','evidence':[{'recipe':'verify_posting_deadline','values':dict(posting_key=key,value=due,
            wording='Apply by '+due,url='https://example.org/official',checked_at=at or checked(),reason='Checked official page',supersedes=list(supersedes))}]}

    def test_retried_evidence_is_a_no_op_for_every_recipe(self):
        # A retry after a restart or uncertain commit must not write evidence a second time.
        item=self.verified_item(self.key)
        self.commit(self.prepare(item))
        audit=self.scalar('SELECT count(*) FROM jobwatch.owner_audit')
        again=self.prepare(item,owner=self.tools())
        self.assertIsNone(again['change_id'],again['review'])
        self.assertEqual(again['review']['unchanged'][0]['outcome'],'already_included')
        self.assertEqual(self.scalar("SELECT count(*) FROM jobwatch.verifications WHERE kind='deadline'"),1)
        # Superseding evidence: the old claim is no longer active on retry, which is not a refusal.
        old=self.scalar("SELECT claim_id FROM jobwatch.posting_deadline_claims WHERE posting_key=%s",(self.key,))
        item=self.verified_item(self.key,due=LATER,supersedes=[old])
        self.commit(self.prepare(item))
        again=self.prepare(item,owner=self.tools())
        self.assertIsNone(again['change_id'],again['review'])
        self.assertEqual(again['review']['unchanged'][0]['deadline'],LATER)
        # Different evidence is still new evidence, not a retry.
        other=self.verified_item(self.key,due=LATER,at=(datetime.now(timezone.utc)-timedelta(seconds=5)).isoformat())
        self.assertEqual(self.prepare(other,owner=self.tools())['review']['changes'][0]['change'],'update_evidence')
        # Leads: correct_lead_deadline and link_lead.
        self.commit(self.prepare({'key':'vacancy:lead','action':'include','evidence':[lead('vacancy:lead',title='Unrelated lead role')]}))
        correct={'key':'vacancy:lead','action':'include','evidence':[{'recipe':'correct_lead_deadline','values':dict(lead_key='vacancy:lead',deadline_date=LATER,
            deadline_wording='Apply by '+LATER,deadline_source_url='https://example.org/official',deadline_official=True,deadline_checked_at=checked())}]}
        self.commit(self.prepare(correct))
        audit=self.scalar('SELECT count(*) FROM jobwatch.owner_audit')
        self.assertIsNone(self.prepare(correct,owner=self.tools())['change_id'])
        with self.target.connect('jobwatch_owner') as c,c.transaction():
            actor(c);c.execute("UPDATE jobwatch.leads SET posting_key=%s WHERE lead_key='vacancy:lead'",(self.key,))
        link={'key':'vacancy:lead','action':'include','evidence':[{'recipe':'link_lead','values':dict(lead_key='vacancy:lead',posting_key=self.key)}]}
        self.assertIsNone(self.prepare(link,owner=self.tools())['change_id'])
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.owner_audit')-audit,1,'only the direct link above')

    def test_published_event_with_a_similar_sibling_is_unchanged_not_blocked(self):
        # Adding an event already in the feed cannot create a second one, so nothing needs confirming.
        self.verify_posting()
        self.commit(self.prepare({'key':'vacancy:sibling','action':'include','confirmed_distinct':True,
            'evidence':[lead('vacancy:sibling',title='Graduate Software Engineer',company='Test')]}))
        p=self.prepare({'key':self.key,'action':'include'},{'key':'vacancy:sibling','action':'include'})
        self.assertEqual(p['review']['blocked'],[])
        self.assertEqual({u['outcome'] for u in p['review']['unchanged']},{'already_included'})

    def test_lead_joining_a_published_posting_is_evidence_not_an_addition(self):
        self.verify_posting()
        url=self.scalar('SELECT posting_url FROM jobwatch.research_postings')
        p=self.prepare({'key':'vacancy:same','action':'include','evidence':[lead('vacancy:same',url=url,title='Graduate Software Engineer',company='Test')]})
        self.assertEqual(p['review']['changes'][0]['change'],'update_evidence')
        self.assertEqual(p['review']['feed_effect']['added'],0)

    def test_expired_events_are_outside_the_projection_counts_and_duplicate_check(self):
        # The feed's rolling window drops old events; the report must describe that same feed.
        self.verify_posting(due=(TODAY-timedelta(days=90)).isoformat())
        r=self.read.call('run_report',{'kind':'calendar_selection','selection':[{'key':self.key}]})['data']
        self.assertEqual(r['items'][0]['status'],'outside_window')
        self.assertEqual(r['projected_deadline_events'],self.summaries()[0])
        self.assertEqual(r['projected_deadline_events'],0)
        # Last cycle's expired role with the same title is not a second event for this one.
        p=self.prepare({'key':'vacancy:new-cycle','action':'include','evidence':[lead('vacancy:new-cycle',title='Graduate Software Engineer',company='Test')]})
        self.assertEqual(p['review']['blocked'],[])

    def test_selection_is_additive_and_leaves_every_unlisted_event_untouched(self):
        # Adding 12 means the feed gains up to 12. It never means the feed is limited to those 12.
        for i in range(3):
            self.commit(self.prepare({'key':'vacancy:old-%d'%i,'action':'include','evidence':[lead('vacancy:old-%d'%i,company='Existing %d'%i,title='Existing role %d'%i)]}))
        _,_,first=self.summaries()
        items=[{'key':'vacancy:n-%d'%i,'action':'include','evidence':[lead('vacancy:n-%d'%i,company='New %d'%i,title='New role %d'%i)]} for i in range(12)]
        p=self.prepare(*items)
        self.assertEqual(p['review']['feed_effect'],{'deadline_events_before':3,'deadline_events_after':15,'added':12,'removed':0,'unlisted_events_changed':0})
        self.commit(p)
        files,second=calendar_feed.build(self.data(),first)
        self.assertEqual(files['deadlines.ics'].count(b'BEGIN:VEVENT'),15)
        for uid,row in first['events'].items():
            self.assertEqual({k:second['events'][uid][k] for k in ('active','sequence','modified_at','fingerprint')},{k:row[k] for k in ('active','sequence','modified_at','fingerprint')})
        self.assertEqual(self.writes(),0,'the default policy needs no selection rows to add')

    def test_full_batch_of_existing_evidence_fits_the_response_bound(self):
        # Existing items report before and after states, each with their evidence.
        keys=[]
        for i in range(25):
            key=self.seed(native_id='full-%d'%i,title='Distinct role number %d'%i)[0];keys.append(key)
            with self.target.connect('jobwatch_owner') as c,c.transaction():
                actor(c)
                for n in range(2):
                    c.execute("INSERT INTO jobwatch.verifications(posting_key,kind,checked_at,checked_by,url,wording,value) VALUES (%s,'deadline',now()-interval '1 minute','owner',%s,%s,%s)",
                        (key,'https://example.org/official/'+'p'*120+str(n),'Observed deadline wording '*12,DUE))
        with tempfile.NamedTemporaryFile('w',suffix='.sql') as f:
            f.write(NARROW);f.flush()
            owner=self.tools(Path(f.name))
            p=self.prepare(*[{'key':k,'action':'include'} for k in keys],owner=owner)
            self.assertEqual(len(p['review']['changes']),25)
            self.assertLess(len(json.dumps(p['review']).encode()),32768)
            self.assertEqual(len(self.commit(p,owner)['items']),25)

    def test_full_batch_at_production_scale_within_service_limits(self):
        # About the production row counts, under the Owner service's real timeouts.
        with self.target.connect('jobwatch_collector') as c,c.transaction():
            actor(c);attempt=run(c,self.source)
            keys=[posting(c,self.source,attempt['id'],native_id='scale-%d'%i,title='Scale role %d'%i)[0] for i in range(660)]
        with self.target.connect('jobwatch_owner') as c,c.transaction():
            actor(c)
            for key in keys[:115]:
                c.execute("INSERT INTO jobwatch.verifications(posting_key,kind,checked_at,checked_by,url,wording,value) VALUES (%s,'deadline',now()-interval '1 minute','owner','https://example.org/check','Apply by date',%s)",(key,DUE))
        config=replace(self.target.config('jobwatch_agent_owner'),statement_timeout_ms=5000,lock_timeout_ms=1000,idle_transaction_timeout_ms=10000)
        with tempfile.NamedTemporaryFile('w',suffix='.sql') as f:
            f.write(NARROW);f.flush()
            owner=OwnerTools(config,calendar_policy=Path(f.name))
            items=[{'key':k,'action':'include'} for k in keys[:15]]
            items+=[{'key':'vacancy:s-%d'%i,'action':'include','evidence':[lead('vacancy:s-%d'%i,company='Scale %d'%i,title='Lead role %d'%i)]} for i in range(9)]
            items.append({'key':'vacancy:s-weak','action':'include','evidence':[lead('vacancy:s-weak',company='Weak',title='Weak role',official=False)]})
            started=time.monotonic();p=self.prepare(*items,owner=owner);prepared=time.monotonic()
            self.assertEqual((len(p['review']['changes']),len(p['review']['blocked'])),(24,1))
            self.commit(p,owner);done=time.monotonic()
            if os.environ.get('JOBWATCH_TIMING_OUTPUT'):
                import resource
                Path(os.environ['JOBWATCH_TIMING_OUTPUT']).write_text(json.dumps({'prepare_seconds':round(prepared-started,3),'commit_seconds':round(done-prepared,3),
                    'peak_rss_mib':round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,1)})+'\n')

    def test_policy_unavailable_and_bounds_fail_closed(self):
        owner=self.tools(Path('/nonexistent/policy.sql'))
        r=owner.call('prepare_owner_change',{'recipe':'select_calendar_deadlines','values':{'items':[{'key':self.key,'action':'include'}]}})
        self.assertEqual(r['error']['code'],'calendar_policy_unavailable')
        too_many=[{'key':'k%d'%i,'action':'include'} for i in range(26)]
        for values in ({'items':too_many},{'items':[]},{'items':[{'key':self.key,'action':'include'}]*2},
                       {'items':[{'key':self.key,'action':'delete'}]},{'items':[{'key':self.key,'action':'remove','evidence':[lead(self.key)]}]},
                       {'items':[{'key':self.key,'action':'include','evidence':[{'recipe':'update_stage','values':{}}]}]},{'sql':'x'}):
            self.assertEqual(self.owner.call('prepare_owner_change',{'recipe':'select_calendar_deadlines','values':values})['error']['code'],'invalid_arguments',values)
        self.assertEqual(self.owner.call('prepare_owner_change',{'recipe':'set_calendar_selection','values':{}})['error']['code'],'invalid_arguments')

    def test_full_25_item_batch_fits_every_transport_bound(self):
        items=[{'key':'vacancy:%d'%i,'action':'include','evidence':[lead('vacancy:%d'%i,title='Distinct role number %d'%i,company='Employer %d'%i)]} for i in range(25)]
        p=self.prepare(*items)
        self.assertEqual(len(p['review']['changes']),25)
        self.assertLess(len(json.dumps(p['review']).encode()),32768)
        self.commit(p)
        self.assertEqual(self.summaries()[0],25)

    def test_concurrent_batches_have_one_winner(self):
        with tempfile.NamedTemporaryFile('w',suffix='.sql') as f:
            f.write(NARROW);f.flush()
            self.verify_posting()
            a=self.tools(Path(f.name));b=self.tools(Path(f.name))
            pa=self.prepare({'key':self.key,'action':'include'},owner=a);pb=self.prepare({'key':self.key,'action':'include'},owner=b)
            with ThreadPoolExecutor(2) as pool:
                results=list(pool.map(lambda x:x[0].call('commit_owner_change',{'change_id':x[1]['change_id'],'review':x[1]['review']}),((a,pa),(b,pb))))
            self.assertEqual(sum(r['ok'] for r in results),1,results)
            self.assertEqual(self.writes(),1)

    def test_role_permissions_are_narrow(self):
        with self.target.connect('jobwatch_owner') as c,c.transaction():
            actor(c);c.execute("INSERT INTO jobwatch.calendar_selections(posting_key,mode,recorded_by) VALUES (%s,'exclude','owner')",(self.key,))
        import psycopg
        def denied(role,sql):
            with self.target.connect(role) as c:
                # Read-only roles may also refuse earlier, through their read-only default.
                with self.assertRaises((psycopg.errors.InsufficientPrivilege,psycopg.errors.ReadOnlySqlTransaction)):
                    with c.transaction():actor(c);c.execute(sql)
        for role in ('jobwatch_agent_readonly','jobwatch_calendar_reader'):
            denied(role,"UPDATE jobwatch.calendar_selections SET mode='include'")
            denied(role,"INSERT INTO jobwatch.calendar_selections(lead_key,mode,recorded_by) VALUES ('x','include','x')")
        denied('jobwatch_agent_owner','DELETE FROM jobwatch.calendar_selections')
        denied('jobwatch_agent_owner','TRUNCATE jobwatch.calendar_selections')
        denied('jobwatch_agent_owner',"UPDATE jobwatch.calendar_selections SET posting_key=NULL")
        r=self.read.call('sql_query',{'sql':"INSERT INTO jobwatch.calendar_selections(lead_key,mode,recorded_by) VALUES ('x','include','x') RETURNING 1",'limit':1})
        self.assertFalse(r['ok'])
        # The Research identity cannot mutate through the report, and unknown input is refused.
        self.assertFalse(self.read.call('run_report',{'kind':'calendar_selection','selection':[{'sql':'x'}]})['ok'])
        self.assertFalse(self.read.call('run_report',{'kind':'daily','selection':[{'key':self.key}]})['ok'])

    def test_grant_delta_is_the_exact_matrix_and_repeatable(self):
        def matrix():
            with self.target.connect() as c:
                return {(r['role'],r['col'],r['p']) for r in c.execute("""SELECT r.role,c.column_name AS col,p.p
                    FROM (VALUES ('jobwatch_agent_owner'),('jobwatch_agent_readonly'),('jobwatch_calendar_reader'),('jobwatch_collector')) r(role)
                    CROSS JOIN information_schema.columns c CROSS JOIN (VALUES ('SELECT'),('INSERT'),('UPDATE')) p(p)
                    WHERE c.table_schema='jobwatch' AND c.table_name='calendar_selections'
                    AND has_column_privilege(r.role,'jobwatch.calendar_selections',c.column_name,p.p)""")}
        cols=('posting_key','lead_key','evidence_key','mode','approved_deadline','decided_at','recorded_by')
        expected={(r,c,'SELECT') for r in ('jobwatch_agent_owner','jobwatch_agent_readonly','jobwatch_calendar_reader') for c in cols}
        expected|={('jobwatch_agent_owner',c,'INSERT') for c in ('posting_key','lead_key','mode','approved_deadline','recorded_by')}
        expected|={('jobwatch_agent_owner',c,'UPDATE') for c in ('mode','approved_deadline','decided_at','recorded_by')}
        self.assertEqual(matrix(),expected)
        # As the migrator, which owns the table: the production rollout's identity for this delta.
        p=subprocess.run(['docker','exec','-i',os.environ['JOBWATCH_PG_TEST_CONTAINER'],'psql','-X','-U','jobwatch_migrator','-d','jobwatch_foundation_test','-v','ON_ERROR_STOP=1','-f','-'],
            input=(ROOT/'sql/postgresql/calendar_selection_access.sql').read_text(),text=True,capture_output=True)
        self.assertEqual(p.returncode,0,p.stderr)
        self.assertEqual(matrix(),expected)

    def test_research_report_status_publisher_and_privacy(self):
        self.verify_posting()
        self.commit(self.prepare({'key':'vacancy:private','action':'include','evidence':[lead('vacancy:private',title='Private role',company='Other')]}))
        self.commit(self.prepare({'key':'vacancy:excluded','action':'include','evidence':[lead('vacancy:excluded',title='Excluded role',company='Third')]}))
        self.commit(self.prepare({'key':'vacancy:excluded','action':'remove'}))
        with tempfile.TemporaryDirectory() as d:
            status=Path(d)/'status.json'
            read=ReadTools(self.target.config('jobwatch_agent_readonly'),shared=True,calendar_status=status)
            selection=[{'key':self.key},{'company':'other','title':'private role','deadline':LATER},{'key':'vacancy:excluded'},{'company':'Nobody','title':'Nothing'}]
            r=read.call('run_report',{'kind':'calendar_selection','selection':selection})
            self.assertTrue(r['ok'],r)
            items=r['data']['items']
            self.assertEqual(r['data']['publisher']['state'],'unavailable')
            self.assertEqual(items[0]['in_published_feed'],'unknown')
            self.assertEqual(items[1]['candidate_deadline_mismatch'],LATER)
            self.assertEqual(items[1]['deadline'],DUE)
            self.assertTrue(items[1]['all_day'])
            self.assertEqual(items[2]['status'],'excluded')
            self.assertEqual(items[3]['resolution'],'not_found')
            data=self.data()
            _,manifest=calendar_feed.build(data)
            value=calendar_feed.publication_status(None,now=data['generated_at'],manifest=manifest)
            calendar_feed.write_status(status,value)
            self.assertEqual(status.stat().st_mode&0o777,0o640)
            r=read.call('run_report',{'kind':'calendar_selection','selection':selection})
            items=r['data']['items']
            self.assertTrue(items[0]['in_published_feed'])
            self.assertEqual(items[0]['sequence'],0)
            self.assertFalse(items[2]['in_published_feed'])
            self.assertEqual(r['data']['publisher']['last_attempt_ok'],True)
            self.assertIn('does not show whether Proton',r['data']['publisher']['note'])
            # A failure retains the last success rather than overwriting it.
            failed=calendar_feed.publication_status(value,now=data['generated_at'])
            self.assertFalse(failed['last_attempt_ok'])
            self.assertEqual(failed['last_success_at'],value['last_success_at'])
            blob=json.dumps(r)+status.read_text()
            for secret in ('PRIVATE_SOURCE_NOTE','calendar.refresh','capability','token='):
                self.assertNotIn(secret,blob)
            # The status file holds no event text.
            for word in ('Private role','example.org','vacancy:'):
                self.assertNotIn(word,status.read_text())
        listing=self.read.call('run_report',{'kind':'calendar_selection'})
        self.assertEqual(listing['data']['recorded_approvals'],1)
        self.assertEqual(listing['data']['items'][0]['key'],'vacancy:excluded')

    def test_legacy_export_stays_distinct_from_the_published_projection(self):
        self.verify_posting()
        self.commit(self.prepare({'key':self.key,'action':'remove'}))
        legacy=self.read.call('run_report',{'kind':'calendar'})['data']['text']
        self.assertIn('BEGIN:VEVENT',legacy)
        self.assertEqual(self.summaries()[0],0)

    def test_psql_recipe_guards_expected_mode(self):
        book=(ROOT/'sql/postgresql/owner_workflows.sql').read_text()
        sql=book.split('-- BEGIN set_calendar_selection\n',1)[1].split('-- END set_calendar_selection',1)[0]
        def run(mode,expected):
            args=['docker','exec','-i',os.environ['JOBWATCH_PG_TEST_CONTAINER'],'psql','-X','-w','-q','-U','jobwatch_owner','-d','jobwatch_foundation_test',
                '-v','actor=agent:synthetic','-v','posting_key='+self.key,'-v','lead_key=','-v','mode='+mode,'-v','expected_mode='+expected,'-v','approved_deadline=']
            return subprocess.run(args+['-f','-'],input=sql,text=True,capture_output=True)
        self.assertEqual(run('exclude','absent').returncode,0)
        self.assertEqual(run('exclude','absent').returncode,0)  # uncertain-commit retry
        self.assertNotEqual(run('include','absent').returncode,0)
        self.assertEqual(run('include','exclude').returncode,0)
        self.assertEqual(self.scalar('SELECT mode FROM jobwatch.calendar_selections'),'include')

    def rollout_probe_stays_non_writing(self):
        """The production rollout's own probe, through the official SDK on both servers."""
        from mcp import ClientSession,StdioServerParameters
        from mcp.client.stdio import stdio_client
        sys.path.insert(0,str(ROOT/'infra/aws/scripts'))
        import calendar_selection_check as check
        base={'PATH':os.defpath,'PYTHONPATH':str(ROOT/'src'),'PYTHONDONTWRITEBYTECODE':'1'}
        servers={'read':StdioServerParameters(command=sys.executable,args=['-m','jobwatch.postgres.read_mcp','--shared'],env={**base,'JOBWATCH_CHAT_DATABASE_URL':self.target.config('jobwatch_agent_readonly').dsn}),
            'owner':StdioServerParameters(command=sys.executable,args=['-m','jobwatch.postgres.owner_mcp'],env={**base,'JOBWATCH_AGENT_OWNER_DATABASE_URL':self.target.config('jobwatch_agent_owner').dsn})}
        audit=self.scalar('SELECT count(*) FROM jobwatch.owner_audit')
        async def run(kind):
            with open(os.devnull,'w') as errors:
                async with stdio_client(servers[kind],errlog=errors) as (r,w),ClientSession(r,w) as session:
                    await session.initialize()
                    return await check.check(session,kind)
        self.assertTrue(asyncio.run(run('read'))['calendar_report'])
        self.assertFalse(asyncio.run(run('owner'))['commit_called'])
        self.assertEqual(self.scalar('SELECT count(*) FROM jobwatch.owner_audit'),audit)

    def test_synthetic_mcp_demonstration_selects_deadlines_in_one_review(self):
        """Official MCP SDK, both servers, one review. Not the ChatGPT approval UI."""
        from mcp import ClientSession,StdioServerParameters
        from mcp.client.stdio import stdio_client
        self.verify_posting()  # Already published by the default policy.
        roles=[('Employer A','Software Engineer Graduate'),('Employer B','Data Analyst 2027'),('Employer C','Cyber Graduate')]
        items=[{'key':self.key,'action':'include'}]
        items+=[{'key':'vacancy:demo-%d'%i,'action':'include','evidence':[lead('vacancy:demo-%d'%i,company=c,title=t)]} for i,(c,t) in enumerate(roles)]
        items.append({'key':'vacancy:demo-weak','action':'include','evidence':[lead('vacancy:demo-weak',company='Employer D',title='Unverified Role',official=False)]})
        out=[]
        async def exercise():
            base={'PATH':os.defpath,'PYTHONPATH':str(ROOT/'src'),'PYTHONDONTWRITEBYTECODE':'1'}
            rp=StdioServerParameters(command=sys.executable,args=['-m','jobwatch.postgres.read_mcp','--shared'],env={**base,'JOBWATCH_CHAT_DATABASE_URL':self.target.config('jobwatch_agent_readonly').dsn})
            wp=StdioServerParameters(command=sys.executable,args=['-m','jobwatch.postgres.owner_mcp'],env={**base,'JOBWATCH_AGENT_OWNER_DATABASE_URL':self.target.config('jobwatch_agent_owner').dsn})
            with open(os.devnull,'w') as errors:
                async with stdio_client(rp,errlog=errors) as (rr,rt),ClientSession(rr,rt) as read:
                    await read.initialize()
                    async with stdio_client(wp,errlog=errors) as (wr,wt),ClientSession(wr,wt) as write:
                        await write.initialize()
                        prepared=(await write.call_tool('prepare_owner_change',{'recipe':'select_calendar_deadlines','values':{'items':items}})).structuredContent
                        self.assertTrue(prepared['ok'],prepared)
                        review=prepared['data']['review']
                        for c in review['changes']:out.append(('CHANGE',c['company'],c['title'],c['deadline'],c['change'],c['evidence_url']))
                        for c in review['unchanged']:out.append(('SAME',c['company'],c['title'],c['deadline'],c['outcome'],''))
                        for c in review['blocked']:out.append(('BLOCKED',c['company'],c['title'],'',','.join(c['reasons']),''))
                        done=(await write.call_tool('commit_owner_change',{'change_id':prepared['data']['change_id'],'review':review})).structuredContent
                        self.assertTrue(done['ok'],done)
                        out.append(('COMMITTED',len(done['data']['items']),done['data']['db_login'],'','',''))
                    selection=[{'key':i['key']} for i in items]
                    after=(await read.call_tool('run_report',{'kind':'calendar_selection','selection':selection})).structuredContent
                    self.assertTrue(after['ok'],after)
                    for i in after['data']['items']:out.append(('READBACK',i['input']['key'],i.get('status',i['resolution']),i.get('deadline'),i.get('in_projection'),''))
        asyncio.run(exercise())
        self.rollout_probe_stays_non_writing()
        if os.environ.get('JOBWATCH_DEMO_OUTPUT'):
            Path(os.environ['JOBWATCH_DEMO_OUTPUT']).write_text('\n'.join(' | '.join(str(x) for x in row) for row in out)+'\n')
        self.assertEqual(self.summaries()[0],4)
        self.assertEqual(sum(r[0]=='BLOCKED' for r in out),1)
