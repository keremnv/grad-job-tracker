import csv
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from jobwatch.cli import main, load_verifications, scan
from jobwatch.overview import weekly, requirements, description_hash, read_applications
from jobwatch.feeds import Posting
from jobwatch.state import merge

NOW='2026-10-17T12:00:00Z'


def state():
    p=Posting('Example','lever','example','one','Graduate Software Engineer','UK','https://jobs.lever.co/example/one',description='Python and Java. Distributed systems.')
    return merge({'schema':1,'postings':{}},{'lever:example':[p]},NOW)[0]


class OverviewTests(unittest.TestCase):
    def test_weekly_seven_day_window_owner_actions_escaping_no_notes(self):
        s=state()
        rows=[{'company':'<b>Owner company</b>','role':'role','stage':'applied','next_action':'Review [x](bad)','next_action_date':'2026-10-18','notes':'PRIVATE NOTE','cv_version':'PRIVATE CV'}]
        out=weekly(s,rows,{},'2026-10-20T12:00:00Z')
        self.assertIn('within seven days (1)',out);self.assertIn('owner-stated date 2026-10-18',out)
        self.assertNotIn('PRIVATE',out);self.assertNotIn('<b>',out)
        self.assertIn('within seven days (0)',weekly(s,rows,{},'2026-10-25T12:00:00Z'))
        self.assertEqual(rows[0]['stage'],'applied')

    def test_only_checked_matching_description_hash_can_supply_skill_counts(self):
        s=state();key='lever:example:one';row=s['postings'][key]
        self.assertIn('samples: 0',requirements(s,{},NOW))
        evidence={key:{'description_verified_at':NOW,'description_sha256':description_hash(row['description']),'description_url':row['url']}}
        out=requirements(s,evidence,NOW)
        self.assertIn('samples: 1',out);self.assertIn('**Python**: 1/1',out);self.assertIn(row['url'],out)
        row['description']='Changed description says Python'
        self.assertIn('samples: 0',requirements(s,evidence,NOW))

    def test_skill_mentions_do_not_count_english_go_and_include_cpp(self):
        s=state();key='lever:example:one';row=s['postings'][key]
        row['description']='We go the extra mile. Experience with C++ and/or Python in Linux; TypeScript, PyTorch, Git and LLMs.'
        evidence={key:{'description_verified_at':NOW,'description_sha256':description_hash(row['description']),'description_url':row['url']}}
        out=requirements(s,evidence,NOW)
        self.assertNotIn('**Go**',out)
        for skill in ('C++','Python','Linux','TypeScript','PyTorch','Git','LLMs'):
            self.assertIn(f'**{skill}**: 1/1',out)
        row['description']='You will work in Go, TypeScript with React, and Postgres.'
        evidence[key]['description_sha256']=description_hash(row['description'])
        self.assertIn('**Go**: 1/1',requirements(s,evidence,NOW))

    def test_manual_only_rows_and_ledger_bytes_preserved(self):
        with TemporaryDirectory() as d:
            d=Path(d);p=d/'applications.csv'
            p.write_bytes(b'job_key,company,role,url,stage,date_applied,cv_version,next_action,next_action_date,notes\r\nmanual:one,Private employer,Role,https://example.org,interview,,,Prepare,2026-10-20,private\r\n')
            before=p.read_bytes();rows=read_applications(p)
            self.assertIn('interview: 1',weekly(state(),rows,{},NOW));self.assertEqual(before,p.read_bytes())

    def test_private_weekly_command_is_local_and_cannot_overwrite_ledger(self):
        with TemporaryDirectory() as d:
            d=Path(d);s=d/'jobs.json';a=d/'applications.csv';v=d/'verifications.json';out=d/'.local-reports'/'weekly.md'
            s.write_text(json.dumps(state()));a.write_text('job_key,company,role,url,stage,next_action,next_action_date,notes\n');v.write_text('{"schema":1,"postings":{}}')
            before=a.read_bytes()
            args=['weekly','--state',str(s),'--applications',str(a),'--verifications',str(v)]
            self.assertEqual(main(args+['--output',str(out)]),0);self.assertTrue(out.exists());self.assertEqual(out.stat().st_mode&0o777,0o600)
            self.assertEqual(main(args+['--output',str(a)]),2);self.assertEqual(a.read_bytes(),before)
            self.assertEqual(main(args+['--output',str(d/'public.md')]),2)

    def test_scan_cannot_write_to_manual_ledger_or_state_as_report(self):
        with TemporaryDirectory() as d:
            d=Path(d)
            for state_path,report_path in ((d/'jobs.json',d/'applications.csv'),(d/'applications.csv',d/'report.md'),(d/'jobs.json',d/'jobs.json')):
                with self.assertRaises(ValueError):scan(d/'config.json',state_path,report_path)

    def test_verification_requires_evidence_and_timezone(self):
        with TemporaryDirectory() as d:
            p=Path(d)/'v.json';p.write_text(json.dumps({'schema':1,'postings':{'x':{'status':'open_verified'}}}))
            with self.assertRaises(ValueError):load_verifications(p)
