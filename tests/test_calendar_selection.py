"""Offline status/filter contracts; database behaviour is in tests/postgres."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

from jobwatch import calendar
from jobwatch.postgres import calendar_feed,calendar_selection,reports

ROOT=Path(__file__).resolve().parents[1]


def record(identity,posting=None,lead=None):
    return {'identity':identity,'posting_key':posting,'lead_key':lead}


class SelectionFilter(unittest.TestCase):
    def rows(self,**modes):
        return [{'evidence_key':k.replace('_',':'),'mode':m} for k,m in modes.items()]

    def test_without_rows_matches_policy_exactly(self):
        data=[record('p:1','p:1'),record('p:1','p:1','l:1'),record('l:2',None,'l:2')]
        self.assertEqual(reports.select_deadlines(data,None,[]),data)
        self.assertEqual(reports.select_deadlines(data,{'l:1'},[]),[data[1]])
        self.assertEqual(reports.select_deadlines(data,set(),[]),[])

    def test_exclude_wins_for_whole_identity_include_widens_it(self):
        data=[record('p:1','p:1'),record('p:1','p:1','l:1'),record('l:2',None,'l:2')]
        self.assertEqual(reports.select_deadlines(data,None,self.rows(l_1='exclude')),[data[2]])
        self.assertEqual(reports.select_deadlines(data,set(),self.rows(l_1='include')),data[:2])
        self.assertEqual(reports.select_deadlines(data,set(),self.rows(l_1='include',p_1='exclude')),[])

    def test_url_inferred_link_is_excluded_through_its_posting_key(self):
        data=[record('p:1',None,'l:1')]
        self.assertEqual(reports.select_deadlines(data,None,self.rows(p_1='exclude')),[])


class FeedEffect(unittest.TestCase):
    def test_counts_the_whole_feed_and_flags_any_unlisted_change(self):
        a,b,c=(calendar.uid('deadline',k) for k in 'abc')
        before,after={'events':{a,b}},{'events':{a,c}}
        self.assertEqual(calendar_selection.feed_effect(before,after,{'b','c'}),{'deadline_events_before':2,'deadline_events_after':2,'added':1,'removed':1,'unlisted_events_changed':0})
        self.assertEqual(calendar_selection.feed_effect(before,after,{'c'})['unlisted_events_changed'],1)


class StatusFile(unittest.TestCase):
    def manifest(self):
        h='a'*64;t='2026-11-01T10:00:00Z'
        event=dict(feed='deadlines',active=True,sequence=3,aliases=[h],anchor=h,fingerprint=h,created_at=t,modified_at=t)
        return {'generated_at':t,'warnings':['w'],'files':{f:{'events':1} for f in calendar_feed.FILES},
                'events':{'u1':event,'u2':{**event,'active':False,'aliases':['b'*64]},'u3':{**event,'feed':'actions','aliases':['c'*64]}}}

    def test_only_active_deadline_hashes_are_recorded_and_failure_keeps_success(self):
        ok=calendar_feed.publication_status(None,now='2026-11-01T10:00:05Z',manifest=self.manifest())
        self.assertEqual(ok['published_deadlines'],{'a'*64:3})
        bad=calendar_feed.publication_status(ok,now='2026-11-01T11:00:05Z')
        self.assertEqual((bad['last_attempt_ok'],bad['last_success_at'],bad['last_attempt_at']),(False,'2026-11-01T10:00:05Z','2026-11-01T11:00:05Z'))

    def test_atomic_group_readable_file_and_unusable_input_is_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'status.json'
            self.assertIsNone(calendar_feed.read_status(path))
            old=os.umask(0o077)
            try:calendar_feed.write_status(path,calendar_feed.publication_status(None,now='2026-11-01T10:00:05Z',manifest=self.manifest()))
            finally:os.umask(old)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o640)
            self.assertEqual(calendar_feed.read_status(path)['events']['deadlines'],1)
            self.assertEqual([p.name for p in Path(d).iterdir()],['status.json'])
            path.write_text('{"schema":"other"}')
            self.assertIsNone(calendar_feed.read_status(path))
            link=Path(d)/'link';link.symlink_to(path)
            self.assertIsNone(calendar_feed.read_status(link))

    def test_publisher_status_failure_cannot_affect_publication(self):
        sys.path.insert(0,str(ROOT/'infra/aws/scripts'))
        spec=importlib.util.spec_from_file_location('calendar_publish',ROOT/'infra/aws/scripts/calendar_publish.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        module.record_status({'status':'/nonexistent/dir/status.json'},None,{'exit_code':2})
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'s.json'
            module.record_status({'status':str(path)},None,{'exit_code':2},now='2026-11-01T10:00:00Z')
            self.assertFalse(json.loads(path.read_text())['last_attempt_ok'])


class RolloutCheck(unittest.TestCase):
    def module(self):
        sys.path.insert(0,str(ROOT/'infra/aws/scripts'))
        spec=importlib.util.spec_from_file_location('calendar_selection_check',ROOT/'infra/aws/scripts/calendar_selection_check.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module

    def state(self,check):
        uid={'active':True,'sequence':2,'created_at':'t','fingerprint':'f'}
        return {'tables':{'owner_audit':{'count':96,'sha256':'a'}},'migrations':[{'version':v,'sha256':str(v)} for v in range(1,7)],'selection_rows':None,'selection_grants':[],
            'private_files':{'/etc/jobwatch/x':'h'},'units':{'jobwatch-postgresql.service':{'ActiveState':'active','UnitFileState':'enabled','MainPID':'626'}},
            'calendar':{'uids':{'u'*64:uid},'files':{'deadlines.ics':{'events':4,'sha256':'c'}},'warnings':0,'uploaded_matches':True}}

    def after(self,check):
        value=json.loads(json.dumps(self.state(check)))
        value['migrations'].append({'version':7,'sha256':'7'});value['selection_rows']=0
        value['selection_grants']=[list(g) for g in check.EXPECTED_GRANTS]
        return value

    def test_expected_rollout_is_preserved_and_each_regression_has_a_code(self):
        check=self.module()
        before=self.state(check)
        self.assertEqual(check.differences(before,self.after(check)),[])
        cases=[(lambda a:a['tables']['owner_audit'].update(count=97),'table_changed:owner_audit'),
            (lambda a:a['migrations'][0].update(sha256='x'),'migration_ledger_changed'),
            (lambda a:a['migrations'].pop(),'schema_not_at_0007'),
            (lambda a:a.update(selection_rows=1),'selection_rows_present'),
            (lambda a:a['selection_grants'].pop(),'selection_grants_differ'),
            (lambda a:a['private_files'].clear(),'private_files_changed'),
            (lambda a:a['units']['jobwatch-postgresql.service'].update(MainPID='1'),'postgresql_restarted'),
            (lambda a:a['units']['jobwatch-postgresql.service'].update(ActiveState='failed'),'unit_state_changed:jobwatch-postgresql.service'),
            (lambda a:a['calendar']['uids']['u'*64].update(sequence=3),'calendar_uid_changed:'+'u'*12),
            (lambda a:a['calendar']['uids'].update({'v'*64:{}}),'calendar_uid_added'),
            (lambda a:a['calendar']['files']['deadlines.ics'].update(sha256='z'),'calendar_files_changed'),
            (lambda a:a['calendar'].update(warnings=1),'calendar_publication_unhealthy')]
        for change,code in cases:
            after=self.after(check);change(after)
            self.assertIn(code,check.differences(before,after))


class Units(unittest.TestCase):
    def test_status_directory_is_group_scoped_and_survives_oneshot_exit(self):
        unit=(ROOT/'infra/aws/integrations/systemd/jobwatch-calendar.service').read_text()
        self.assertIn('RuntimeDirectory=jobwatch-calendar-status\nRuntimeDirectoryMode=0750\nRuntimeDirectoryPreserve=yes',unit)
        self.assertIn('StateDirectoryMode=0700',unit)
        read=(ROOT/'infra/aws/integrations/systemd/jobwatch-agent-read.service').read_text()
        owner=(ROOT/'infra/aws/integrations/systemd/jobwatch-agent-owner.service').read_text()
        for text in (read,owner):
            self.assertIn('SupplementaryGroups=jobwatch-db',text)
            self.assertIn('--calendar-policy /etc/jobwatch/calendar-deadlines.sql',text)
        self.assertIn('--calendar-status /run/jobwatch-calendar-status/status.json',read)
        self.assertNotIn('--calendar-status',owner)


if __name__=='__main__':unittest.main()
