"""Offline status/filter contracts; database behaviour is in tests/postgres."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest

from jobwatch.postgres import calendar_feed,reports

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
