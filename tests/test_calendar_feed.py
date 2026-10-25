"""Offline publication tests: synthetic fields, real private files, no HTTP server."""
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from jobwatch import calendar
from jobwatch.postgres import calendar_feed as feed
from jobwatch.postgres.reports import calendar_events
from test_calendar import properties

NOW = '2026-10-25T12:00:00Z'
LATER = '2026-10-25T13:00:00Z'
URL = 'https://example.org/jobs/one'
KEY = 'greenhouse:test:1'


def claim(**changes):
    return {'identity':URL,'kind':'vacancy','lead_key':'vacancy:one','company':'Example',
        'title':'Graduate Engineer','posting_url':URL,'url':URL,'value':'2026-10-29',
        'wording':'PRIVATE_SOURCE_WORDING','checked_at':NOW,'provenance':'checked_official_lead',
        'structured':True,'ambiguous':False,'status':'listed',
        'calendar_aliases':['lead:vacancy:one'],**changes}


def data(**changes):
    return {'generated_at':NOW,'deadlines':[claim()], 'applications':[
        {'job_key':'manual:one','company':'Example','role':'Engineer','url':URL,
         'stage':'applied','next_action':'PRIVATE_ACTION_TEXT','next_action_date':'2026-10-30',
         'notes':'PRIVATE_NOTE','cv_version':'PRIVATE_CV'}], 'revisits':[], **changes}


class SnapshotTests(unittest.TestCase):
    def test_unchanged_generation_has_identical_events_and_bytes(self):
        first, meta=feed.build(data())
        second, after=feed.build(data(generated_at=LATER),meta)
        self.assertEqual(first,second)
        self.assertNotEqual(meta['generated_at'],after['generated_at'])
        self.assertEqual(meta['events'],after['events'])
        self.assertEqual(meta['files'],after['files'])
        for event in properties(first['combined.ics']):
            self.assertEqual(event['DTSTAMP'],event['LAST-MODIFIED'])
            self.assertEqual(event['SEQUENCE'],'0')

    def test_check_refresh_and_private_text_edits_do_not_churn_feed(self):
        first,meta=feed.build(data())
        updated=data(generated_at=LATER)
        updated['deadlines'][0].update(checked_at=LATER,wording='New private quote')
        updated['applications'][0].update(next_action='A different private pending action',notes='New private note')
        self.assertEqual(first,feed.build(updated,meta)[0])

    def test_reschedule_clear_reactivate_and_closed_application(self):
        original,meta=feed.build(data())
        uid=properties(original['actions.ics'])[0]['UID']
        updated=data(generated_at=LATER);updated['applications'][0]['next_action_date']='2026-11-01'
        bodies,meta=feed.build(updated,meta)
        event=properties(bodies['actions.ics'])[0]
        self.assertEqual(event['UID'],uid);self.assertEqual(event['SEQUENCE'],'1')
        self.assertEqual(event['DTSTART;VALUE=DATE'],'20261101')
        updated['applications'][0]['next_action']=''
        bodies,meta=feed.build(updated,meta)
        self.assertEqual(properties(bodies['actions.ics']),[])
        self.assertFalse(meta['events'][uid]['active'])
        bodies,meta=feed.build(data(generated_at=LATER),meta)
        self.assertEqual(properties(bodies['actions.ics'])[0]['UID'],uid)
        self.assertEqual(meta['events'][uid]['sequence'],3)
        for stage in ('rejected','withdrawn'):
            closed=data();closed['applications'][0]['stage']=stage
            self.assertEqual(properties(feed.build(closed)[0]['actions.ics']),[])

    def test_legacy_unlinked_uid_later_link_merge_and_unlink(self):
        original,meta=feed.build(data(applications=[]))
        original_uid=properties(original['deadlines.ics'])[0]['UID']
        self.assertEqual(original_uid,calendar.uid('deadline',URL))
        legacy,warnings=calendar.deadline_events({'postings':{}},{},{'vacancies':[
            {'company':'Example','title':'Graduate Engineer','url':URL,'official':True,'status':'listed',
             'checked_at':NOW,'deadline':{'value':'2026-10-29','wording':'Apply by 29 October 2026'}}]})
        self.assertFalse(warnings)
        self.assertEqual(original_uid,legacy[0]['uid'])
        linked=claim(identity=KEY,calendar_aliases=['lead:vacancy:one','posting:'+KEY])
        bodies,meta=feed.build(data(generated_at=LATER,deadlines=[linked],applications=[]),meta)
        self.assertEqual(properties(bodies['deadlines.ics'])[0]['UID'],original_uid)
        separate=claim(identity=KEY,calendar_aliases=['posting:'+KEY])
        bodies,_=feed.build(data(generated_at=LATER,deadlines=[separate],applications=[]),meta)
        self.assertEqual(properties(bodies['deadlines.ics'])[0]['UID'],original_uid)
        # A second, previously separate posting deadline now joins the lead identity.
        _,both=feed.build(data(deadlines=[claim(),separate],applications=[]))
        bodies,merged=feed.build(data(generated_at=LATER,deadlines=[linked],applications=[]),both)
        self.assertEqual(len(properties(bodies['deadlines.ics'])),1)
        self.assertEqual(sum(r['active'] for r in merged['events'].values()),1)
        bodies,split=feed.build(data(generated_at=LATER,deadlines=[claim(),separate],applications=[]),merged)
        self.assertEqual(len(properties(bodies['deadlines.ics'])),2)
        self.assertEqual(len({e['UID'] for e in properties(bodies['deadlines.ics'])}),2)
        self.assertEqual(sum(r['active'] for r in split['events'].values()),2)

    def test_linked_application_keeps_manual_followup_identity(self):
        first,meta=feed.build(data())
        linked=data(generated_at=LATER);linked['applications'][0]['posting_key']=KEY
        self.assertEqual(first,feed.build(linked,meta)[0])

    def test_minimal_privacy_in_all_artifacts_and_injection_escaping(self):
        source=data();source['applications'][0]['role']='Engineer\r\nATTENDEE:malicious;é'*15
        bodies,meta=feed.build(source)
        raw=b'\n'.join(bodies.values())+feed.encoded(meta)
        for word in (b'PRIVATE_SOURCE_WORDING',b'PRIVATE_ACTION_TEXT',b'PRIVATE_NOTE',b'PRIVATE_CV'):
            self.assertNotIn(word,raw)
        self.assertNotIn(b'\r\nATTENDEE:',bodies['actions.ics'])
        self.assertIn(b'\\nATTENDEE',bodies['actions.ics'])
        for body in bodies.values():
            self.assertTrue(all(len(line)<=75 for line in body.split(b'\r\n')))
            self.assertEqual(body.decode().count('BEGIN:VEVENT'),len(properties(body)))
        self.assertIn(b'checked_official_lead',bodies['deadlines.ics'])

    def test_programme_cycle_date_never_inferred_or_rolled_forward(self):
        programme=claim(identity='programme:example:2027',kind='programme',cycle_year=2027,
            status='open',calendar_aliases=['lead:programme:example:2027'],value='2026-11-01')
        bodies,_=feed.build(data(deadlines=[programme],applications=[]))
        event=properties(bodies['deadlines.ics'])[0]
        self.assertIn('cycle 2027',event['SUMMARY'])
        self.assertEqual(event['DTSTART;VALUE=DATE'],'20261101')
        for values in ({'status':'previous_cycle'},{'value':None},{'status':'unknown'},{'provenance':'unverified_lead'}):
            bodies,_=feed.build(data(deadlines=[{**programme,**values}],applications=[]))
            self.assertEqual(properties(bodies['deadlines.ics']),[])

    def test_uncertain_conflicting_rolling_unsafe_and_future_claims(self):
        for changes in ({'value':'2026-10-29T12:00'}, {'value':'2026-10-29T12:00-00:00'},
                        {'wording':'Rolling deadline'}, {'ambiguous':True},
                        {'url':'https://example.org/?token=secret'}, {'checked_at':'2027-01-01T00:00:00Z'}):
            bodies,meta=feed.build(data(deadlines=[claim(**changes)],applications=[]))
            self.assertEqual(properties(bodies['deadlines.ics']),[])
            self.assertTrue(meta['warnings'])
        bodies,meta=feed.build(data(deadlines=[claim(),claim(value='2026-11-01')]))
        self.assertEqual(properties(bodies['deadlines.ics']),[]);self.assertTrue(meta['warnings'])

    def test_revisit_and_feed_selection_use_same_projection(self):
        source=data(revisits=[{'posting_key':KEY,'company':'Example','title':'Engineer',
                             'posting_url':URL,'revisit_on':'2026-10-31'}])
        bodies,meta=feed.build(source,feeds=['actions'])
        self.assertEqual(properties(bodies['deadlines.ics']),[])
        self.assertEqual(properties(bodies['combined.ics']),properties(bodies['actions.ics']))
        self.assertEqual(len(properties(bodies['actions.ics'])),2)
        self.assertTrue(any(e['UID']==calendar.uid('revisit',KEY) for e in properties(bodies['actions.ics'])))
        minimal,_=calendar_events(source,minimal=True)
        self.assertEqual(len(minimal['actions']),2)

    def test_bounds_clock_and_alias_collisions_fail_without_truncation(self):
        for kwargs in ({'max_bytes':128},{'future_days':0},{'past_days':True},{'feeds':['actions','actions']}):
            with self.assertRaises(feed.FeedError):feed.build(data(),**kwargs)
        _,meta=feed.build(data())
        with self.assertRaises(feed.FeedError):feed.build(data(generated_at='2026-10-24T00:00:00Z'),meta)
        with self.assertRaises(feed.FeedError):
            feed.build(data(deadlines=[claim(),claim(identity='other')]))
        bodies,meta=feed.build(data(deadlines=[claim(value='2030-01-01')],applications=[]))
        self.assertEqual(properties(bodies['combined.ics']),[])
        with patch.object(feed,'MAX_STATE',0),self.assertRaises(feed.FeedError):feed.build(data())

    def test_london_window_for_timestamp(self):
        source=data(generated_at='2026-07-01T12:00:00Z',applications=[],deadlines=[claim(
            value='2026-06-30T23:30:00Z',checked_at='2026-06-01T00:00:00Z')])
        bodies,_=feed.build(source,past_days=0)
        self.assertEqual(len(properties(bodies['deadlines.ics'])),1)

    def test_atomic_snapshot_unchanged_mtime_checksums_and_freshness(self):
        with TemporaryDirectory() as temp:
            root=Path(temp)/'feed'
            result=feed.publish(data(),root,initialize=True)
            old,meta=feed.current_snapshot(root);before=(old/'combined.ics').stat().st_mtime
            for name,row in meta['files'].items():self.assertEqual(sha256((old/name).read_bytes()).hexdigest(),row['sha256'])
            feed.publish(data(generated_at=LATER),root)
            new,after=feed.current_snapshot(root)
            self.assertNotEqual(old,new)
            self.assertEqual(before,(new/'combined.ics').stat().st_mtime)
            self.assertEqual((new/'combined.ics').read_bytes(),(old/'combined.ics').read_bytes())
            self.assertTrue(feed.summary(after,now=LATER)['fresh'])
            self.assertFalse(feed.summary(after,now='2026-10-26T13:00:00Z')['fresh'])
            self.assertFalse(result['external_subscription_verified'])
            for path in [root,new,*new.iterdir()]:self.assertEqual(path.stat().st_mode&0o077,0)
            self.assertNotIn('PRIVATE_',json.dumps(after))

    def test_failed_publication_preserves_prior_generation(self):
        with TemporaryDirectory() as temp:
            root=Path(temp)/'feed';feed.publish(data(),root,initialize=True)
            old,meta=feed.current_snapshot(root)
            with self.assertRaises(feed.FeedError):feed.publish(data(generated_at=LATER),root,max_bytes=128)
            with patch.object(feed.os,'replace',side_effect=OSError('synthetic')),self.assertRaises(OSError):
                feed.publish(data(generated_at=LATER),root)
            self.assertEqual(feed.current_snapshot(root),(old,meta))
            self.assertEqual(len(list(root.glob('snapshot-*'))),1)
            feed.publish(data(generated_at=LATER),root)

    def test_corruption_and_unsafe_paths_fail_closed(self):
        with TemporaryDirectory() as temp:
            root=Path(temp)/'feed';feed.publish(data(),root,initialize=True)
            current,_=feed.current_snapshot(root)
            (current/'actions.ics').write_bytes(b'corrupt')
            with self.assertRaises(feed.FeedError):feed.publish(data(),root)
            with self.assertRaises(feed.FeedError):feed.publish(data(),root,initialize=True)
            (root/'current').unlink()
            with self.assertRaises(feed.FeedError):feed.publish(data(),root)
            link=Path(temp)/'link';link.symlink_to(root)
            with self.assertRaises(feed.FeedError):feed.publish(data(),link)

    def test_locking_initial_retry_retention_and_recovery_copy(self):
        import shutil
        with TemporaryDirectory() as temp:
            root=Path(temp)/'feed'
            with self.assertRaises(feed.FeedError):feed.publish(data(),root,initialize=True,max_bytes=128)
            feed.publish(data(),root,initialize=True)
            with feed.publication_lock(root),self.assertRaises(feed.FeedError):feed.publish(data(),root)
            feed.publish(data(generated_at=LATER),root)
            feed.publish(data(generated_at='2026-10-25T14:00:00Z'),root)
            self.assertEqual(len(list(root.glob('snapshot-*'))),2)
            restored=Path(temp)/'restored';shutil.copytree(root,restored,symlinks=True)
            before=(root/'current'/'combined.ics').read_bytes()
            feed.publish(data(generated_at='2026-10-25T15:00:00Z'),restored)
            self.assertEqual(before,(restored/'current'/'combined.ics').read_bytes())

    def test_hardlinks_permissions_and_corrupt_metadata(self):
        with TemporaryDirectory() as temp:
            root=Path(temp)/'feed';feed.publish(data(),root,initialize=True)
            current,meta=feed.current_snapshot(root)
            os.link(current/'actions.ics',Path(temp)/'hardlink')
            with self.assertRaises(feed.FeedError):feed.current_snapshot(root)
            (Path(temp)/'hardlink').unlink();os.chmod(current/'actions.ics',0o644)
            with self.assertRaises(feed.FeedError):feed.current_snapshot(root)
            for bad in (None,[],{}, {**meta,'events':{'bad':{}}}, {**meta,'files':None}):
                with self.assertRaises(feed.FeedError):feed.check_manifest(bad)

    def test_private_modes_under_both_umasks_and_symlink_file_refusal(self):
        for mask in (0o022,0o077):
            with TemporaryDirectory() as temp:
                root=Path(temp)/'feed';previous=os.umask(mask)
                try:feed.publish(data(),root,initialize=True)
                finally:os.umask(previous)
                current,_=feed.current_snapshot(root)
                self.assertEqual(root.stat().st_mode&0o777,0o700)
                self.assertTrue(all(p.stat().st_mode&0o777==0o600 for p in current.iterdir()))
                external=Path(temp)/'external';external.write_bytes(b'PRIVATE_EXTERNAL')
                (current/'actions.ics').unlink();(current/'actions.ics').symlink_to(external)
                with self.assertRaises(OSError):feed.current_snapshot(root)
                self.assertEqual(external.read_bytes(),b'PRIVATE_EXTERNAL')
