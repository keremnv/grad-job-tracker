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
from jobwatch.applications import COLUMNS

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
            s.write_text(json.dumps(state()));a.write_text(','.join(COLUMNS)+'\n');v.write_text('{"schema":1,"postings":{}}')
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


class WeeklyDecisionTests(unittest.TestCase):
    def application(self, key, due='', stage='applied', **extra):
        from jobwatch.applications import make_application
        return make_application({'job_key': 'manual:' + key, 'company': key, 'role': 'Role',
                                 'url': 'https://example.org/' + key, 'stage': stage,
                                 'next_action': 'Review ' + key, 'next_action_date': due, **extra})

    def test_decision_section_order_dates_sorted_terminal_actions_excluded(self):
        rows = [self.application('tomorrow', '2026-10-23'), self.application('old', '2026-10-10'),
                self.application('today', '2026-10-22'), self.application('yesterday', '2026-10-21'),
                self.application('edge', '2026-10-29'), self.application('later', '2026-10-30'),
                self.application('unknown'), self.application('rejected', '2026-10-10', 'rejected'),
                self.application('withdrawn', '2026-10-10', 'withdrawn')]
        out = weekly(state(), rows, {}, '2026-10-22T12:00:00Z')
        headings = ['Overdue owner-entered actions', 'Actions due within seven days',
                    'Source-verified application deadlines requiring attention', 'Applications by stage',
                    'New relevant vacancies', 'Stale feeds and collection failures']
        positions = [out.index('## ' + h) for h in headings]
        self.assertEqual(positions, sorted(positions))
        overdue = out[positions[0]:positions[1]]
        upcoming = out[positions[1]:positions[2]]
        self.assertLess(overdue.index('Review old'), overdue.index('Review yesterday'))
        self.assertLess(upcoming.index('Review today'), upcoming.index('Review tomorrow'))
        self.assertLess(upcoming.index('Review tomorrow'), upcoming.index('Review edge'))
        for action in ('Review rejected', 'Review withdrawn'):
            self.assertNotIn(action, out)
        for action in ('Review later', 'Review unknown'):
            self.assertNotIn(action, overdue + upcoming)
            self.assertIn(action, out)

    def test_owner_calendar_uses_london_date_at_bst_midnight(self):
        rows = [self.application('past', '2026-10-21'), self.application('due', '2026-10-22')]
        out = weekly(state(), rows, {}, '2026-10-21T23:30:00Z')
        overdue, upcoming = out.split('## Actions due within seven days')
        self.assertIn('Review past', overdue)
        self.assertNotIn('Review due', overdue)
        self.assertIn('Review due', upcoming)

    def test_verified_deadlines_deduplicate_leads_and_skip_unverified_feed_dates(self):
        s = state(); key = 'lever:example:one'; posting = s['postings'][key]
        posting['feed_status'] = 'not_seen'
        d = {'value': '2026-10-25', 'wording': 'Apply by 25 October 2026.',
             'url': posting['url'], 'verified_at': '2026-10-17T12:00:00Z'}
        posting['deadlines'] = [d, {'value': '2026-10-24', 'wording': 'FEED ONLY', 'url': posting['url']}]
        verification = {key: {'status': 'unknown', 'deadlines': [d, dict(d)]}}
        leads = {'vacancies': [{'company': posting['company'], 'title': posting['title'], 'url': posting['url'],
                  'official': True, 'checked_at': NOW, 'status': 'listed', 'deadline': d},
                 {'company': 'Manual only', 'title': 'Manual Vacancy', 'url': 'https://example.org/manual',
                  'official': True, 'checked_at': NOW, 'status': 'listed',
                  'deadline': {'value': '2026-10-26', 'wording': 'MANUAL DEADLINE'}}]}
        out = weekly(s, [], verification, '2026-10-22T12:00:00Z', leads)
        self.assertEqual(out.count('Apply by 25 October 2026.'), 1)
        self.assertEqual(out.count('MANUAL DEADLINE'), 1)
        self.assertNotIn('FEED ONLY', out)
        self.assertIn('expiry time/timezone unknown', out)
        self.assertIn('never proves closure', out)
        # Rejected/applied applications require no new application-deadline action.
        for stage in ('applied', 'assessment', 'interview', 'offer', 'rejected', 'withdrawn'):
            application = self.application('one', stage=stage)
            application['job_key'] = key; application['url'] = posting['url']
            self.assertNotIn('Apply by 25 October 2026.', weekly(s, [application], verification, '2026-10-22T12:00:00Z', leads))
        for stage in ('bookmarked', 'preparing'):
            application = self.application('one', stage=stage); application['job_key'] = key
            self.assertIn('Apply by 25 October 2026.', weekly(s, [application], verification, '2026-10-22T12:00:00Z'))

    def test_deadlines_sorted_passed_recheck_far_omitted_invalid_verification_omitted(self):
        s = state(); key = 'lever:example:one'; url = s['postings'][key]['url']
        def deadline(value, wording, checked=NOW):
            return {'value': value, 'wording': wording, 'url': url, 'verified_at': checked}
        verification = {key: {'deadlines': [deadline('2026-10-29', 'LATER'), deadline('2026-10-20', 'PASSED'),
                    deadline('2026-12-01', 'FAR'), deadline('2026-10-23', 'UNVERIFIED', ''),
                    deadline('2026-10-23', 'FUTURE CHECK', '2027-01-01T00:00:00Z'),
                    deadline('2026-10-22T17:00:00+01:00', 'TIMED')]}}
        out = weekly(s, [], verification, '2026-10-22T12:00:00Z')
        self.assertLess(out.index('PASSED'), out.index('TIMED'))
        self.assertLess(out.index('TIMED'), out.index('LATER'))
        for value in ('FAR', 'UNVERIFIED', 'FUTURE CHECK'):
            self.assertNotIn(value, out)
        self.assertIn('2026-10-22T17:00:00+01:00', out)
        self.assertIn('application status unknown', out)

    def test_private_notes_never_in_reports_and_failures_escape(self):
        s = state()
        s['boards'] = {'lever:example': {'status': 'failed', 'last_success': NOW, 'error': '<script>failed</script>'}}
        s['last_scan'] = {'failures': ['<img>failure']}
        application = self.application('private', '2026-10-22', notes='SECRET NOTES', cv_version='SECRET CV')
        out = weekly(s, [application], {}, '2026-10-22T12:00:00Z')
        self.assertNotIn('SECRET', out)
        self.assertNotIn('<script>', out); self.assertNotIn('<img>', out)
        self.assertIn('STALE', out); self.assertIn('&lt;script&gt;', out)

    def test_weekly_symlink_output_cannot_escape_ignored_directory(self):
        with TemporaryDirectory() as d:
            root = Path(d); output = root/'.local-reports'; output.mkdir()
            (output/'escape').symlink_to(root, target_is_directory=True)
            ledger = root/'applications.csv'; ledger.write_text(','.join(COLUMNS)+'\n')
            with patch('sys.stderr', new=io.StringIO()):
                self.assertEqual(main(['weekly', '--applications', str(ledger),
                                       '--output', str(output/'escape'/'public.md')]), 2)
            self.assertFalse((root/'public.md').exists())
